"""
Agent 记忆库 (专用 agent_db) — 会话与消息持久化 + 项目管理
- 入参: conversation_id / BaseMessage 等业务对象
- 方法: 连接池管理、建表、会话 CRUD、消息存取、项目管理
- 出参: bool / dict / List[BaseMessage]

设计要点 (完全模仿 data/business_db.py 的成熟模式):
  1. 独立的 _agent_pool (ThreadedConnectionPool), 与业务库完全隔离
  2. 懒初始化 + 不可用优雅降级: agent_db 未创建/不可达时, 所有函数返回 None/空/False
  3. 上层 memory.py 检测到不可用后, 自动回退到内存 dict (学习零阻塞)

为什么独立模块而不复用 business_db.py:
  - 业务库存放具体业务数据 (由运行环境决定, 高频读写)
  - agent_db 存放对话历史 (低频但需持久化), 两者职责完全不同
  - 配置上 config.py 已预留 agent_db_* 字段, 此处对接

消息持久化映射 (JSONB 存 tool_calls, 下轮可完整还原):
  ┌──────────────┬────────┬─────────┬───────────────┬──────────────┬──────┐
  │ 消息类型      │ role   │ content │ tool_calls    │ tool_call_id │ name │
  ├──────────────┼────────┼─────────┼───────────────┼──────────────┼──────┤
  │ HumanMessage │ human  │   ✓     │      -        │      -       │  -   │
  │ AIMessage    │ ai     │   ✓     │   ✓(JSONB)    │      -       │  -   │
  │ ToolMessage  │ tool   │   ✓     │      -        │      ✓       │  ✓   │
  │ SystemMessage│ system │   ✓     │      -        │      -       │  -   │
  └──────────────┴────────┴─────────┴───────────────┴──────────────┴──────┘

新增: 项目级会话管理
  - project 表: 项目元数据 (user_id + name + description)
  - conversation 表新增 project_id 外键
  - 会话 CRUD: update_conversation / delete_conversation
  - 项目 CRUD: create_project / list_projects / update_project / delete_project
"""
import json
import uuid
import logging
from contextlib import contextmanager
from typing import Optional, Dict, Any, List

from langchain_core.messages import (
    BaseMessage,
    HumanMessage,
    AIMessage,
    ToolMessage,
    SystemMessage,
)

from backend.config import settings

logger = logging.getLogger(__name__)

# 延迟导入 psycopg2, 避免未安装时整个模块加载失败
_psycopg2 = None
_pool = None


def _get_psycopg2():
    global _psycopg2
    if _psycopg2 is None:
        try:
            import psycopg2
            from psycopg2 import pool as _pool_mod
            from psycopg2.extras import Json
            _psycopg2 = (psycopg2, _pool_mod, Json)
        except ImportError:
            logger.warning("psycopg2 未安装, agent_db 功能不可用 (将降级为内存模式)")
            return None, None, None
    return _psycopg2


def _get_pool():
    """懒初始化 agent_db 连接池"""
    global _pool
    if _pool is not None:
        return _pool
    psycopg2, pool_mod, _ = _get_psycopg2()
    if psycopg2 is None:
        return None
    try:
        _pool = pool_mod.ThreadedConnectionPool(
            minconn=1,
            maxconn=3,
            host=settings.agent_db_host,
            port=settings.agent_db_port,
            dbname=settings.agent_db_name,
            user=settings.agent_db_user,
            password=settings.agent_db_password,
        )
        logger.info(f"agent_db 连接池已创建: {settings.agent_db_host}:{settings.agent_db_port}/{settings.agent_db_name}")
        return _pool
    except Exception as e:
        logger.warning(f"agent_db 连接失败, 将使用内存模式: {e}")
        return None


@contextmanager
def get_conn():
    """获取 agent_db 连接 (上下文管理器, 不可用时 yield None)"""
    pool = _get_pool()
    if pool is None:
        yield None
        return
    conn = None
    try:
        conn = pool.getconn()
        yield conn
    finally:
        if conn is not None and pool is not None:
            pool.putconn(conn)


def agent_db_available() -> bool:
    """检查 agent_db 是否可用 (含建表前的连通性测试)"""
    pool = _get_pool()
    if pool is None:
        return False
    try:
        with get_conn() as conn:
            if conn is None:
                return False
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
            return True
    except Exception:
        return False


# ==================== Schema 初始化 ====================

def init_schema() -> bool:
    """
    启动时建表 (IF NOT EXISTS, 幂等)。
    agent_db 不可用时返回 False, 仅 warning 不崩溃。
    """
    _, _, _ = _get_psycopg2()
    with get_conn() as conn:
        if conn is None:
            logger.warning("agent_db 不可用, schema 未初始化 (将使用内存模式)")
            return False
        try:
            with conn.cursor() as cur:
                # ★ 项目表 (新增)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS project (
                        id          TEXT PRIMARY KEY,
                        user_id     TEXT NOT NULL,
                        name        TEXT NOT NULL,
                        description TEXT DEFAULT '',
                        created_at  TIMESTAMPTZ DEFAULT now(),
                        updated_at  TIMESTAMPTZ DEFAULT now()
                    )
                """)
                cur.execute("""
                    CREATE INDEX IF NOT EXISTS idx_project_user
                    ON project(user_id)
                """)
                # ★ v2.4 工作区改造: project 表加 folder_path (本地文件夹关联)
                cur.execute("ALTER TABLE project ADD COLUMN IF NOT EXISTS folder_path TEXT")
                cur.execute("""
                    CREATE UNIQUE INDEX IF NOT EXISTS idx_project_folder_path
                    ON project(folder_path) WHERE folder_path IS NOT NULL
                """)

                # 会话表 (新增 project_id 外键)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS conversation (
                        id               TEXT PRIMARY KEY,
                        user_id          TEXT NOT NULL,
                        project_id       TEXT REFERENCES project(id) ON DELETE SET NULL,
                        title            TEXT,
                        trace_event_logs JSONB DEFAULT '[]'::jsonb,
                        trace_prompt_logs JSONB DEFAULT '[]'::jsonb,
                        trace_ws_logs    JSONB DEFAULT '[]'::jsonb,
                        created_at       TIMESTAMPTZ DEFAULT now(),
                        updated_at       TIMESTAMPTZ DEFAULT now()
                    )
                """)
                # ★ 兼容旧表: 若 conversation 表已存在但无 project_id / trace 列, 尝试添加
                try:
                    cur.execute("""
                        ALTER TABLE conversation ADD COLUMN IF NOT EXISTS
                        project_id TEXT REFERENCES project(id) ON DELETE SET NULL
                    """)
                    cur.execute("ALTER TABLE conversation ADD COLUMN IF NOT EXISTS trace_event_logs JSONB DEFAULT '[]'::jsonb")
                    cur.execute("ALTER TABLE conversation ADD COLUMN IF NOT EXISTS trace_prompt_logs JSONB DEFAULT '[]'::jsonb")
                    cur.execute("ALTER TABLE conversation ADD COLUMN IF NOT EXISTS trace_ws_logs JSONB DEFAULT '[]'::jsonb")
                    # ★ v2.5 消息分支: 记录当前展示的分支叶子节点 (fork/regenerate 时更新)
                    cur.execute("ALTER TABLE conversation ADD COLUMN IF NOT EXISTS active_leaf_node UUID")
                except Exception:
                    pass  # 列已存在或权限不足, 忽略

                # ★ 会话状态追踪 (active/completed/stopped)
                try:
                    cur.execute(
                        "ALTER TABLE conversation ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'active'"
                    )
                except Exception:
                    pass

                # 消息表 — tool_calls 用 JSONB 完整保留工具调用结构
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS message (
                        id              BIGSERIAL PRIMARY KEY,
                        conversation_id TEXT NOT NULL REFERENCES conversation(id) ON DELETE CASCADE,
                        role            TEXT NOT NULL,
                        content         TEXT,
                        thinking_content TEXT,
                        tool_calls      JSONB,
                        tool_call_id    TEXT,
                        name            TEXT,
                        created_at      TIMESTAMPTZ DEFAULT now()
                    )
                """)
                cur.execute("ALTER TABLE message ADD COLUMN IF NOT EXISTS thinking_content TEXT")
                # ★ v2.5 消息分支 (Git-like DAG): 加 node_id/parent_id/is_active 字段
                # node_id: 消息节点唯一标识 (UUID, 逻辑主键, 分支引用用)
                # parent_id: 父节点 (前驱消息的 node_id, 首条为 NULL)
                # is_active: 软删除/分支隐藏标志 (TRUE=当前分支可见)
                cur.execute("ALTER TABLE message ADD COLUMN IF NOT EXISTS node_id UUID DEFAULT gen_random_uuid()")
                cur.execute("ALTER TABLE message ADD COLUMN IF NOT EXISTS parent_id UUID")
                cur.execute("ALTER TABLE message ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT TRUE")
                # ★ 聊天图片: AI 消息内嵌图片 URL 列表 (chat_image action 产物), 刷新会话后回填到聊天窗口
                cur.execute("ALTER TABLE message ADD COLUMN IF NOT EXISTS images JSONB")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_msg_node ON message(node_id)")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_msg_parent ON message(parent_id)")
                cur.execute("""
                    CREATE INDEX IF NOT EXISTS idx_msg_conv
                    ON message(conversation_id, id)
                """)
                # 短期记忆 - 会话摘要表 (每个会话至多一条最新摘要)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS conversation_summary (
                        conversation_id  TEXT PRIMARY KEY REFERENCES conversation(id) ON DELETE CASCADE,
                        summary          TEXT,
                        summarized_upto  BIGINT,
                        updated_at       TIMESTAMPTZ DEFAULT now()
                    )
                """)
                # ★ v2.5 多级压缩: 摘要表加 compress_level 列 (0=未压缩, 1=drop工具消息, 2=LLM摘要)
                cur.execute("""
                    ALTER TABLE conversation_summary
                    ADD COLUMN IF NOT EXISTS compress_level INT DEFAULT 0
                """)
                # 长期记忆 - 用户画像表 (跨会话, 按 user_id 隔离)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS user_memory (
                        id         BIGSERIAL PRIMARY KEY,
                        user_id    TEXT NOT NULL,
                        key        TEXT NOT NULL,
                        value      TEXT,
                        category   TEXT DEFAULT 'preference',
                        created_at TIMESTAMPTZ DEFAULT now(),
                        UNIQUE(user_id, key)
                    )
                """)
                cur.execute("""
                    CREATE INDEX IF NOT EXISTS idx_um_user
                    ON user_memory(user_id)
                """)

                # ★ AI 任务表 (P3 任务持久化): 追踪重工具的执行状态/进度/IO, 支持断点续算
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS ai_task (
                        id              BIGSERIAL PRIMARY KEY,
                        conversation_id TEXT REFERENCES conversation(id) ON DELETE CASCADE,
                        user_id         TEXT,
                        task_type       TEXT NOT NULL,        -- segment / detect_change / rule_overlay / report / preprocess
                        tool_name       TEXT,                 -- 触发任务的具体工具名
                        status          TEXT NOT NULL DEFAULT 'pending',  -- pending / running / done / failed / cancelled
                        input           JSONB,                -- 入参 (image_paths, classes, ...)
                        output          JSONB,                -- 出参 (result paths, stats)
                        progress        INTEGER DEFAULT 0,    -- 0~100
                        error           TEXT,
                        started_at      TIMESTAMPTZ,
                        finished_at     TIMESTAMPTZ,
                        created_at      TIMESTAMPTZ DEFAULT now()
                    )
                """)
                cur.execute("""
                    CREATE INDEX IF NOT EXISTS idx_ai_task_conv
                    ON ai_task(conversation_id, id)
                """)
                cur.execute("""
                    CREATE INDEX IF NOT EXISTS idx_ai_task_status
                    ON ai_task(status, created_at DESC)
                """)
                # ★ 多 Agent 第一阶段: 复用 ai_task 作为专业 worker 状态源
                cur.execute("ALTER TABLE ai_task ADD COLUMN IF NOT EXISTS parent_task_id BIGINT")
                cur.execute("ALTER TABLE ai_task ADD COLUMN IF NOT EXISTS agent_role TEXT")
                cur.execute("ALTER TABLE ai_task ADD COLUMN IF NOT EXISTS goal TEXT")
                cur.execute("ALTER TABLE ai_task ADD COLUMN IF NOT EXISTS verification JSONB")
                cur.execute("""
                    CREATE INDEX IF NOT EXISTS idx_ai_task_agent_role
                    ON ai_task(agent_role, status, created_at DESC)
                """)

                # ★ 任务日志表 (P3/P8): ai_task 执行过程的 info/warning/error 日志
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS task_log (
                        id         BIGSERIAL PRIMARY KEY,
                        task_id    BIGINT REFERENCES ai_task(id) ON DELETE CASCADE,
                        level      TEXT NOT NULL DEFAULT 'info',  -- info / warning / error
                        message    TEXT NOT NULL,
                        elapsed_ms INTEGER,                       -- 该步骤耗时 (可选)
                        created_at TIMESTAMPTZ DEFAULT now()
                    )
                """)
                cur.execute("""
                    CREATE INDEX IF NOT EXISTS idx_task_log_task
                    ON task_log(task_id, id)
                """)
            conn.commit()
            logger.info("agent_db schema 初始化完成 (project / conversation[status] / message / conversation_summary / user_memory / ai_task / task_log)")
            # ★ v2.5 消息分支: 迁移历史数据 (补 node_id/parent_id + 设 active_leaf_node)
            # 幂等: 只处理 node_id IS NULL 或 active_leaf_node IS NULL 的会话
            try:
                _migrate_message_nodes()
            except Exception as e:
                logger.warning(f"消息节点迁移 (非阻塞): {e}")
            return True
        except Exception as e:
            conn.rollback()
            logger.error(f"agent_db schema 初始化失败: {e}")
            return False


def _migrate_message_nodes():
    """
    ★ v2.5 历史数据迁移: 把线性消息链转为 DAG 单链 (补 node_id/parent_id)。

    幂等: 只处理 node_id IS NULL 的消息 + active_leaf_node IS NULL 的会话。
    策略:
      1. 给 node_id 为 NULL 的消息生成 UUID
      2. 按 (conversation_id, id ASC) 设 parent_id = 前一条同会话消息的 node_id
         (首条消息 parent_id = NULL, 即根节点)
      3. 给 active_leaf_node 为 NULL 的会话设为最后一条 active 消息的 node_id
    """
    with get_conn() as conn:
        if conn is None:
            return
        with conn.cursor() as cur:
            # 1. 补 node_id (DEFAULT gen_random_uuid() 已自动生成, 只更新显式 NULL 的)
            cur.execute("UPDATE message SET node_id = gen_random_uuid() WHERE node_id IS NULL")
            # 2. 按 (conversation_id, id ASC) 用窗口函数 lag 设 parent_id
            cur.execute("""
                UPDATE message m SET parent_id = sub.prev_node_id
                FROM (
                    SELECT id,
                           LAG(node_id) OVER (PARTITION BY conversation_id ORDER BY id ASC) AS prev_node_id
                    FROM message
                ) sub
                WHERE m.id = sub.id AND m.parent_id IS NULL
            """)
            # 3. 设 active_leaf_node = 每个会话最后一条 active 消息的 node_id
            # ★ conversation 表主键是 id (不是 conversation_id), 关联子查询用 c2.id
            cur.execute("""
                UPDATE conversation c SET active_leaf_node = sub.last_node
                FROM (
                    SELECT c2.id,
                           (SELECT node_id FROM message
                            WHERE conversation_id = c2.id AND is_active = TRUE
                            ORDER BY id DESC LIMIT 1) AS last_node
                    FROM conversation c2
                    WHERE c2.active_leaf_node IS NULL
                ) sub
                WHERE c.id = sub.id AND c.active_leaf_node IS NULL
            """)
        conn.commit()
        logger.info("[Migrate] 消息节点迁移完成 (node_id/parent_id/active_leaf_node)")


def fork_from_node(conversation_id: str, parent_node_id: str) -> Optional[str]:
    """
    ★ v2.5 从指定消息节点开新分支。

    方法:
      1. 把同 parent 的"后续兄弟节点"标记为 is_active=FALSE (旧分支隐藏)
      2. 更新 conversation.active_leaf_node = parent_node_id (新分支从这开始)
      3. 返回 parent_node_id (作为新消息的 parent)

    入参:
      - conversation_id: 会话 id
      - parent_node_id: 分叉点 (新分支挂在这条消息之后)
    返回: parent_node_id (成功) 或 None (失败)
    """
    with get_conn() as conn:
        if conn is None:
            return None
        try:
            with conn.cursor() as cur:
                # 找到分叉点的 DB id, 用于定位"它之后的兄弟节点"
                cur.execute(
                    "SELECT id FROM message WHERE conversation_id = %s AND node_id = %s",
                    (conversation_id, parent_node_id),
                )
                row = cur.fetchone()
                if not row:
                    logger.warning(f"[Fork] 节点不存在: conv={conversation_id} node={parent_node_id}")
                    return None
                fork_db_id = row[0]

                # 把分叉点之后的 active 消息标记为 is_active=FALSE (旧分支隐藏)
                cur.execute(
                    """UPDATE message SET is_active = FALSE
                       WHERE conversation_id = %s AND id > %s AND is_active = TRUE""",
                    (conversation_id, fork_db_id),
                )
                hidden = cur.rowcount

                # 更新 active_leaf_node 为分叉点 (新分支从这开始)
                cur.execute(
                    "UPDATE conversation SET active_leaf_node = %s, updated_at = now() WHERE id = %s",
                    (parent_node_id, conversation_id),
                )
            conn.commit()
            logger.info(f"[Fork] conv={conversation_id} from node={parent_node_id} hidden={hidden}")
            return parent_node_id
        except Exception as e:
            conn.rollback()
            logger.error(f"[Fork] 失败 conv={conversation_id}: {e}")
            return None


def get_active_leaf_node(conversation_id: str) -> Optional[str]:
    """获取会话当前 active 分支的叶子节点 node_id (新消息挂在这之后)。"""
    with get_conn() as conn:
        if conn is None:
            return None
        try:
            with conn.cursor() as cur:
                # 优先用 conversation.active_leaf_node; 为空则取最后一条 active 消息
                cur.execute(
                    "SELECT active_leaf_node FROM conversation WHERE id = %s",
                    (conversation_id,),
                )
                row = cur.fetchone()
                if row and row[0]:
                    return str(row[0])
                # fallback: 最后一条 active 消息的 node_id
                cur.execute(
                    """SELECT node_id FROM message
                       WHERE conversation_id = %s AND is_active = TRUE
                       ORDER BY id DESC LIMIT 1""",
                    (conversation_id,),
                )
                row = cur.fetchone()
                return str(row[0]) if row else None
        except Exception as e:
            logger.error(f"获取 active_leaf_node 失败 conv={conversation_id}: {e}")
            return None


def soft_delete_branch_after(conversation_id: str, after_msg_id: int) -> int:
    """
    ★ v2.5 软删除: 把 checkpoint 之后的消息标记 is_active=FALSE (替代物理 delete_messages_after)。
    用于停止回滚场景 — 保留数据但隐藏, 旧分支可恢复。

    返回受影响行数。
    """
    with get_conn() as conn:
        if conn is None:
            return 0
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE message SET is_active = FALSE
                       WHERE conversation_id = %s AND id > %s AND is_active = TRUE""",
                    (conversation_id, after_msg_id),
                )
                affected = cur.rowcount
            conn.commit()
            return affected
        except Exception as e:
            conn.rollback()
            logger.error(f"软删除失败 conv={conversation_id}: {e}")
            return 0


# ==================== 项目 CRUD (新增) ====================

def create_project(user_id: str, name: str, description: str = "", folder_path: str = None) -> Optional[str]:
    """
    创建新项目。
    - folder_path (可选): 关联的本地文件夹绝对路径。传了则走 UPSERT 语义 (已有同路径则复用)
    返回: 项目 id (UUID), 失败返回 None
    """
    project_id = str(uuid.uuid4())
    with get_conn() as conn:
        if conn is None:
            return None
        try:
            with conn.cursor() as cur:
                # ★ folder_path 已存在 → 复用现有 project (UPSERT 语义)
                if folder_path:
                    cur.execute(
                        "SELECT id FROM project WHERE folder_path = %s",
                        (folder_path,),
                    )
                    existing = cur.fetchone()
                    if existing:
                        logger.info(f"[DB] 复用现有 project (folder_path={folder_path}): {existing[0]}")
                        return existing[0]
                cur.execute(
                    """INSERT INTO project (id, user_id, name, description, folder_path)
                       VALUES (%s, %s, %s, %s, %s)""",
                    (project_id, user_id, name, description, folder_path),
                )
            conn.commit()
            logger.info(f"项目创建成功: {project_id} name={name} folder={folder_path or '-'}")
            return project_id
        except Exception as e:
            conn.rollback()
            logger.error(f"创建项目失败 [user={user_id}, folder={folder_path}]: {e}")
            return None


def get_project_by_folder_path(folder_path: str) -> Optional[Dict[str, Any]]:
    """按 folder_path 查询 project (不存在返回 None)。"""
    if not folder_path:
        return None
    with get_conn() as conn:
        if conn is None:
            return None
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, user_id, name, folder_path FROM project WHERE folder_path = %s",
                    (folder_path,),
                )
                row = cur.fetchone()
                if row is None:
                    return None
                return {"id": row[0], "user_id": row[1], "name": row[2], "folder_path": row[3]}
        except Exception as e:
            logger.error(f"按 folder_path 查询项目失败 [{folder_path}]: {e}")
            return None


def list_projects(user_id: str) -> List[Dict[str, Any]]:
    """
    列出用户的所有项目 (按更新时间倒序), 含会话数量。
    """
    with get_conn() as conn:
        if conn is None:
            return []
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT p.id, p.name, p.description, p.created_at, p.updated_at, p.folder_path,
                              COUNT(c.id) AS conv_count
                       FROM project p
                       LEFT JOIN conversation c ON c.project_id = p.id
                       WHERE p.user_id = %s
                       GROUP BY p.id
                       ORDER BY p.updated_at DESC""",
                    (user_id,),
                )
                rows = cur.fetchall()
            return [
                {
                    "id": r[0],
                    "name": r[1],
                    "description": r[2] or "",
                    "created_at": r[3].isoformat() if r[3] else None,
                    "updated_at": r[4].isoformat() if r[4] else None,
                    "folder_path": r[5] or "",
                    "conversation_count": r[6],
                }
                for r in rows
            ]
        except Exception as e:
            logger.error(f"列出项目失败 [user={user_id}]: {e}")
            return []


def get_project(project_id: str) -> Optional[Dict[str, Any]]:
    """查询单个项目元数据"""
    with get_conn() as conn:
        if conn is None:
            return None
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, user_id, name, description, created_at, updated_at FROM project WHERE id = %s",
                    (project_id,),
                )
                row = cur.fetchone()
                if row is None:
                    return None
                return {
                    "id": row[0],
                    "user_id": row[1],
                    "name": row[2],
                    "description": row[3] or "",
                    "created_at": row[4],
                    "updated_at": row[5],
                }
        except Exception as e:
            logger.error(f"查询项目失败 [{project_id}]: {e}")
            return None


def update_project(project_id: str, name: str = None, description: str = None) -> bool:
    """
    更新项目名称和/或描述。
    至少传一个字段。
    """
    with get_conn() as conn:
        if conn is None:
            return False
        try:
            with conn.cursor() as cur:
                if name is not None:
                    cur.execute(
                        "UPDATE project SET name = %s, updated_at = now() WHERE id = %s",
                        (name, project_id),
                    )
                if description is not None:
                    cur.execute(
                        "UPDATE project SET description = %s, updated_at = now() WHERE id = %s",
                        (description, project_id),
                    )
            conn.commit()
            return True
        except Exception as e:
            conn.rollback()
            logger.error(f"更新项目失败 [{project_id}]: {e}")
            return False


def delete_project(project_id: str) -> bool:
    """
    删除项目 (会话的 project_id 会被 SET NULL, 不会级联删除会话)。
    """
    with get_conn() as conn:
        if conn is None:
            return False
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM project WHERE id = %s", (project_id,))
            conn.commit()
            return True
        except Exception as e:
            conn.rollback()
            logger.error(f"删除项目失败 [{project_id}]: {e}")
            return False


# ==================== 会话 CRUD ====================

def create_conversation(conversation_id: str, user_id: str, title: str = "",
                        project_id: str = None) -> bool:
    """
    新建会话记录。
    - project_id: 可选, 归属到某个项目
    - 若会话已存在, 补齐缺失的标题/项目归属, 避免前端先建壳会话后丢失绑定
    """
    with get_conn() as conn:
        if conn is None:
            return False
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO conversation (id, user_id, title, project_id)
                       VALUES (%s, %s, %s, %s)
                       ON CONFLICT (id) DO UPDATE
                       SET title = CASE
                           WHEN EXCLUDED.title IS NOT NULL AND EXCLUDED.title <> ''
                                AND (conversation.title IS NULL OR conversation.title = '' OR conversation.title = '(新对话)')
                           THEN EXCLUDED.title
                           ELSE conversation.title
                       END,
                           project_id = CASE
                               WHEN EXCLUDED.project_id IS NOT NULL AND EXCLUDED.project_id <> ''
                               THEN EXCLUDED.project_id
                               ELSE conversation.project_id
                           END,
                           updated_at = now()""",
                    (conversation_id, user_id, title, project_id),
                )
            conn.commit()
            return True
        except Exception as e:
            conn.rollback()
            logger.error(f"创建会话失败 [{conversation_id}]: {e}")
            return False


def get_conversation(conversation_id: str) -> Optional[Dict[str, Any]]:
    """查询会话元数据, 用于校验 user_id 归属"""
    with get_conn() as conn:
        if conn is None:
            return None
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, user_id, project_id, title, created_at, updated_at FROM conversation WHERE id = %s",
                    (conversation_id,),
                )
                row = cur.fetchone()
                if row is None:
                    return None
                return {
                    "id": row[0],
                    "user_id": row[1],
                    "project_id": row[2],
                    "title": row[3],
                    "created_at": row[4],
                    "updated_at": row[5],
                }
        except Exception as e:
            logger.error(f"查询会话失败 [{conversation_id}]: {e}")
            return None


def update_conversation(conversation_id: str, title: str = None,
                        project_id: str = None) -> bool:
    """
    更新会话: 重命名 和/或 移动到其他项目。
    - title: 新标题 (None 表示不修改)
    - project_id: 新项目 ID (None 表示不修改, 空字符串 "" 表示移出项目)
    """
    with get_conn() as conn:
        if conn is None:
            return False
        try:
            with conn.cursor() as cur:
                if title is not None:
                    cur.execute(
                        "UPDATE conversation SET title = %s, updated_at = now() WHERE id = %s",
                        (title, conversation_id),
                    )
                if project_id is not None:
                    # 空字符串视为 NULL (移出项目)
                    pid = project_id if project_id else None
                    cur.execute(
                        "UPDATE conversation SET project_id = %s, updated_at = now() WHERE id = %s",
                        (pid, conversation_id),
                    )
            conn.commit()
            return True
        except Exception as e:
            conn.rollback()
            logger.error(f"更新会话失败 [{conversation_id}]: {e}")
            return False


def _normalize_trace_items(items):
    if not isinstance(items, list):
        return []
    normalized = []
    for item in items:
        if isinstance(item, dict):
            normalized.append(item)
    return normalized


def get_conversation_trace(conversation_id: str) -> Dict[str, Any]:
    """读取会话绑定的 trace 快照。"""
    with get_conn() as conn:
        if conn is None:
            return {"eventLogs": [], "promptLogs": [], "wsLogs": []}
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT trace_event_logs, trace_prompt_logs, trace_ws_logs
                       FROM conversation WHERE id = %s""",
                    (conversation_id,),
                )
                row = cur.fetchone()
                if row is None:
                    return {"eventLogs": [], "promptLogs": [], "wsLogs": []}
                return {
                    "eventLogs": _normalize_trace_items(row[0]),
                    "promptLogs": _normalize_trace_items(row[1]),
                    "wsLogs": _normalize_trace_items(row[2]),
                }
        except Exception as e:
            logger.error(f"读取会话 trace 失败 [{conversation_id}]: {e}")
            return {"eventLogs": [], "promptLogs": [], "wsLogs": []}


def save_conversation_trace(
    conversation_id: str,
    event_logs: List[Dict[str, Any]] = None,
    prompt_logs: List[Dict[str, Any]] = None,
    ws_logs: List[Dict[str, Any]] = None,
) -> bool:
    """保存会话绑定的 trace 快照到 conversation 表。"""
    _, _, Json = _get_psycopg2()
    if Json is None:
        return False
    with get_conn() as conn:
        if conn is None:
            return False
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE conversation
                       SET trace_event_logs = %s,
                           trace_prompt_logs = %s,
                           trace_ws_logs = %s,
                           updated_at = now()
                       WHERE id = %s""",
                    (
                        Json(_normalize_trace_items(event_logs)),
                        Json(_normalize_trace_items(prompt_logs)),
                        Json(_normalize_trace_items(ws_logs)),
                        conversation_id,
                    ),
                )
                if cur.rowcount == 0:
                    conn.rollback()
                    logger.warning(f"保存会话 trace 失败, 会话不存在: {conversation_id}")
                    return False
            conn.commit()
            return True
        except Exception as e:
            conn.rollback()
            logger.error(f"保存会话 trace 失败 [{conversation_id}]: {e}")
            return False


def delete_conversation(conversation_id: str) -> bool:
    """
    删除会话 (CASCADE 删除关联的 message 和 conversation_summary)。
    """
    with get_conn() as conn:
        if conn is None:
            return False
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM conversation WHERE id = %s", (conversation_id,))
            conn.commit()
            logger.info(f"会话已删除: {conversation_id}")
            return True
        except Exception as e:
            conn.rollback()
            logger.error(f"删除会话失败 [{conversation_id}]: {e}")
            return False


# ==================== 消息存取 ====================

def _is_missing_thinking_column_error(exc: Exception) -> bool:
    text = str(exc)
    lower_text = text.lower()
    return (
        "thinking_content" in lower_text
        and (
            "不存在" in text
            or "does not exist" in lower_text
            or "undefined column" in lower_text
        )
    )


def _ensure_message_thinking_column() -> bool:
    with get_conn() as conn:
        if conn is None:
            return False
        try:
            with conn.cursor() as cur:
                cur.execute("ALTER TABLE message ADD COLUMN IF NOT EXISTS thinking_content TEXT")
            conn.commit()
            logger.info("[DB] 已确认 message.thinking_content 可用")
            return True
        except Exception as e:
            conn.rollback()
            logger.error(f"[DB] 补齐 message.thinking_content 失败: {e}")
            return False


def append_message(conversation_id: str, msg: BaseMessage,
                   parent_node_id: str = None) -> bool:
    """
    追加一条消息, 自动按消息类型拆字段。
    - AIMessage 的 tool_calls 用 JSONB 完整保存 (下轮 load_messages 可还原)
    - AIMessage 的 thinking_content 单独落库, 供前端刷新后重建思考轨迹
    - ★ v2.5: parent_node_id 指定父节点 (消息分支 DAG), 不传则自动取当前 active_leaf_node
    """
    _, _, Json = _get_psycopg2()
    if Json is None:
        return False

    role, tool_calls, tool_call_id, name, thinking_content = _unpack_message(msg)
    storage_content = _message_content_for_storage(msg)
    images = _message_images_for_storage(msg)

    # ★ v2.5: 未传 parent_node_id 时, 自动取会话当前 active_leaf_node
    if parent_node_id is None:
        parent_node_id = get_active_leaf_node(conversation_id)

    for attempt in range(2):
        with get_conn() as conn:
            if conn is None:
                return False
            try:
                with conn.cursor() as cur:
                    # ★ v2.5: 插入时带 parent_id, node_id 由 DEFAULT gen_random_uuid() 自动生成
                    cur.execute(
                        """INSERT INTO message (conversation_id, role, content, thinking_content, tool_calls, tool_call_id, name, parent_id, images)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                           RETURNING node_id""",
                        (
                            conversation_id,
                            role,
                            storage_content,
                            thinking_content,
                            Json(tool_calls) if tool_calls else None,
                            tool_call_id,
                            name,
                            parent_node_id,
                            Json(images) if images else None,
                        ),
                    )
                    new_node_id = cur.fetchone()[0]
                    # 更新会话的 updated_at + active_leaf_node (新消息成为叶子)
                    cur.execute(
                        "UPDATE conversation SET updated_at = now(), active_leaf_node = %s WHERE id = %s",
                        (str(new_node_id), conversation_id),
                    )
                conn.commit()
                return True
            except Exception as e:
                conn.rollback()
                if attempt == 0 and _is_missing_thinking_column_error(e) and _ensure_message_thinking_column():
                    continue
                logger.error(f"追加消息失败 [{conversation_id}, role={role}]: {e}")
                return False
    return False


def load_messages(conversation_id: str, limit: int = None) -> List[BaseMessage]:
    """
    加载会话的历史消息 (按时间正序), 还原完整 tool_calls 结构。
    - limit: 最近 N 条 (默认取 settings.history_load_limit)
    - ★ v2.5: 只加载 is_active=TRUE 的消息 (当前分支), 软删除的不返回
    - agent_db 不可用时返回空列表 (由上层 memory.py 决定是否降级)
    """
    if limit is None:
        limit = settings.history_load_limit

    with get_conn() as conn:
        if conn is None:
            return []
        try:
            with conn.cursor() as cur:
                # ★ v2.5: 加 is_active = TRUE 过滤 (只返回当前分支)
                cur.execute(
                    """SELECT role, content, tool_calls, tool_call_id, name, images
                       FROM (
                           SELECT role, content, tool_calls, tool_call_id, name, images, id
                           FROM message
                           WHERE conversation_id = %s AND (is_active = TRUE OR is_active IS NULL)
                           ORDER BY id DESC
                           LIMIT %s
                       ) t
                       ORDER BY id ASC""",
                    (conversation_id, limit),
                )
                rows = cur.fetchall()
            return [_pack_message(r) for r in rows]
        except Exception as e:
            logger.error(f"加载消息失败 [{conversation_id}]: {e}")
            return []


# ==================== 消息 ↔ 行 转换 (内部) ====================

def _unpack_message(msg: BaseMessage):
    """从 LangChain 消息对象拆出数据库字段 (role, tool_calls, tool_call_id, name, thinking_content)"""
    if isinstance(msg, HumanMessage):
        return "human", None, None, None, None
    if isinstance(msg, AIMessage):
        # additional_kwargs 中可能含 tool_calls (旧版 LangChain), 优先用 .tool_calls 属性
        tc = msg.tool_calls or msg.additional_kwargs.get("tool_calls")
        thinking_content = None
        if isinstance(getattr(msg, "additional_kwargs", None), dict):
            raw_thinking = msg.additional_kwargs.get("thinking_content")
            if raw_thinking is not None:
                thinking_content = _content_to_str(raw_thinking)
        return "ai", tc, None, None, thinking_content
    if isinstance(msg, ToolMessage):
        return "tool", None, msg.tool_call_id, getattr(msg, "name", None), None
    if isinstance(msg, SystemMessage):
        return "system", None, None, None, None
    # 兜底: 按 content 类型猜测
    return "unknown", None, None, None, None


def _message_content_for_storage(msg: BaseMessage) -> str:
    """
    入参:
      - msg: 待落库的 LangChain 消息对象。
    方法:
      - HumanMessage 若携带 display_content, 只保存用户可见原文。
      - 其他消息保存实际 content。
    出参:
      - str, message.content 字段的落库文本。
    """
    if isinstance(msg, HumanMessage) and isinstance(getattr(msg, "additional_kwargs", None), dict):
        display = msg.additional_kwargs.get("display_content")
        if display is not None:
            return _content_to_str(display)
    return _content_to_str(msg.content)


def _message_images_for_storage(msg: BaseMessage):
    """
    入参:
      - msg: 待落库的 LangChain 消息对象。
    方法:
      - 读取 additional_kwargs.images。
      - 仅接受 list, 避免把异常结构写入 JSONB。
    出参:
      - list | None, 可写入 message.images 的附件列表。
    """
    if not isinstance(getattr(msg, "additional_kwargs", None), dict):
        return None
    images = msg.additional_kwargs.get("images")
    return images if isinstance(images, list) and images else None


def _pack_message(row) -> BaseMessage:
    """数据库行 → LangChain 消息对象 (还原 tool_calls)"""
    if len(row) >= 6:
        role, content, tool_calls_raw, tool_call_id, name, images = row
    else:
        role, content, tool_calls_raw, tool_call_id, name = row
        images = None
    content_str = content or ""

    if role == "human":
        image_list = images if isinstance(images, list) else []
        return HumanMessage(
            content=_append_image_context_to_content(content_str, image_list),
            additional_kwargs={
                "display_content": content_str,
                "images": image_list,
            },
        )
    if role == "ai":
        tool_calls = _decode_tool_calls(tool_calls_raw)
        return AIMessage(content=content_str, tool_calls=tool_calls)
    if role == "tool":
        return ToolMessage(
            content=content_str,
            tool_call_id=tool_call_id or "",
            name=name or "",
        )
    if role == "system":
        return SystemMessage(content=content_str)
    # 兜底
    return HumanMessage(content=content_str)


def _append_image_context_to_content(content: str, images: list) -> str:
    """
    入参:
      - content: 数据库中保存的用户可见原文。
      - images: message.images 中保存的影像附件。
    方法:
      - 无附件时返回原文。
      - 有附件时追加本地路径上下文, 支持后续“继续处理刚才影像”。
    出参:
      - str, 加载给 LLM 的 HumanMessage 内容。
    """
    if not images:
        return content
    lines = ["", "[历史影像输入]"]
    for idx, item in enumerate(images, 1):
        if not isinstance(item, dict) or not item.get("path"):
            continue
        title = item.get("full_name") or item.get("name") or f"image_{idx}"
        lines.append(f"- image_{idx}: {title}")
        if item.get("full_name"):
            lines.append(f"  图层: {item['full_name']}")
        if item.get("name"):
            lines.append(f"  文件名: {item['name']}")
        lines.append(f"  本地路径: {item['path']}")
        lines.append(f"  来源: {'GeoServer 图层下载' if item.get('source') == 'geoserver' else '用户手动上传'}")
    return f"{content.rstrip()}\n" + "\n".join(lines)


def _decode_tool_calls(raw):
    """把 JSONB 反序列化为 LangChain tool_calls 列表 (兼容 dict / list 形态)"""
    if not raw:
        return []
    # psycopg2 已自动解析 JSONB 为 Python 对象
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            return []
    # 标准化: [{"name","args","id"}]
    if isinstance(raw, list):
        return [
            {
                "name": tc.get("name", ""),
                "args": tc.get("args", {}) or tc.get("arguments", {}),
                "id": tc.get("id"),
            }
            for tc in raw
        ]
    return []


def _content_to_str(content) -> str:
    """把消息 content 统一成字符串 (LangChain 允许 list[str|dict] 形态)"""
    if isinstance(content, str):
        return content
    try:
        return json.dumps(content, ensure_ascii=False)
    except Exception:
        return str(content)


# ==================== 短期记忆 - 会话摘要 ====================

def save_summary(conversation_id: str, summary: str, summarized_upto: int,
                compress_level: int = 0) -> bool:
    """
    保存/更新会话摘要 (每个会话至多一条, UPSERT)。
    - summarized_upto: 摘要覆盖到的最大 message.id
      → 上层据此删除已摘要的旧消息, 下次只加载此 id 之后的消息
    - compress_level: 压缩级别 (v2.5)
      0=未压缩, 1=drop工具消息(可逆), 2=LLM摘要(不可逆)
    """
    with get_conn() as conn:
        if conn is None:
            return False
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO conversation_summary
                       (conversation_id, summary, summarized_upto, compress_level, updated_at)
                       VALUES (%s, %s, %s, %s, now())
                       ON CONFLICT (conversation_id) DO UPDATE
                       SET summary = EXCLUDED.summary,
                           summarized_upto = EXCLUDED.summarized_upto,
                           compress_level = EXCLUDED.compress_level,
                           updated_at = now()""",
                    (conversation_id, summary, summarized_upto, compress_level),
                )
            conn.commit()
            return True
        except Exception as e:
            conn.rollback()
            logger.error(f"保存摘要失败 [{conversation_id}]: {e}")
            return False


def load_summary(conversation_id: str) -> Optional[Dict[str, Any]]:
    """加载会话的最新摘要。返回 {summary, summarized_upto, compress_level} 或 None"""
    with get_conn() as conn:
        if conn is None:
            return None
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT summary, summarized_upto, compress_level
                       FROM conversation_summary WHERE conversation_id = %s""",
                    (conversation_id,),
                )
                row = cur.fetchone()
                if row is None:
                    return None
                # compress_level 列可能不存在 (旧库未迁移), 兜底 0
                level = row[2] if len(row) > 2 and row[2] is not None else 0
                return {"summary": row[0], "summarized_upto": row[1], "compress_level": level}
        except Exception as e:
            logger.error(f"加载摘要失败 [{conversation_id}]: {e}")
            return None


def load_messages_since(conversation_id: str, since_id: int, limit: int = None) -> List[BaseMessage]:
    """
    加载 since_id 之后的消息 (用于摘要后只取近期消息)。
    - since_id: 通常是 summary.summarized_upto, 取 > 该 id 的消息
    - limit: 最近 N 条
    - ★ v2.5: 只加载 is_active=TRUE 的消息 (当前分支)
    """
    if limit is None:
        limit = settings.history_load_limit
    with get_conn() as conn:
        if conn is None:
            return []
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT role, content, tool_calls, tool_call_id, name, images
                       FROM (
                           SELECT role, content, tool_calls, tool_call_id, name, images, id
                           FROM message
                           WHERE conversation_id = %s AND id > %s
                             AND (is_active = TRUE OR is_active IS NULL)
                           ORDER BY id DESC
                           LIMIT %s
                       ) t
                       ORDER BY id ASC""",
                    (conversation_id, since_id or 0, limit),
                )
                rows = cur.fetchall()
            return [_pack_message(r) for r in rows]
        except Exception as e:
            logger.error(f"加载近期消息失败 [{conversation_id}, since={since_id}]: {e}")
            return []


def delete_messages_upto(conversation_id: str, upto_id: int) -> int:
    """
    删除已被摘要覆盖的旧消息 (id <= upto_id), 防止历史无限膨胀。
    返回删除条数。保留 ToolMessage 的配对完整性由上层保证 (upto_id 落在 AI 最终消息边界)。
    """
    if not upto_id:
        return 0
    with get_conn() as conn:
        if conn is None:
            return 0
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM message WHERE conversation_id = %s AND id <= %s",
                    (conversation_id, upto_id),
                )
                deleted = cur.rowcount
            conn.commit()
            return deleted
        except Exception as e:
            conn.rollback()
            logger.error(f"删除旧消息失败 [{conversation_id}, upto={upto_id}]: {e}")
            return 0


# ==================== 长期记忆 - 用户画像 ====================

def save_user_memory(user_id: str, key: str, value: str, category: str = "preference") -> bool:
    """
    写入/更新一条用户长期记忆 (同 user_id+key 覆盖, UPSERT)。
    - category: preference(偏好) / entity(实体) / fact(事实)
    """
    with get_conn() as conn:
        if conn is None:
            return False
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO user_memory (user_id, key, value, category)
                       VALUES (%s, %s, %s, %s)
                       ON CONFLICT (user_id, key) DO UPDATE
                       SET value = EXCLUDED.value,
                           category = EXCLUDED.category""",
                    (user_id, key, value, category),
                )
            conn.commit()
            return True
        except Exception as e:
            conn.rollback()
            logger.error(f"保存用户记忆失败 [user={user_id}, key={key}]: {e}")
            return False


def load_user_memory(user_id: str, category: str = None) -> List[Dict[str, Any]]:
    """
    加载用户的长期记忆。返回 [{key, value, category}, ...]
    - category=None: 全量加载 (学习项目记忆量小, 全量注入 system prompt)
    """
    with get_conn() as conn:
        if conn is None:
            return []
        try:
            with conn.cursor() as cur:
                if category:
                    cur.execute(
                        "SELECT key, value, category FROM user_memory WHERE user_id = %s AND category = %s ORDER BY id",
                        (user_id, category),
                    )
                else:
                    cur.execute(
                        "SELECT key, value, category FROM user_memory WHERE user_id = %s ORDER BY id",
                        (user_id,),
                    )
                rows = cur.fetchall()
            return [{"key": r[0], "value": r[1], "category": r[2]} for r in rows]
        except Exception as e:
            logger.error(f"加载用户记忆失败 [user={user_id}]: {e}")
            return []


def delete_user_memory(user_id: str, key: str) -> bool:
    """删除一条用户记忆"""
    with get_conn() as conn:
        if conn is None:
            return False
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM user_memory WHERE user_id = %s AND key = %s",
                    (user_id, key),
                )
            conn.commit()
            return True
        except Exception as e:
            conn.rollback()
            logger.error(f"删除用户记忆失败 [user={user_id}, key={key}]: {e}")
            return False


# ==================== 会话列表与历史加载 (供前端侧栏) ====================

def list_conversations(user_id: str, project_id: str = None, limit: int = 50) -> List[Dict[str, Any]]:
    """
    列出用户的所有会话 (按最后活动时间倒序), 含消息条数。
    - project_id: 可选, 按项目过滤
    - 供前端侧栏展示历史会话列表
    - LEFT JOIN message 计数: 即使没有消息也会显示 (新建的空会话)
    """
    with get_conn() as conn:
        if conn is None:
            return []
        try:
            with conn.cursor() as cur:
                if project_id:
                    cur.execute(
                        """SELECT c.id, c.user_id, c.project_id, c.title, c.created_at, c.updated_at,
                                  c.status, COUNT(m.id) AS msg_count
                           FROM conversation c
                           LEFT JOIN message m ON m.conversation_id = c.id
                               AND (m.is_active = TRUE OR m.is_active IS NULL)
                           WHERE c.user_id = %s AND c.project_id = %s
                           GROUP BY c.id
                           ORDER BY c.updated_at DESC
                           LIMIT %s""",
                        (user_id, project_id, limit),
                    )
                else:
                    cur.execute(
                        """SELECT c.id, c.user_id, c.project_id, c.title, c.created_at, c.updated_at,
                                  c.status, COUNT(m.id) AS msg_count
                           FROM conversation c
                           LEFT JOIN message m ON m.conversation_id = c.id
                               AND (m.is_active = TRUE OR m.is_active IS NULL)
                           WHERE c.user_id = %s
                           GROUP BY c.id
                           ORDER BY c.updated_at DESC
                           LIMIT %s""",
                        (user_id, limit),
                    )
                rows = cur.fetchall()
            return [
                {
                    "id": r[0],
                    "user_id": r[1],
                    "project_id": r[2],
                    "title": r[3] or "(无标题)",
                    "created_at": r[4].isoformat() if r[4] else None,
                    "updated_at": r[5].isoformat() if r[5] else None,
                    "status": r[6] or "active",
                    "message_count": r[7],
                }
                for r in rows
            ]
        except Exception as e:
            logger.error(f"列出会话失败 [user={user_id}]: {e}")
            return []


def get_conversation_messages(conversation_id: str, limit: int = 200) -> List[Dict[str, Any]]:
    """
    返回会话的全部消息 (按时间正序), 含 tool_calls 结构。
    - 供前端加载历史会话时重建聊天 UI
    - tool_calls 已从 JSONB 反序列化为 Python dict, 便于 JSON 序列化返回前端
    - thinking_content 单独返回, 供前端刷新后重建思考轨迹
    - limit 默认 200, 防止超大对话拖慢加载
    - ★ v2.5: 只返回 is_active=TRUE 的消息 (当前分支), 含 node_id/parent_id 供前端分支交互
    """
    for attempt in range(2):
        with get_conn() as conn:
            if conn is None:
                return []
            try:
                with conn.cursor() as cur:
                    # ★ v2.5: 加 is_active 过滤 + 返回 node_id/parent_id
                    cur.execute(
                        """SELECT id, role, content, thinking_content, tool_calls, tool_call_id, name,
                                  node_id, parent_id, images
                           FROM message
                           WHERE conversation_id = %s
                             AND (is_active = TRUE OR is_active IS NULL)
                           ORDER BY id ASC
                           LIMIT %s""",
                        (conversation_id, limit),
                    )
                    rows = cur.fetchall()
                result = []
                for r in rows:
                    msg_id, role, content, thinking_content, tc, tcid, name, node_id, parent_id, images = r
                    # tool_calls 已是 Python list (psycopg2 自动解析 JSONB)
                    tc_list = tc if isinstance(tc, list) else None
                    result.append({
                        "id": msg_id,
                        "role": role,
                        "content": content or "",
                        "thinking_content": thinking_content or "",
                        "tool_calls": tc_list,
                        "tool_call_id": tcid,
                        "name": name,
                        "node_id": str(node_id) if node_id else None,
                        "parent_id": str(parent_id) if parent_id else None,
                        "images": images if isinstance(images, list) else None,
                    })
                return result
            except Exception as e:
                conn.rollback()
                if attempt == 0 and _is_missing_thinking_column_error(e) and _ensure_message_thinking_column():
                    continue
                logger.error(f"加载会话消息失败 [{conversation_id}]: {e}")
                return []
    return []


def set_last_assistant_images(conversation_id: str, images: Optional[List[Dict[str, Any]]]) -> bool:
    """
    把本轮聊天图片列表写到该会话最后一条 assistant 消息的 images 列。

    入参:
        - conversation_id: 当前会话 ID。
        - images: chat_image action 收集的图片列表 [{url, caption}, ...]; 空列表或 None 跳过。
    方法:
        - 在 persist_turn(最终回复) 之后调用, 取该会话最后一条 role='ai' 消息 UPDATE images。
        - 此时最后一条 assistant 即本轮最终回复, 与前端 finishAssistant 归档的图片归属一致。
        - 无图片或无匹配消息时静默返回, 不影响主流程。
    出参:
        - True 写入成功; False DB 不可用 / 无匹配消息 / 空列表。
    """
    _, _, Json = _get_psycopg2()
    if Json is None or not images:
        return False
    with get_conn() as conn:
        if conn is None:
            return False
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE message SET images = %s
                       WHERE id = (
                           SELECT id FROM message
                           WHERE conversation_id = %s AND role = 'ai'
                             AND (is_active = TRUE OR is_active IS NULL)
                           ORDER BY id DESC LIMIT 1
                       )""",
                    (Json(images), conversation_id),
                )
            conn.commit()
            return True
        except Exception as e:
            conn.rollback()
            logger.error(f"写入聊天图片失败 [{conversation_id}]: {e}")
            return False


# ==================== 对话上下文管理: Checkpoint / 回滚 ====================

def get_last_message_id(conversation_id: str) -> Optional[int]:
    """
    获取会话最后一条消息的 id (BIGSERIAL)。
    用于 checkpoint: 记录本轮对话开始前的最后一条消息 id。
    ★ v2.5: 只算 is_active=TRUE 的消息 (软删除的不算, 否则 checkpoint 会指向已隐藏的消息)
    返回 None 表示会话无消息或 DB 不可用。
    """
    with get_conn() as conn:
        if conn is None:
            return None
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT id FROM message
                       WHERE conversation_id = %s AND (is_active = TRUE OR is_active IS NULL)
                       ORDER BY id DESC LIMIT 1""",
                    (conversation_id,),
                )
                row = cur.fetchone()
                return row[0] if row else None
        except Exception as e:
            logger.error(f"获取最后消息 id 失败 [{conversation_id}]: {e}")
            return None


def delete_messages_after(conversation_id: str, after_msg_id: int) -> int:
    """
    ★ v2.5 改为软删除: 把 id > after_msg_id 的消息标记 is_active=FALSE (替代物理 DELETE)。
    用于停止回滚: 隐藏本轮对话新增的消息, 还原到 checkpoint 状态。
    旧分支数据保留在 DB, 可通过 fork 恢复。
    返回受影响条数。
    """
    if after_msg_id is None:
        return 0
    with get_conn() as conn:
        if conn is None:
            return 0
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE message SET is_active = FALSE
                       WHERE conversation_id = %s AND id > %s AND (is_active = TRUE OR is_active IS NULL)""",
                    (conversation_id, after_msg_id),
                )
                affected = cur.rowcount
            conn.commit()
            logger.info(f"软删除回滚 [{conversation_id}]: {affected} 条消息隐藏 (id > {after_msg_id})")
            return affected
        except Exception as e:
            conn.rollback()
            logger.error(f"回滚删除失败 [{conversation_id}, after={after_msg_id}]: {e}")
            return 0


def update_conversation_status(conversation_id: str, status: str) -> bool:
    """
    更新会话状态。
    - status: 'active' | 'completed' | 'stopped' | 'background'
    """
    with get_conn() as conn:
        if conn is None:
            return False
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE conversation SET status = %s, updated_at = now() WHERE id = %s",
                    (status, conversation_id),
                )
            conn.commit()
            return True
        except Exception as e:
            conn.rollback()
            logger.error(f"更新会话状态失败 [{conversation_id}]: {e}")
            return False


# ==================== AI 任务持久化 (P3 任务调度) ====================
# 任务 = 一个对话轮次里 LLM 调用的某个"重工具"(samseg/rule/report/preprocess 类)
# 的执行记录, 用于追踪状态/进度/IO, 支持断点续算与历史重跑.

# 触发任务记录的工具分类白名单 (轻工具如 memory/skill/database 查询不开任务, 避免噪声)
HEAVY_TOOL_CATEGORIES = {"samseg", "rule", "report", "preprocess", "analysis"}


def create_task(
    conversation_id: str,
    task_type: str,
    tool_name: str,
    user_id: str = None,
    input_data: Dict[str, Any] = None,
    parent_task_id: int = None,
    agent_role: str = None,
    goal: str = None,
) -> Optional[int]:
    """
    创建一个 AI 任务记录 (status=pending)。
    入参:
      - conversation_id: 所属会话。
      - task_type: segment / detect_change / report / agent_worker 等任务类型。
      - tool_name: 触发任务的工具名或调度入口名。
      - user_id: 当前用户。
      - input_data: 入参 dict, 以 JSONB 存储。
      - parent_task_id: 可选父任务 ID, 用于 worker 关联主任务。
      - agent_role: 可选 worker 角色。
      - goal: 可选 worker 子任务目标。
    方法:
      - 插入 ai_task, 新增多 Agent 字段均为可选, 不影响旧调用。
    出参:
      - 新任务 id; 失败返回 None。
    """
    _, _, Json = _get_psycopg2()
    if Json is None:
        return None
    with get_conn() as conn:
        if conn is None:
            return None
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO ai_task (
                           conversation_id, user_id, task_type, tool_name, status, input,
                           parent_task_id, agent_role, goal
                       )
                       VALUES (%s, %s, %s, %s, 'pending', %s, %s, %s, %s)
                       RETURNING id""",
                    (conversation_id, user_id, task_type, tool_name,
                     Json(input_data) if input_data else None,
                     parent_task_id, agent_role, goal),
                )
                row = cur.fetchone()
            conn.commit()
            task_id = row[0] if row else None
            if task_id:
                logger.info(f"[Task] 创建任务 id={task_id} type={task_type} tool={tool_name} conv={conversation_id}")
            return task_id
        except Exception as e:
            conn.rollback()
            logger.error(f"创建任务失败 [conv={conversation_id}, type={task_type}]: {e}")
            return None


def update_task(
    task_id: int,
    status: str = None,
    progress: int = None,
    output: Dict[str, Any] = None,
    error: str = None,
    verification: Dict[str, Any] = None,
) -> bool:
    """
    更新任务状态/进度/输出/错误/验证结果。
    入参:
      - task_id: ai_task 主键。
      - status: pending / running / done / failed / cancelled。
      - progress: 0~100 进度。
      - output: 任务输出 JSON。
      - error: 失败原因。
      - verification: worker 或工具产物校验结果。
    方法:
      - 任一参数为 None 表示不修改该字段。
      - status 切到 running 时自动写 started_at, 切到 done/failed/cancelled 时写 finished_at。
    出参:
      - bool, True 表示更新成功。
    """
    _, _, Json = _get_psycopg2()
    if Json is None or task_id is None:
        return False
    sets = []
    params = []
    if status is not None:
        sets.append("status = %s")
        params.append(status)
        if status == "running":
            sets.append("started_at = COALESCE(started_at, now())")
        elif status in ("done", "failed", "cancelled"):
            sets.append("finished_at = now()")
    if progress is not None:
        sets.append("progress = %s")
        params.append(int(progress))
    if output is not None:
        sets.append("output = %s")
        params.append(Json(output))
    if error is not None:
        sets.append("error = %s")
        params.append(error)
    if verification is not None:
        sets.append("verification = %s")
        params.append(Json(verification))
    if not sets:
        return False
    params.append(task_id)
    with get_conn() as conn:
        if conn is None:
            return False
        try:
            with conn.cursor() as cur:
                cur.execute(
                    f"UPDATE ai_task SET {', '.join(sets)} WHERE id = %s",
                    params,
                )
            conn.commit()
            return True
        except Exception as e:
            conn.rollback()
            logger.error(f"更新任务失败 [task={task_id}]: {e}")
            return False


def append_task_log(
    task_id: int,
    message: str,
    level: str = "info",
    elapsed_ms: int = None,
) -> bool:
    """
    追加一条任务日志 (info/warning/error).
    """
    if task_id is None:
        return False
    with get_conn() as conn:
        if conn is None:
            return False
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO task_log (task_id, level, message, elapsed_ms)
                       VALUES (%s, %s, %s, %s)""",
                    (task_id, level, message, elapsed_ms),
                )
            conn.commit()
            return True
        except Exception as e:
            conn.rollback()
            logger.error(f"追加任务日志失败 [task={task_id}]: {e}")
            return False


def get_task(task_id: int) -> Optional[Dict[str, Any]]:
    """
    查询单个任务详情。
    入参:
      - task_id: ai_task 主键。
    方法:
      - 读取任务输入、输出、验证结果和时间戳。
      - 多 Agent 字段为空时仍按旧任务返回。
    出参:
      - dict 或 None。
    """
    with get_conn() as conn:
        if conn is None:
            return None
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT id, conversation_id, user_id, task_type, tool_name,
                              status, input, output, progress, error,
                              started_at, finished_at, created_at,
                              parent_task_id, agent_role, goal, verification
                       FROM ai_task WHERE id = %s""",
                    (task_id,),
                )
                row = cur.fetchone()
                if row is None:
                    return None
            return {
                "id": row[0],
                "conversation_id": row[1],
                "user_id": row[2],
                "task_type": row[3],
                "tool_name": row[4],
                "status": row[5],
                "input": row[6],
                "output": row[7],
                "progress": row[8],
                "error": row[9],
                "started_at": row[10].isoformat() if row[10] else None,
                "finished_at": row[11].isoformat() if row[11] else None,
                "created_at": row[12].isoformat() if row[12] else None,
                "parent_task_id": row[13],
                "agent_role": row[14],
                "goal": row[15],
                "verification": row[16],
            }
        except Exception as e:
            logger.error(f"查询任务失败 [task={task_id}]: {e}")
            return None


def list_tasks(
    conversation_id: str = None,
    status: str = None,
    user_id: str = None,
    agent_role: str = None,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    """
    列出任务 (按创建时间倒序)。
    入参:
      - conversation_id/status/user_id/agent_role: 可选过滤条件。
      - limit: 最大返回数量。
    方法:
      - 返回简表, 不含 input/output 大字段, 避免拖慢任务列表。
    出参:
      - list[dict]。
    """
    where = []
    params = []
    if conversation_id:
        where.append("conversation_id = %s")
        params.append(conversation_id)
    if status:
        where.append("status = %s")
        params.append(status)
    if user_id:
        where.append("user_id = %s")
        params.append(user_id)
    if agent_role:
        where.append("agent_role = %s")
        params.append(agent_role)
    where_clause = ("WHERE " + " AND ".join(where)) if where else ""
    params.append(limit)
    with get_conn() as conn:
        if conn is None:
            return []
        try:
            with conn.cursor() as cur:
                cur.execute(
                    f"""SELECT id, conversation_id, task_type, tool_name, status,
                               progress, error, started_at, finished_at, created_at,
                               parent_task_id, agent_role, goal
                        FROM ai_task
                        {where_clause}
                        ORDER BY id DESC
                        LIMIT %s""",
                    params,
                )
                rows = cur.fetchall()
            return [
                {
                    "id": r[0],
                    "conversation_id": r[1],
                    "task_type": r[2],
                    "tool_name": r[3],
                    "status": r[4],
                    "progress": r[5],
                    "error": r[6],
                    "started_at": r[7].isoformat() if r[7] else None,
                    "finished_at": r[8].isoformat() if r[8] else None,
                    "created_at": r[9].isoformat() if r[9] else None,
                    "parent_task_id": r[10],
                    "agent_role": r[11],
                    "goal": r[12],
                }
                for r in rows
            ]
        except Exception as e:
            logger.error(f"列出任务失败: {e}")
            return []


def get_task_logs(task_id: int, limit: int = 200) -> List[Dict[str, Any]]:
    """查询某任务的全部日志 (按时间正序)."""
    with get_conn() as conn:
        if conn is None:
            return []
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT id, level, message, elapsed_ms, created_at
                       FROM task_log
                       WHERE task_id = %s
                       ORDER BY id ASC
                       LIMIT %s""",
                    (task_id, limit),
                )
                rows = cur.fetchall()
            return [
                {
                    "id": r[0],
                    "level": r[1],
                    "message": r[2],
                    "elapsed_ms": r[3],
                    "created_at": r[4].isoformat() if r[4] else None,
                }
                for r in rows
            ]
        except Exception as e:
            logger.error(f"查询任务日志失败 [task={task_id}]: {e}")
            return []
