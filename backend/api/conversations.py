"""会话管理路由 (/api/v1/conversations)。

含历史查询、编辑/删除、trace 保存、消息分支 (Git-like DAG) fork/regenerate/branches。
会话级资源回收 (沙盒容器 + 本地缓存) 随删除一并编排。
"""
import asyncio
import logging

from fastapi import APIRouter

from backend.api.deps import agent_db, sandbox_manager
from backend.api.schemas import (
    ForkRequest,
    RegenerateRequest,
    UpdateConversationRequest,
    UpdateConversationTraceRequest,
)
from backend.utils.conv_file_cleanup import cleanup_conversation_files

router = APIRouter()
logger = logging.getLogger(__name__)


# ==================== 会话历史查询 (HTTP, 供前端侧栏) ====================

@router.get("/api/v1/conversations")
async def list_conversations(user_id: str = "study_user", project_id: str = None):
    """
    列出用户的所有历史会话 (按最后活动时间倒序)
    - project_id: 可选, 按项目过滤
    """
    return {"conversations": agent_db.list_conversations(user_id, project_id)}


@router.get("/api/v1/conversations/{conversation_id}/messages")
async def get_conversation_messages(conversation_id: str):
    """返回指定会话的全部消息 (正序), 供前端重建聊天 UI"""
    msgs = agent_db.get_conversation_messages(conversation_id)
    trace = agent_db.get_conversation_trace(conversation_id)
    return {"conversation_id": conversation_id, "messages": msgs, "trace": trace}


# ==================== 会话管理 (编辑/删除) ====================

@router.patch("/api/v1/conversations/{conversation_id}")
async def update_conversation(conversation_id: str, req: UpdateConversationRequest):
    """
    编辑会话: 重命名 和/或 移动到其他项目
    - title: 新标题
    - project_id: 目标项目 ID (空字符串 "" 表示移出项目, 不传表示不修改)
    """
    ok = agent_db.update_conversation(conversation_id, req.title, req.project_id)
    if ok:
        return {"status": "success"}
    return {"status": "error", "msg": "会话不存在或数据库不可用"}


@router.put("/api/v1/conversations/{conversation_id}/trace")
async def update_conversation_trace(conversation_id: str, req: UpdateConversationTraceRequest):
    """保存会话 trace 快照 (事件日志 / prompt 记录 / websocket 记录)。"""
    ok = agent_db.save_conversation_trace(
        conversation_id,
        event_logs=req.eventLogs,
        prompt_logs=req.promptLogs,
        ws_logs=req.wsLogs,
    )
    if ok:
        return {"status": "success"}
    return {"status": "error", "msg": "会话不存在或数据库不可用"}


@router.delete("/api/v1/conversations/{conversation_id}")
async def delete_conversation(conversation_id: str):
    """删除会话 (CASCADE 删除关联的消息和摘要)

    ★ 会话级资源回收: 删 DB 前先取出 project_id (删后查不到),
      异步回收该会话的沙盒容器 + agent-files 本地缓存文件, 不阻塞删除响应。
      回收 best-effort: 失败仅日志, 不影响会话删除成功语义。
    """
    # 删 DB 前查 project_id (回收需用它反算 work_dir / 双层产物目录定位)
    conv_meta = agent_db.get_conversation(conversation_id)
    project_id = (conv_meta or {}).get("project_id") or ""

    ok = agent_db.delete_conversation(conversation_id)

    # 资源回收: fire-and-forget, 失败仅日志
    #   ★ 先停沙盒容器 (释放 sandbox/workspace 文件占用) 再删磁盘文件, 顺序不可反。
    #   即便 conv_meta 查不到 (DB 不可用), 回收任务内部仍按 label / 约定兜底。
    try:
        asyncio.create_task(
            _reclaim_conversation_resources(conversation_id, project_id)
        )
    except Exception as e:
        logger.warning(f"[Reclaim] 删除会话 {conversation_id[:8]} 时回收调度失败: {e}")

    if ok:
        return {"status": "success"}
    return {"status": "error", "msg": "会话不存在或数据库不可用"}


async def _reclaim_conversation_resources(conversation_id: str, project_id: str):
    """
    入参:
      - conversation_id (str): 刚被删除的会话 UUID
      - project_id (str): 该会话所属项目 ID (双层产物目录定位用, 可空)
    方法:
      先停沙盒容器 (释放对 sandbox/workspace 文件的 bind mount 占用),
      再在线程池中删除 agent-files 下该会话的全部产物目录。
      ★ rmtree 是阻塞磁盘 I/O, 用 asyncio.to_thread 避免卡住事件循环。
    出参:
      无; 全程 best-effort, 任何环节异常仅记日志, 不向上抛。
    """
    # 1) 停沙盒容器: cleanup_by_conversation 自身吞异常, await 安全
    await sandbox_manager.cleanup_by_conversation(conversation_id, project_id)
    # 2) 删本地缓存: 阻塞 I/O 放线程池; to_thread 仅在线程池异常时抛, 兜底记日志
    try:
        await asyncio.to_thread(cleanup_conversation_files, conversation_id, project_id)
    except Exception as e:
        logger.warning(f"[Reclaim] 会话 {conversation_id[:8]} 本地缓存清理失败: {e}")


# ==================== v2.5 消息分支 (Git-like DAG) ====================

@router.post("/api/v1/conversations/{conversation_id}/fork")
async def fork_conversation(conversation_id: str, req: ForkRequest):
    """
    从指定消息节点开新分支 (Git-like fork)。
    - 把分叉点之后的旧消息标记 is_active=FALSE (隐藏, 不删除)
    - 更新会话 active_leaf_node 为分叉点
    - 后续新消息会挂在这个分叉点之后, 形成新分支

    用途: "回到第 N 轮重新问""编辑消息重新生成"
    """
    new_leaf = agent_db.fork_from_node(conversation_id, req.parent_node_id)
    if new_leaf:
        return {"status": "success", "leaf_node_id": new_leaf}
    return {"status": "error", "msg": "分叉失败: 节点不存在或数据库不可用"}


@router.post("/api/v1/conversations/{conversation_id}/regenerate")
async def regenerate_from_node(conversation_id: str, req: RegenerateRequest):
    """
    从指定消息节点重新生成对话 (等价于 fork + chat_request)。
    - 先 fork: 隐藏分叉点之后的旧消息
    - 再发起对话: 用新的 prompt 从分叉点继续

    ★ 这是一个同步 HTTP 端点, 只做 fork + 返回状态。
      实际的 LLM 对话由前端拿到响应后, 通过 WebSocket 发 chat_request 触发
      (复用现有 tool_chat_ws 流程, append_message 会自动挂到 active_leaf_node)。
    """
    # 编辑用户消息时从目标之前分叉；重新生成 AI 时仍从指定父节点之后分叉。
    if req.replace_node_id:
        fork_result = agent_db.fork_before_node(conversation_id, req.replace_node_id)
        if fork_result is None:
            return {"status": "error", "msg": "编辑分叉失败: 节点不存在或数据库不可用"}
        new_leaf = fork_result.get("parent_node_id")
    else:
        new_leaf = agent_db.fork_from_node(conversation_id, req.parent_node_id)
        if not new_leaf:
            return {"status": "error", "msg": "分叉失败: 节点不存在或数据库不可用"}
    # Step 2: 返回新 leaf + prompt, 前端据此发 WebSocket chat_request 继续
    return {
        "status": "success",
        "leaf_node_id": new_leaf,
        "prompt": req.prompt,
        "message": "已从指定节点分叉, 请通过 WebSocket 发送 chat_request 继续",
    }


@router.get("/api/v1/conversations/{conversation_id}/branches")
async def get_branch_info(conversation_id: str):
    """
    获取会话的分支信息 (供前端展示分支数, 轻量 UI)。
    返回: 当前 active 消息数 + 隐藏消息数 (代表有多少旧分支)。
    """
    with agent_db.get_conn() as conn:
        if conn is None:
            return {"status": "error", "msg": "数据库不可用"}
        with conn.cursor() as cur:
            cur.execute(
                """SELECT
                    count(*) FILTER (WHERE is_active = TRUE OR is_active IS NULL) as active_count,
                    count(*) FILTER (WHERE is_active = FALSE) as hidden_count
                   FROM message WHERE conversation_id = %s""",
                (conversation_id,),
            )
            row = cur.fetchone()
            return {
                "status": "success",
                "active_messages": row[0] if row else 0,
                "hidden_messages": row[1] if row else 0,
                "has_branches": (row[1] if row else 0) > 0,
            }
