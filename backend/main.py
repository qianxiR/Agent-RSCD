"""
FastAPI 入口 - Agent 服务
- 入参: HTTP 请求 / WebSocket 连接
- 方法: 注册路由、中间件、启动事件
- 出参: SSE 流式响应 / WebSocket 双向通道

★ v2.1 多对话并行:
  - 同一 WebSocket 连接可同时运行多个对话的 Agent 推理
  - stop_chat 支持按 conversation_id 精确停止
  - 新增 get_active_tasks 查询当前活跃对话列表

启动方式:
  cd agent-flow-study
  python -m backend.main
  # 或
  uvicorn backend.main:app --reload --port 8020
"""
# ★ 必须在任何 numpy/torch/MKL 相关 import 之前设置环境变量。
#   sam3 环境 (numpy + torch + MKL) 各自带一份 OpenMP 运行时, 同时初始化会触发
#   "OMP: Error #15: Initializing libiomp5md.dll, but found libiomp5md.dll already initialized"
#   导致分割完成后整个 asyncio task 崩溃, 结果无法渲染到工作区。
#   KMP_DUPLICATE_LIB_OK=TRUE 是 Intel 官方文档认可的 workaround (见错误提示里的链接)。
import os as _os
_os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

import sys
import uuid
import asyncio
import logging
from pathlib import Path
from typing import Optional

# 将项目根目录加入 sys.path, 使 from xxx 导入正常工作
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, UploadFile, File, Form, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

from backend.config import settings
from backend.utils.async_subprocess import run_subprocess
from backend.utils.conv_file_cleanup import cleanup_conversation_files

# ★ v2.3 统一文件存储 (按来源维度): 解析后的绝对路径
FILE_ROOT = Path(settings.file_storage_root).resolve()
SAMSEG_UPLOAD = Path(settings.samseg_upload_root).resolve()
SAMSEG_OUTPUT = Path(settings.samseg_output_root).resolve()
GEOSERVER_DL = Path(settings.geoserver_download_root).resolve()
SANDBOX_ROOT = Path(settings.sandbox_workspace_dir).resolve()
for d in [FILE_ROOT, SAMSEG_UPLOAD, SAMSEG_OUTPUT, GEOSERVER_DL, SANDBOX_ROOT]:
    d.mkdir(parents=True, exist_ok=True)
from backend.agent.ws_manager import get_ws_manager
from backend.agent.chat_service import AgentChatService, ToolChatRequest
from backend.agent.memory import agent_db
from backend.data import geoserver_client
from backend.data import business_db
# ★ 沙盒回收: 删除会话时按 conversation_id 停止对应容器 (会话级回收)
from backend.model.tools.sandbox_manager import sandbox_manager

# ★ 导入工具模块, 触发 @register_tool 装饰器执行, 将工具注册到全局注册表
#   model.tools: GeoServer/数据库/图层/遥感工具 (工具箱, 主体)
#   agent.tools: 记忆工具 (跟随记忆留在 agent 层)
import backend.model.tools
import backend.agent.tools

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="国土智察-自然资源遥感智能监测系统", version="1.0.0")

# CORS - 允许前端跨域访问
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

chat_service = AgentChatService()


@app.on_event("startup")
async def _startup_init():
    """启动时初始化 agent_db schema (不可用则只 warning, 不阻塞)"""
    ok = agent_db.init_schema()
    if ok:
        logger.info("agent_db schema 就绪, 将使用 DB 持久化记忆")
    else:
        logger.warning("agent_db 不可用, 记忆将降级到内存模式")

    # ★ Phase A: 业务库 schema (image_metadata / vector_layer, #2/#4)
    #   不可用时只 warning, 元数据登记将静默跳过, 不影响其他功能.
    try:
        bok = business_db.ensure_business_schema()
        if bok:
            logger.info("business_db schema 就绪 (image_metadata / vector_layer, PostGIS=%s)",
                        business_db.postgis_available())
        else:
            logger.warning("business_db schema 未就绪, 元数据登记将降级")
    except Exception as e:
        logger.warning(f"business_db schema 初始化异常 (元数据登记将降级): {e}")

    # ★ v2.3: 打印统一文件存储路径 (按来源维度)
    logger.info(f"[Storage] 文件根目录: {FILE_ROOT}")
    logger.info(f"[Storage]   samseg/send:     {SAMSEG_UPLOAD}")
    logger.info(f"[Storage]   samseg/generate: {SAMSEG_OUTPUT}")
    logger.info(f"[Storage]   geoserver/generate: {GEOSERVER_DL}")
    logger.info(f"[Storage]   sandbox/workspace:  {SANDBOX_ROOT}")

    # ★ SamSeg 模型按需加载 (2026-06 改):
    #   不再在启动期预加载 SAM 3 权重 (3.3GB, load_model 一次几十秒, 会占显存/内存)。
    #   runner._get_processor() 本身已是惰性单例 —— 首次 run_segment / run_change_detection
    #   调用时才加载, 之后全局复用 _processor。这样:
    #     - 不做遥感分析时, 模型永不加载, 不占资源
    #     - 启动更快, 不阻塞 (即使原本是后台线程也会争抢启动期的 CPU/IO)
    #   代价: 首次遥感请求多等几十秒加载时间, 由调用方 (工具层) 自行承担。

    # ★ 沙盒状态检查 (启动时一次性诊断, 不阻塞服务启动):
    #   检查 3 级: 配置开关 → Docker daemon → 沙盒镜像。
    #   不可用时只 warning, sandbox_manager.is_available() 会在运行时重新检测
    #   (成功时缓存, 失败时不缓存, 允许后续恢复)。
    if not settings.sandbox_enabled:
        logger.info("[Sandbox] 沙盒已禁用 (SANDBOX_ENABLED=false)")
    else:
        logger.info(
            f"[Sandbox] 配置已启用 | image={settings.sandbox_docker_image} "
            f"network={settings.sandbox_docker_network or 'host'} "
            f"volume={settings.sandbox_docker_volume or '(bind mount)'} "
            f"timeout={settings.sandbox_exec_timeout}s"
        )
        try:
            # 检查 Docker daemon 可达性
            docker_info = await run_subprocess(
                ["docker", "info", "--format", "{{.ServerVersion}}"]
            )
            if docker_info["returncode"] != 0:
                err = docker_info["stderr"].decode("utf-8", errors="replace").strip()
                logger.warning(f"[Sandbox] Docker daemon 不可达: {err or '(未知错误)'} — 沙盒功能将降级")
            else:
                daemon_ver = docker_info["stdout"].decode().strip()
                # 检查沙盒镜像是否已构建
                img = settings.sandbox_docker_image
                image_info = await run_subprocess(
                    [
                        "docker", "image", "inspect", img,
                        "--format", "size={{.Size}} created={{.Created}}",
                    ]
                )
                if image_info["returncode"] != 0:
                    err2 = image_info["stderr"].decode("utf-8", errors="replace").strip()
                    logger.warning(
                        f"[Sandbox] 镜像 '{img}' 未构建 ({err2 or 'not found'}) "
                        f"— 沙盒功能将降级。构建命令: docker build -t {img} -f sandbox/Dockerfile ."
                    )
                else:
                    logger.info(
                        f"[Sandbox] 就绪 | Docker {daemon_ver} | "
                        f"镜像={img} ({image_info['stdout'].decode().strip()})"
                    )
        except FileNotFoundError:
            logger.warning("[Sandbox] 未找到 docker 命令 — 沙盒功能将降级")
        except Exception as e:
            logger.warning("[Sandbox] 启动检查异常: %r — 运行时将重试检测", e, exc_info=True)


# ==================== 项目管理端点 (新增) ====================

class CreateProjectRequest(BaseModel):
    name: str
    description: str = ""
    folder_path: str = None  # ★ 工作区文件夹路径 (可选, 传了走 UPSERT)


class UpdateProjectRequest(BaseModel):
    name: str = None
    description: str = None


@app.get("/api/v1/projects")
async def list_projects(user_id: str = "study_user"):
    """列出用户的所有项目"""
    projects = agent_db.list_projects(user_id)
    return {"projects": projects}


@app.post("/api/v1/projects")
async def create_project(req: CreateProjectRequest, user_id: str = "study_user"):
    """创建新项目 (支持 folder_path 关联本地文件夹, 已有同路径则复用)"""
    project_id = agent_db.create_project(user_id, req.name, req.description, req.folder_path)
    if project_id:
        return {"status": "success", "project_id": project_id}
    return {"status": "error", "msg": "数据库不可用"}


@app.patch("/api/v1/projects/{project_id}")
async def update_project(project_id: str, req: UpdateProjectRequest):
    """更新项目名称/描述"""
    ok = agent_db.update_project(project_id, req.name, req.description)
    if ok:
        return {"status": "success"}
    return {"status": "error", "msg": "项目不存在或数据库不可用"}


@app.delete("/api/v1/projects/{project_id}")
async def delete_project(project_id: str):
    """删除项目 (会话的 project_id 会被设为 NULL, 不会级联删除会话)"""
    ok = agent_db.delete_project(project_id)
    if ok:
        return {"status": "success"}
    return {"status": "error", "msg": "项目不存在或数据库不可用"}


# ==================== 工作区文件夹浏览 API (v2.4) ====================

@app.get("/api/v1/workspace/list")
async def list_workspace(roots: str = "", depth: int = 3):
    """
    列出指定根目录下的子文件夹 (递归到 depth 层)。
    ★ 安全: 规范化路径 + 拒绝 .. 穿越 + 拒绝系统目录 + 不可读返回空。
    入参: roots (逗号分隔的绝对路径列表), depth (递归深度, 默认3)
    出参: { roots: [{ path, name, children: [{ path, name, children: [...] }] }] }
    """
    import os
    import sys

    if not roots:
        return {"roots": []}

    depth = max(1, min(depth, 5))  # 限制 1-5 层
    root_paths = [r.strip() for r in roots.split(",") if r.strip()]
    result = []

    # 系统目录黑名单 (Windows)
    system_prefixes = [
        "C:\\Windows", "C:\\Program Files", "C:\\Program Files (x86)",
        "C:\\ProgramData", "C:\\System Volume Information", "C:\\$Recycle.Bin",
    ]

    def _list_recursive(dir_path, current_depth, max_depth):
        """递归列出子文件夹，返回树状结构。"""
        if current_depth >= max_depth:
            return []
        try:
            entries = os.listdir(dir_path)
        except (PermissionError, OSError):
            return []
        children = []
        for entry in sorted(entries, key=lambda e: e.lower()):
            full = os.path.join(dir_path, entry)
            try:
                if os.path.isdir(full) and not entry.startswith("."):
                    node = {"name": entry, "path": full}
                    sub = _list_recursive(full, current_depth + 1, max_depth)
                    if sub:
                        node["children"] = sub
                    children.append(node)
            except OSError:
                pass
        return children

    for root in root_paths:
        root_entry = {"path": root, "name": os.path.basename(root) or root, "children": [], "error": None}
        try:
            if ".." in root:
                root_entry["error"] = "路径包含 ..，拒绝访问"
                result.append(root_entry)
                continue

            norm = os.path.normpath(root)
            abs_root = os.path.abspath(norm)

            is_system = False
            for sp in system_prefixes:
                if abs_root.lower().startswith(sp.lower()):
                    is_system = True
                    break
            if is_system:
                root_entry["error"] = "系统目录，拒绝访问"
                result.append(root_entry)
                continue

            if not os.path.isdir(abs_root):
                root_entry["error"] = "目录不存在或不可读"
                result.append(root_entry)
                continue

            root_entry["path"] = abs_root
            root_entry["name"] = os.path.basename(abs_root) or abs_root
            root_entry["children"] = _list_recursive(abs_root, 0, depth)
        except Exception as e:
            root_entry["error"] = str(e) or "未知错误"

        result.append(root_entry)

    return {"roots": result}


# ==================== 会话历史查询端点 (HTTP, 供前端侧栏) ====================

@app.get("/api/v1/conversations")
async def list_conversations(user_id: str = "study_user", project_id: str = None):
    """
    列出用户的所有历史会话 (按最后活动时间倒序)
    - project_id: 可选, 按项目过滤
    """
    return {"conversations": agent_db.list_conversations(user_id, project_id)}


@app.get("/api/v1/conversations/{conversation_id}/messages")
async def get_conversation_messages(conversation_id: str):
    """返回指定会话的全部消息 (正序), 供前端重建聊天 UI"""
    msgs = agent_db.get_conversation_messages(conversation_id)
    trace = agent_db.get_conversation_trace(conversation_id)
    return {"conversation_id": conversation_id, "messages": msgs, "trace": trace}


# ==================== 会话管理端点 (新增: 编辑/删除) ====================

class UpdateConversationRequest(BaseModel):
    title: str = None
    project_id: str = None  # 空字符串表示移出项目


class UpdateConversationTraceRequest(BaseModel):
    eventLogs: list[dict] = []
    promptLogs: list[dict] = []
    wsLogs: list[dict] = []


@app.patch("/api/v1/conversations/{conversation_id}")
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


@app.put("/api/v1/conversations/{conversation_id}/trace")
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


@app.delete("/api/v1/conversations/{conversation_id}")
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


# ==================== v2.5 消息分支 (Git-like DAG) 端点 ====================

class ForkRequest(BaseModel):
    """从指定消息节点分叉, 开新分支"""
    parent_node_id: str  # 分叉点: 新分支挂在这条消息之后


@app.post("/api/v1/conversations/{conversation_id}/fork")
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


class RegenerateRequest(BaseModel):
    """
    入参: prompt 必填；parent_node_id 与 replace_node_id 按分支模式二选一。
    方法: 校验重新生成或编辑替换请求的结构化参数。
    出参: FastAPI regenerate 路由使用的请求模型。
    """
    parent_node_id: Optional[str] = None   # 分叉点 (从哪条消息之后重新开始)
    replace_node_id: Optional[str] = None  # 编辑模式: 替换该用户消息及其后续分支
    prompt: str           # 新的 prompt (编辑后的用户输入)
    model: Optional[str] = None  # 可选, 指定模型 (默认用 settings.llm_model)


@app.post("/api/v1/conversations/{conversation_id}/regenerate")
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
    # Step 2: 返回新 leaf + prompt, 前端据此发 WebSocket chat_request
    return {
        "status": "success",
        "leaf_node_id": new_leaf,
        "prompt": req.prompt,
        "message": "已从指定节点分叉, 请通过 WebSocket 发送 chat_request 继续",
    }


@app.get("/api/v1/conversations/{conversation_id}/branches")
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


# ==================== AI 任务查询端点 (P3 任务持久化) ====================
# 这些端点只读 ai_task / task_log, 供前端任务监控面板 (P8) 或排查问题用.

@app.get("/api/v1/tasks")
async def list_tasks(
    conversation_id: str = None,
    status: str = None,
    user_id: str = None,
    limit: int = 50,
):
    """
    列出 AI 任务 (按创建时间倒序). 可按 conversation_id / status / user_id 过滤.
    - status: pending / running / done / failed / cancelled
    - 返回简表 (不含 input/output 详情)
    """
    if not agent_db.agent_db_available():
        return {"status": "error", "msg": "agent_db 不可用, 任务功能未启用"}
    tasks = agent_db.list_tasks(
        conversation_id=conversation_id, status=status, user_id=user_id, limit=limit
    )
    return {"status": "success", "count": len(tasks), "tasks": tasks}


@app.get("/api/v1/tasks/{task_id}")
async def get_task(task_id: int):
    """查询单个任务详情 (含 input/output/error/时间戳)."""
    if not agent_db.agent_db_available():
        return {"status": "error", "msg": "agent_db 不可用, 任务功能未启用"}
    task = agent_db.get_task(task_id)
    if task is None:
        return {"status": "error", "msg": f"任务不存在: {task_id}"}
    return {"status": "success", "task": task}


@app.get("/api/v1/tasks/{task_id}/logs")
async def get_task_logs(task_id: int, limit: int = 200):
    """查询某任务的执行日志 (按时间正序)."""
    if not agent_db.agent_db_available():
        return {"status": "error", "msg": "agent_db 不可用, 任务功能未启用"}
    logs = agent_db.get_task_logs(task_id, limit=limit)
    return {"status": "success", "count": len(logs), "logs": logs}


# ==================== GeoServer 代理端点 ====================

@app.get("/api/v1/geoserver/feature-info")
async def get_feature_info(
    layer_name: str,
    bbox: str,       # "minx,miny,maxx,maxy"
    width: int,
    height: int,
    i: int,
    j: int,
    workspace: str = None,
):
    """
    WMS GetFeatureInfo 代理 —— 前端点击遥感影像时获取该像素的属性数据。
    代理原因: 避免浏览器 CORS 限制, 统一 GeoServer 认证。
    """
    try:
        bbox_parts = [float(x) for x in bbox.split(",")]
        if len(bbox_parts) != 4:
            return {"success": False, "msg": "bbox 格式错误, 应为 minx,miny,maxx,maxy"}
    except ValueError:
        return {"success": False, "msg": "bbox 解析失败, 所有值必须为数字"}

    result = geoserver_client.get_feature_info(
        layer_name=layer_name,
        bbox=(bbox_parts[0], bbox_parts[1], bbox_parts[2], bbox_parts[3]),
        width=width,
        height=height,
        i=i,
        j=j,
        workspace=workspace,
    )
    return result


@app.get("/api/v1/geoserver/wms")
async def proxy_geoserver_wms(request: Request):
    """
    WMS GetMap/GetLegendGraphic 通用代理 —— 前端 OpenLayers TileWMS 瓦片请求经此转发。
    代理原因: 与 feature-info 同 —— 前端直连 GeoServer (默认 http://localhost:8080/geoserver/wms)
      会因浏览器到 GeoServer 的地址/CORS 限制而加载失败 (表现为“WMS 加载超时”)。
      走后端代理后: ① 地址随服务端 settings.geoserver_url, 不再硬编码 localhost;
                    ② 后端内网访问 + 统一认证, 绕过浏览器 CORS。
    方法: 把前端 query 原样转发到 settings.geoserver_wms_url, 透传响应体与 Content-Type。
    出参: 原始瓦片字节流 (image/png 等), 带原始 Content-Type; GeoServer 不可用时返回 502。
    """
    # ★ GeoServer 不可用直接 502, 避免前端逐瓦片空等触发 10s 超时
    if not geoserver_client.geoserver_available():
        return Response(
            content='{"error": "GeoServer 不可用"}',
            media_type="application/json",
            status_code=502,
        )

    # 把前端的 query 参数原样转发给 GeoServer WMS (含 LAYERS/SLD_BODY/FORMAT 等)
    # ★ 用 httpx async, 避免在 async 端点里用同步 requests 阻塞事件循环
    #   (并发瓦片请求多, 阻塞会拖慢同连接上的其他请求)。
    # ★ trust_env=False: 后端访问本机 GeoServer (127.0.0.1:8080) 属内网固定地址,
    #   不应走任何系统代理。Windows 开启系统代理(Clash/v2ray 等)时, httpx 默认
    #   trust_env=True 会读取注册表代理把本机请求劫持到代理端口 → "All connection
    #   attempts failed" (2s 超时) → 502。requests 库默认不读注册表代理, 故不受影响。
    params = dict(request.query_params)
    try:
        import httpx as _httpx
        async with _httpx.AsyncClient(timeout=30.0, trust_env=False) as client:
            resp = await client.get(
                settings.geoserver_wms_url,
                params=params,
                auth=(settings.geoserver_username, settings.geoserver_password),
            )
    except Exception as e:
        return Response(
            content=f'{{"error": "GeoServer 请求失败: {e}"}}',
            media_type="application/json",
            status_code=502,
        )

    if resp.status_code != 200:
        return Response(
            content=resp.content,
            media_type=resp.headers.get("Content-Type", "application/json"),
            status_code=resp.status_code,
        )
    # 透传原始字节 + Content-Type (通常是 image/png)
    return Response(
        content=resp.content,
        media_type=resp.headers.get("Content-Type", "image/png"),
    )


# ==================== GeoServer 图层管理端点 (供前端图层管理面板直接调用) ====================
# ★ 设计意图: 让前端面板能直接拉数据/操作图层, 不必每次走 AI (省 token、即时响应)。
#   全部复用 backend.data.geoserver_client 已有能力, 不引入新数据层逻辑。

@app.get("/api/v1/geoserver/services")
async def list_geoserver_services_tree():
    """
    列出 GeoServer 所有工作空间及其图层树 (供前端图层管理面板渲染)。

    返回: {status, workspaces:[{name, layer_count, layers:[{name, full_name, type}]}], services:{...}}
      - type: "raster" / "vector" / "unknown" (前端据此刻画不同图标 + 决定 WMS/WFS 渲染方式)
    """
    try:
        if not geoserver_client.geoserver_available():
            return {"status": "error", "msg": "GeoServer 不可用, 请检查服务是否启动"}
        data = geoserver_client.list_services()

        # ★ 给每个图层补 type: 复用 get_layer_type (REST GET /layers/{ws}:{layer}.json 判定栅格/矢量)
        workspaces = data.get("workspaces", [])
        for ws in workspaces:
            layer_names = ws.get("layers", [])
            typed_layers = []
            for ln in layer_names:
                full = f"{ws['workspace']}:{ln}"
                try:
                    ltype = geoserver_client.get_layer_type(full)
                except Exception:
                    ltype = "unknown"
                typed_layers.append({"name": ln, "full_name": full, "type": ltype or "unknown"})
            ws["layers_typed"] = typed_layers

        total = sum(ws.get("layer_count", len(ws.get("layers", []))) for ws in workspaces)
        return {
            "status": "success",
            "workspaces": workspaces,
            "services": data.get("services", {}),
            "total_layers": total,
        }
    except Exception as e:
        return {"status": "error", "msg": str(e)}


@app.get("/api/v1/geoserver/layers/{workspace}/{layer_name}/bbox")
async def get_layer_bbox(workspace: str, layer_name: str):
    """
    获取指定图层的 BBOX (供前端"定位到图层"功能)。
    复用 geoserver_client.get_layer_bbox (WMS GetCapabilities)。

    返回: {status, bbox:{minx,miny,maxx,maxy}} 或 {status:"error", msg}
    """
    try:
        if not geoserver_client.geoserver_available():
            return {"status": "error", "msg": "GeoServer 不可用"}
        full = f"{workspace}:{layer_name}"
        bbox = geoserver_client.get_layer_bbox(full)
        if bbox is None:
            return {"status": "error", "msg": f"无法获取图层范围: {full}"}
        return {"status": "success", "bbox": bbox, "full_name": full}
    except Exception as e:
        return {"status": "error", "msg": str(e)}


@app.delete("/api/v1/geoserver/layers/{workspace}/{layer_name}")
async def delete_geoserver_layer_rest(workspace: str, layer_name: str):
    """
    删除指定图层 (供前端图层面板"删除图层"按钮直接调用)。
    复用 geoserver_client.delete_layer (REST DELETE coveragestores/datastores, 含真实删除校验)。

    返回: {status:"success", layer_name, workspace, layer_type, msg} 或 {status:"error", msg}
    """
    try:
        if not geoserver_client.geoserver_available():
            return {"status": "error", "msg": "GeoServer 不可用"}
        # ★ delete_layer 签名: (layer_name 裸名, workspace, delete_store) — 分开传, 不拼全名
        result = geoserver_client.delete_layer(layer_name, workspace=workspace, delete_store=True)
        return result  # delete_layer 已返回标准 {status, layer_name, workspace, layer_type, msg}
    except Exception as e:
        return {"status": "error", "msg": str(e)}


class PublishLayerRequest(BaseModel):
    """前端发布图层请求 (图层面板"上传图层"按钮)。
    file_path: 已上传到服务器的文件绝对路径 (先经 /api/v1/files/upload 获得)
    layer_name: 发布后的图层名, 留空则用文件名
    workspace: 已废弃 — 前端无传参入口, 后端按图层类型固定工作空间 (保留字段仅向后兼容)
    layer_type: 强制指定类型 "raster"/"vector"; 留空则按扩展名推断
    """
    file_path: str
    layer_name: str = ""
    workspace: str = ""
    layer_type: str = ""


@app.post("/api/v1/geoserver/layers")
async def publish_geoserver_layer_rest(req: PublishLayerRequest):
    """
    发布本地文件为 GeoServer 图层 (供前端图层面板直接调用, 不经过 LLM)。

    ★ 工作空间按图层类型固定 (前端无 workspace 传参入口):
      - 栅格 (.tif/.tiff/.cog) → cd_raster
      - 矢量 (.shp)            → cd_shp
    路由到底层 upload_raster / upload_shapefile。

    返回: {status, layer_name, workspace, wms_url, msg} 或 {status:"error", msg}
    """
    # ★ 按图层类型固定的目标工作空间 (前端无传参入口, 后端决策; 避免透传空串给 GeoServer
    #   触发 "workspace name must not be null")
    WS_BY_TYPE = {"raster": "cd_raster", "vector": "cd_shp"}

    try:
        if not geoserver_client.geoserver_available():
            return {"status": "error", "msg": "GeoServer 不可用"}

        import os
        if not os.path.isfile(req.file_path):
            return {"status": "error", "msg": f"文件不存在: {req.file_path}"}

        # ★ 类型推断: 显式指定 > 扩展名 (仅栅格/矢量两类, 不支持 geojson 直接上传)
        ext = os.path.splitext(req.file_path)[1].lower()
        if req.layer_type == "raster" or ext in (".tif", ".tiff", ".cog"):
            layer_type = "raster"
        elif req.layer_type == "vector" or ext in (".shp",):
            layer_type = "vector"
        else:
            return {"status": "error", "msg": f"不支持的文件类型: {ext} (支持 .tif/.tiff/.cog/.shp)"}

        target_ws = WS_BY_TYPE[layer_type]
        if layer_type == "raster":
            result = geoserver_client.upload_raster(req.file_path, req.layer_name or None, target_ws)
        else:
            result = geoserver_client.upload_shapefile(req.file_path, req.layer_name or None, target_ws)

        if result.get("status") == "success":
            return {"status": "success", **{k: v for k, v in result.items() if k != "status"},
                    "msg": result.get("msg", "")}
        return {"status": "error", "msg": result.get("msg", "发布失败")}
    except Exception as e:
        return {"status": "error", "msg": str(e)}


# ==================== 文件下载端点 ====================

@app.get("/api/v1/download/{filepath:path}")
async def download_file(filepath: str, request: Request):
    """
    下载已生成的文件（GeoTIFF / PNG 等）。
    ★ v2.3: 文件存放在 agent-files/ 下按来源分目录,
      filepath 已包含来源前缀 (如 samseg/generate/... 或 geoserver/generate/...)
    ★ 影像预览: 带 ?preview=1 时, 对浏览器不支持的格式 (tif/geotiff 等) 自动转 PNG
      返回 (供 <img> 直接显示); 浏览器原生格式 (png/jpg/...) 零开销原样返回。
      不带 ?preview=1 时行为完全不变 (renderDownload 等下载场景走原路径)。
    """
    import mimetypes
    file_path = (FILE_ROOT / filepath).resolve()
    if not str(file_path).startswith(str(FILE_ROOT)):
        raise HTTPException(status_code=403, detail="禁止访问")
    if not file_path.is_file():
        raise HTTPException(status_code=404, detail=f"文件不存在: {filepath}")

    # ★ 影像预览转码: 仅在 ?preview=1 且非浏览器原生格式时触发
    if request.query_params.get("preview") == "1":
        from backend.model.tools import image_preview
        try:
            img_bytes, mime = image_preview.render_image_to_png_bytes(file_path)
            return Response(content=img_bytes, media_type=mime)
        except Exception as e:
            # 转码失败 → 兜底走原文件下载 (不比现状差)
            logger.warning(f"[download] 影像转码失败, 落回原文件: {filepath} ({e})")

    media, _ = mimetypes.guess_type(filepath)
    return FileResponse(
        path=str(file_path),
        filename=file_path.name,
        media_type=media or "application/octet-stream",
    )


# ==================== 沙盒文件导出到宿主机端点 (v2.5) ====================

class SandboxDownloadRequest(BaseModel):
    """前端卡片下载按钮请求体。"""
    file_path: str   # 沙盒内路径或 agent-files 相对路径
    dest_dir: str    # 宿主机目标文件夹绝对路径 (任意盘符)


@app.post("/api/v1/sandbox/download-to-host")
async def sandbox_download_to_host(req: SandboxDownloadRequest):
    """
    把沙盒可访问的文件下载（复制）到用户指定的宿主机任意目录。
    ★ 前端卡片下载按钮调用；复用 sandbox_export_tools.export_to_host 的路径解析 + 复制逻辑。
    ★ 与 download_file_from_sandbox 工具共用同一份逻辑, 路径映射规则一致。

    入参: {file_path, dest_dir}
    出参: {ok: True, dest_path, size_bytes} 或 {ok: False, msg}
    """
    from backend.model.tools.sandbox_export_tools import export_to_host
    result = export_to_host(req.file_path, req.dest_dir)
    if not result.get("ok"):
        return {"ok": False, "msg": result.get("msg", "导出失败")}
    return {
        "ok": True,
        "dest_path": result["dest_path"],
        "size_bytes": result["size_bytes"],
    }


@app.get("/api/v1/upload/{filepath:path}")
async def serve_upload_file(filepath: str, request: Request):
    """
    读取用户上传的文件 (SamSeg 输入影像等), 供前端展示原图。
    ★ v2.3: 文件存放在 agent-files/samseg/send/ 下。
    ★ 影像预览: 带 ?preview=1 时, 对浏览器不支持的格式 (tif/geotiff 等) 自动转 PNG
      返回 (供 <img> 直接显示); 浏览器原生格式 (png/jpg/...) 零开销原样返回。
      不带 ?preview=1 时行为完全不变。
    """
    import mimetypes
    file_path = (SAMSEG_UPLOAD / filepath).resolve()
    if not str(file_path).startswith(str(SAMSEG_UPLOAD)):
        raise HTTPException(status_code=403, detail="禁止访问")
    if not file_path.is_file():
        raise HTTPException(status_code=404, detail=f"文件不存在: {filepath}")

    # ★ 影像预览转码: 仅在 ?preview=1 且非浏览器原生格式时触发
    if request.query_params.get("preview") == "1":
        from backend.model.tools import image_preview
        try:
            img_bytes, mime = image_preview.render_image_to_png_bytes(file_path)
            return Response(content=img_bytes, media_type=mime)
        except Exception as e:
            # 转码失败 → 兜底走原文件下载 (不比现状差)
            logger.warning(f"[upload] 影像转码失败, 落回原文件: {filepath} ({e})")

    media, _ = mimetypes.guess_type(filepath)
    return FileResponse(
        path=str(file_path),
        filename=file_path.name,
        media_type=media or "application/octet-stream",
    )


# ==================== SamSeg 图片上传端点 ====================

@app.get("/api/v1/image/meta")
async def get_image_meta(path: str):
    """
    返回影像的地理元数据 (供前端卡片显示坐标范围 + CRS)。

    ★ 用 rasterio 读; 非 GeoTIFF / 无 CRS 的影像返回 has_crs=false。
    ★ 安全: path 必须在 agent-files/ 或 samseg/send/ 之下, 防路径越权。

    入参: path (str) - 影像宿主机绝对路径 或 相对 agent-files/ 的路径
    出参: {has_crs, crs, bbox: [minx,miny,maxx,maxy], width, height, driver}
    """
    from pathlib import Path as _Path

    # 路径解析: 支持绝对路径 或 相对 FILE_ROOT 的路径
    p = _Path(path).expanduser()
    if not p.is_absolute():
        p = (FILE_ROOT / path).resolve()
    else:
        p = p.resolve()

    # 安全边界: 必须在 FILE_ROOT 之下
    if not str(p).startswith(str(FILE_ROOT)):
        return {"has_crs": False, "msg": "路径不在可访问范围内"}

    if not p.is_file():
        return {"has_crs": False, "msg": f"文件不存在: {path}"}

    try:
        import rasterio
        with rasterio.open(str(p)) as src:
            crs = src.crs
            if crs is None:
                return {"has_crs": False, "width": src.width, "height": src.height,
                        "driver": src.driver, "msg": "影像无 CRS (本地影像)"}
            # 计算 bbox (4 个角点的地理坐标)
            # rasterio bounds: (left, bottom, right, top) = (minx, miny, maxx, maxy)
            bounds = src.bounds
            bbox = [round(bounds.left, 6), round(bounds.bottom, 6),
                    round(bounds.right, 6), round(bounds.top, 6)]
            # CRS 展示名 (优先 EPSG:xxxx, 兜底从 WKT 提取投影名)
            crs_str = str(crs)
            epsg = None
            try:
                epsg = crs.to_epsg()
            except Exception:
                pass
            if epsg:
                crs_str = f"EPSG:{epsg}"
            else:
                # to_epsg 失败 (如 PROJ 数据库冲突) → 从 WKT 提取 PROJCS/GEOGCS 名字简写
                import re as _re
                m = _re.search(r'PROJCS\["([^"]+)"', crs_str) or _re.search(r'GEOGCS\["([^"]+)"', crs_str)
                if m:
                    crs_str = m.group(1)
                elif len(crs_str) > 40:
                    crs_str = crs_str[:37] + "..."
            return {
                "has_crs": True,
                "crs": crs_str,
                "bbox": bbox,
                "width": src.width,
                "height": src.height,
                "driver": src.driver,
            }
    except ImportError:
        return {"has_crs": False, "msg": "rasterio 未安装"}
    except Exception as e:
        # 非影像文件 / rasterio 读不了 → 尝试用 PIL 读尺寸
        try:
            from PIL import Image
            with Image.open(str(p)) as img:
                return {"has_crs": False, "width": img.width, "height": img.height,
                        "msg": f"非地理影像 ({e})"}
        except Exception:
            return {"has_crs": False, "msg": f"读取失败: {e}"}


@app.get("/api/v1/vector/shp-to-geojson")
async def shp_to_geojson(shp_path: str):
    """
    读 Shapefile → 返回 GeoJSON (供前端 OpenLayers 渲染矢量层)。

    ★ 设计:
      - geopandas.read_file 自动解析同目录的 .dbf/.shx/.prj, 前端只需传 .shp 主文件路径。
      - 兼容两种入参形态:
          ① .shp 主文件绝对路径 (如 .../xxx_shp/xxx.shp)
          ② shp 多文件目录路径 (即工具返回的 shp_zip_path 字段, 实为目录) → 自动取目录下第一个 .shp
      - 返回标准 GeoJSON FeatureCollection, OL 的 ol.format.GeoJSON 可直接消费。
      - 安全边界: 路径必须在 FILE_ROOT (agent-files) 之下, 防路径越权。

    入参:
      - shp_path (str): .shp 主文件路径 或 shp 目录路径 (绝对 或 相对 FILE_ROOT)
    出参:
      - {success, geojson, feature_count, crs} 或 {success: False, msg}
    """
    from pathlib import Path as _Path

    # 路径解析: 绝对路径 或 相对 FILE_ROOT
    p = _Path(shp_path).expanduser()
    if not p.is_absolute():
        p = (FILE_ROOT / shp_path).resolve()
    else:
        p = p.resolve()

    # 安全边界: 必须在 FILE_ROOT 之下
    if not str(p).startswith(str(FILE_ROOT)):
        return {"success": False, "msg": "路径不在可访问范围内"}

    # 兼容目录入参: 若传的是目录 (shp_zip_path 字段), 取目录下第一个 .shp
    if p.is_dir():
        shp_files = sorted(p.glob("*.shp"))
        if not shp_files:
            return {"success": False, "msg": f"目录下无 .shp 文件: {shp_path}"}
        p = shp_files[0].resolve()
    elif not p.is_file():
        return {"success": False, "msg": f"文件不存在: {shp_path}"}
    elif p.suffix.lower() != ".shp":
        return {"success": False, "msg": f"不是 Shapefile (.shp): {shp_path}"}

    try:
        import geopandas as gpd
    except ImportError:
        return {"success": False, "msg": "geopandas 未安装, 无法读取 Shapefile"}

    try:
        gdf = gpd.read_file(str(p))
    except Exception as e:
        return {"success": False, "msg": f"Shapefile 读取失败: {e}"}

    if gdf is None or gdf.empty:
        return {"success": False, "msg": "Shapefile 为空 (无有效要素)"}

    feature_count = len(gdf)

    # ★ 大文件保护: feature 数 > 5000 时采样, 避免前端 OL 渲染卡死
    sampled = False
    if feature_count > 5000:
        gdf = gdf.sample(n=5000, random_state=42)
        sampled = True

    # CRS 信息 (供前端判断投影, OL 默认按 EPSG:4326 解析)
    crs_str = None
    try:
        if gdf.crs is not None:
            epsg = None
            try:
                epsg = gdf.crs.to_epsg()
            except Exception:
                pass
            crs_str = f"EPSG:{epsg}" if epsg else str(gdf.crs)
            # 非 4326 时转 4326 (OL 前端按经纬度渲染)
            if epsg and epsg != 4326:
                gdf = gdf.to_crs(epsg=4326)
                crs_str = "EPSG:4326"
    except Exception as e:
        logger.warning(f"[SHP2GeoJSON] CRS 处理失败, 按原始坐标返回: {e}")

    try:
        geojson = gdf.to_json()
    except Exception as e:
        return {"success": False, "msg": f"GeoJSON 序列化失败: {e}"}

    logger.info(f"[SHP2GeoJSON] {p.name} → {feature_count} 要素 (采样={sampled}), crs={crs_str}")
    return {
        "success": True,
        "geojson": geojson,
        "feature_count": feature_count,
        "crs": crs_str,
        "sampled": sampled,
        "shp_path": str(p),
    }


@app.post("/api/v1/samseg/upload")
async def upload_samseg_images(
    images: list[UploadFile] = File(...),
    conversation_id: str = Form(None),
):
    """
    接收前端上传的图片 (1-2 张), 存到 agent-files/send/{conversation_id}/, 返回绝对路径列表供 agent 工具使用。

    ★ v2.3 规范化:
      - 保存根目录: agent-files/send/{conversation_id}/
      - 命名规则: {conv前缀}_{原文件名} (同会话同文件名重传时加短随机后缀防覆盖)
      - 无会话 id 时: 退回 agent-files/send/_anonymous/{时间戳}_{原文件名}

    - 入参: multipart/form-data
      - images: 1-2 个图片文件 (分割 1 张, 变化检测 2 张)
      - conversation_id (可选): 所属会话 id
    - 出参: {status, paths: [绝对路径...], count, files:[{name,path,preview_url,source}]}
    - 前端拿到 files 后静默保存为聊天附件, 不再把路径写入输入框。
    """
    if not images:
        raise HTTPException(status_code=400, detail="未收到图片文件")

    files = images[:2]  # 最多 2 张

    import time
    import re

    # ★ v2.3: 按会话隔离上传文件 → agent-files/samseg/send/{conversation_id}/
    conv_prefix = ""
    if conversation_id:
        conv_prefix = re.sub(r"[^a-zA-Z0-9._-]", "", conversation_id)
    conv_dir_name = conv_prefix if conv_prefix else "_anonymous"
    upload_dir = (SAMSEG_UPLOAD / conv_dir_name).resolve()
    upload_dir.mkdir(parents=True, exist_ok=True)

    saved_paths = []
    saved_files = []
    for f in files:
        if not f.filename:
            continue
        # 防路径遍历: 只取文件名
        safe_name = Path(f.filename).name
        if conv_prefix:
            # 带会话 id 命名; 若已存在同名文件, 加 4 位随机后缀防覆盖
            fname = f"{conv_prefix}_{safe_name}"
            dest = upload_dir / fname
            if dest.exists():
                suffix = _rand_suffix(4)
                stem = Path(safe_name).stem
                ext = Path(safe_name).suffix
                dest = upload_dir / f"{conv_prefix}_{stem}_{suffix}{ext}"
        else:
            # 无会话 id: 退回时间戳前缀 (兼容)
            ts = int(time.time() * 1000)
            dest = upload_dir / f"{ts}_{safe_name}"
        content = await f.read()
        dest.write_bytes(content)
        saved_paths.append(str(dest))
        rel = dest.relative_to(SAMSEG_UPLOAD).as_posix()
        from urllib.parse import quote
        saved_files.append({
            "name": dest.name,
            "path": str(dest),
            "preview_url": f"/api/v1/upload/{quote(rel)}?preview=1",
            "source": "upload",
        })
        logger.info(f"[SamSeg] 图片已上传: {dest.name} conv={conv_prefix[:8] or '-'} ({len(content)//1024}KB)")

    if not saved_paths:
        raise HTTPException(status_code=400, detail="无有效图片文件")

    # ★ 系统只接受带坐标影像: 上传后硬拦截无 CRS 影像 (删除已落盘文件 + 400)
    bad = _reject_ungeoreferenced(saved_paths)
    if bad:
        raise HTTPException(
            status_code=400,
            detail=bad[0],  # 取第一条错误信息 (中文提示)
        )

    # ★ Phase A: 上传影像自动登记元数据 (失败不阻塞)
    #   仅对 GeoTIFF/COG 等地理影像生效 (能读出 transform/crs);
    #   普通图片 (PNG/JPG) 无地理信息则跳过.
    for p in saved_paths:
        try:
            _try_register_image_metadata(p, conv_prefix or "_anonymous")
        except Exception as e:
            logger.warning(f"[Metadata] 影像元数据登记失败 ({Path(p).name}): {e}")

    return {
        "status": "success",
        "paths": saved_paths,
        "count": len(saved_paths),
        "files": saved_files,
    }


@app.post("/api/v1/files/upload")
async def upload_general_files(
    files: list[UploadFile] = File(...),
    conversation_id: str = Form(None),
):
    """
    接收前端上传的通用资料文件, 存到 agent-files/samseg/send/{conversation_id}/, 返回绝对路径列表供 Agent 继续使用。

    入参:
        - files: 一个或多个通用文件, 允许图片/GeoJSON/JSON/PDF/Markdown/文本/表格/压缩包等。
        - conversation_id: 可选会话 ID, 用于把上传文件与当前会话目录绑定。
    方法:
        - 复用现有 samseg/send 目录作为用户输入资料区, 保持 /api/v1/upload 路径兼容。
        - 统一做文件名净化与同名防覆盖, 不在这里限制业务类型, 让上层 Agent 决定如何消费。
    出参:
        - {status, paths, count, files:[{name, path, size, content_type}]}
    """
    if not files:
        raise HTTPException(status_code=400, detail="未收到上传文件")

    import re

    conv_prefix = ""
    if conversation_id:
        conv_prefix = re.sub(r"[^a-zA-Z0-9._-]", "", conversation_id)
    conv_dir_name = conv_prefix if conv_prefix else "_anonymous"
    upload_dir = (SAMSEG_UPLOAD / conv_dir_name).resolve()
    upload_dir.mkdir(parents=True, exist_ok=True)

    saved_paths = []
    saved_files = []
    for f in files:
        if not f.filename:
            continue

        safe_name = Path(f.filename).name
        if conv_prefix:
            fname = f"{conv_prefix}_{safe_name}"
            dest = upload_dir / fname
            if dest.exists():
                suffix = _rand_suffix(4)
                stem = Path(safe_name).stem
                ext = Path(safe_name).suffix
                dest = upload_dir / f"{conv_prefix}_{stem}_{suffix}{ext}"
        else:
            dest = upload_dir / safe_name
            if dest.exists():
                suffix = _rand_suffix(4)
                stem = Path(safe_name).stem
                ext = Path(safe_name).suffix
                dest = upload_dir / f"{stem}_{suffix}{ext}"

        content = await f.read()
        dest.write_bytes(content)
        saved_paths.append(str(dest))
        saved_files.append({
            "name": safe_name,
            "path": str(dest),
            "size": len(content),
            "content_type": f.content_type or "",
        })
        logger.info(
            f"[Upload] 通用文件已上传: {dest.name} conv={conv_prefix[:8] or '-'} "
            f"({len(content) // 1024}KB, type={f.content_type or '-'})"
        )

    if not saved_paths:
        raise HTTPException(status_code=400, detail="无有效上传文件")

    # ★ 系统只接受带坐标影像: 仅校验影像类后缀 (.tif/.tiff/.cog/.png/.jpg/.jpeg/.bmp/.webp),
    #   非影像资料 (.json/.pdf/.shp 等) 放行不变; 无 CRS 影像删除已落盘文件 + 400。
    _IMAGE_EXTS = (".tif", ".tiff", ".cog", ".png", ".jpg", ".jpeg", ".bmp", ".webp")
    image_paths = [p for p in saved_paths if Path(p).suffix.lower() in _IMAGE_EXTS]
    if image_paths:
        bad = _reject_ungeoreferenced(image_paths)
        if bad:
            raise HTTPException(
                status_code=400,
                detail=bad[0],
            )

    return {
        "status": "success",
        "paths": saved_paths,
        "count": len(saved_paths),
        "files": saved_files,
    }


# ★ Shapefile 组件后缀: .shp 主文件 + .shx/.dbf 必需 + .prj/.cpg/... 可选
#   upload_shapefile 发布时按 .shp 同目录同 stem 找这些组件。
_SHAPEFILE_EXTS = (".shp", ".shx", ".dbf", ".prj", ".cpg", ".sbn", ".sbx", ".fbn", ".fbx", ".ain", ".aih", ".qix", ".sbn")


@app.post("/api/v1/files/upload-shapefile")
async def upload_shapefile_components(
    files: list[UploadFile] = File(...),
    conversation_id: str = Form(None),
):
    """
    上传 Shapefile 全部组件 (.shp/.shx/.dbf/.prj/.cpg...) 并落盘到同一目录,
    返回 .shp 在服务器的绝对路径, 供前端调 /api/v1/geoserver/layers 发布。

    ★ 为什么需要这个端点: Shapefile 是多文件组合, 浏览器单文件上传只传 .shp,
      服务器无同目录 .shx/.dbf → upload_shapefile 报 "缺少必要文件: xxx.shx"。
      本端点让前端一次性上传全部组件, 保证同 stem 同目录落盘。

    入参:
        - files: shapefile 全部组件 (必须含 .shp, 建议含 .shx/.dbf/.prj)
        - conversation_id: 可选会话 ID, 落盘到 samseg/send/{conv_id}/
    方法:
        - 按 .shp 的 stem 校验组件集合; 同一 stem 的全部组件用同一 stem 落盘
          (不各自加随机后缀, 否则 upload_shapefile 找不到同 stem 的 .shx/.dbf)
        - 同目录已存在同名 .shp 时, 整组 stem 加同一个随机后缀
    出参:
        - {status, shp_path, stem, components:[{name, path, size}], warnings:[]}
        - status != success 时前端据 msg 提示用户补齐组件
    """
    if not files:
        raise HTTPException(status_code=400, detail="未收到上传文件")

    import re

    # 1) 净化文件名 + 校验至少一个 .shp
    received = []  # [(stem_lower, ext_lower, original_name, UploadFile)]
    shp_stems = set()
    for f in files:
        if not f.filename:
            continue
        safe_name = Path(f.filename).name
        stem = Path(safe_name).stem
        ext = Path(safe_name).suffix.lower()
        if ext not in _SHAPEFILE_EXTS:
            # 忽略非 shapefile 组件 (容错)
            continue
        received.append((stem.lower(), stem, ext, safe_name, f))
        if ext == ".shp":
            shp_stems.add(stem.lower())

    if not received:
        raise HTTPException(status_code=400, detail="未收到有效的 shapefile 组件 (.shp/.shx/.dbf/...)")
    if not shp_stems:
        raise HTTPException(status_code=400, detail="缺少 .shp 主文件, 请选中完整的 shapefile 组件集")

    # 2) 校验每个 .shp 都有 .shx + .dbf (upload_shapefile 必需)
    #    按 stem 分组
    by_stem: dict[str, dict[str, tuple]] = {}
    for stem_lower, stem, ext, name, f in received:
        by_stem.setdefault(stem_lower, {"_stem": stem})[ext] = (name, f)

    warnings = []
    for stem_lower, group in by_stem.items():
        if ".shp" not in group:
            warnings.append(f"{group['_stem']}: 缺少 .shp 主文件")
            continue
        missing = [e for e in (".shx", ".dbf") if e not in group]
        if missing:
            warnings.append(f"{group['_stem']}: 缺少必需组件 {'/'.join(missing)}")

    # 3) 落盘到 samseg/send/{conv_id}/, 同 stem 组件用同一 stem
    conv_prefix = ""
    if conversation_id:
        conv_prefix = re.sub(r"[^a-zA-Z0-9._-]", "", conversation_id)
    conv_dir_name = conv_prefix if conv_prefix else "_anonymous"
    upload_dir = (SAMSEG_UPLOAD / conv_dir_name).resolve()
    upload_dir.mkdir(parents=True, exist_ok=True)

    components_out = []
    shp_path = None

    for stem_lower, group in by_stem.items():
        if ".shp" not in group:
            continue  # 缺 .shp 的组跳过 (已记 warning)
        base_stem = group["_stem"]

        # 同目录已有同名 .shp → 整组 stem 加同一随机后缀, 保持组件同 stem
        dest_shp = upload_dir / f"{base_stem}.shp"
        final_stem = base_stem
        if dest_shp.exists():
            suffix = _rand_suffix(4)
            final_stem = f"{base_stem}_{suffix}"

        for ext, (name, f) in group.items():
            if ext == "_stem":
                continue
            dest = upload_dir / f"{final_stem}{ext}"
            content = await f.read()
            dest.write_bytes(content)
            logger.info(
                f"[Upload] Shapefile 组件已上传: {dest.name} "
                f"conv={conv_prefix[:8] or '-'} ({len(content) // 1024}KB)"
            )
            components_out.append({"name": dest.name, "path": str(dest), "size": len(content)})
            if ext == ".shp":
                shp_path = str(dest)

    if not shp_path:
        raise HTTPException(
            status_code=400,
            detail="未能落盘任何 .shp 组件: " + ("; ".join(warnings) or "未知原因"),
        )

    return {
        "status": "success",
        "shp_path": shp_path,
        "stem": Path(shp_path).stem,
        "components": components_out,
        "warnings": warnings,
    }


def _reject_ungeoreferenced(image_paths: list[str]) -> list[str]:
    """
    上传端点 CRS 预检: 检测影像列表中无 CRS (无地理坐标) 的项。

    ★ 无 CRS 影像 → 删除已落盘文件 (防污染 send 目录) 并返回中文错误信息列表。
    ★ 有 CRS 或读取异常 (保守放行, 避免误伤) → 不计入返回。
    ★ 返回空列表表示全部通过; 非空表示存在无坐标影像 (调用方据此抛 400)。

    出参: [error_msg, ...] (每条含文件名 + 原因); 空列表 = 全部通过
    """
    errors = []
    try:
        from backend.model.SamSeg.geoio import assert_image_has_crs
    except ImportError as e:
        logger.warning(f"[Upload] CRS 校验模块不可用, 跳过预检: {e}")
        return errors  # 模块缺失不阻断 (降级为旧行为)

    for idx, p in enumerate(image_paths, 1):
        ok, reason = assert_image_has_crs(p)
        if not ok:
            # 删除已落盘的无 CRS 影像, 防止 send 目录堆积无效文件
            try:
                Path(p).unlink(missing_ok=True)
            except Exception:
                pass
            name = Path(p).name
            errors.append(
                f"第 {idx} 张影像 ({name}) 无地理坐标(CRS)，系统仅接受带坐标的遥感影像(GeoTIFF/COG)。原因: {reason}"
            )
            logger.warning(f"[Upload] 拒绝无 CRS 影像: {name} ({reason})")
    return errors


def _try_register_image_metadata(file_path: str, owner_hint: str = "study_user") -> None:
    """
    尝试把上传的影像文件登记到 image_metadata 表 (#4).
    - 仅 GeoTIFF/COG 等地理影像 (能读 transform/crs) 才登记
    - 普通图片 (PNG/JPG) 无地理信息 → 跳过, 不报错
    - PostGIS 可用时 bbox 转 SRID=4490, 否则存 JSONB
    """
    try:
        import rasterio
    except ImportError:
        return
    p = Path(file_path)
    ext = p.suffix.lower()
    if ext not in (".tif", ".tiff", ".cog", ".vrt"):
        return  # 非地理影像, 跳过
    try:
        with rasterio.open(file_path) as src:
            b = src.bounds
            bbox = (b.left, b.bottom, b.right, b.top)
            try:
                srid = int(src.crs.to_epsg()) if src.crs else None
            except Exception:
                srid = None
            # ★ to_epsg() 对 Web Mercator Auxiliary Sphere 等复合 CRS 常返回 None
            #   用 bounds 数值范围兜底判断: >180 必定是投影坐标 (Web Mercator=3857 最常见)
            if srid is None:
                if abs(b.left) > 180 or abs(b.bottom) > 90:
                    srid = 3857  # Web Mercator 投影
                else:
                    srid = 4490  # 经纬度
            res = float(src.res[0]) if src.res else None
            w, h, bands = src.width, src.height, src.count
        layer_name = f"upload_{p.stem}_{owner_hint[:8]}"
        business_db.register_image_metadata(
            layer_name=layer_name,
            file_path=file_path,
            bbox=bbox,
            srid=srid,
            resolution=res,
            width=w,
            height=h,
            band_count=bands,
            uploaded_by=owner_hint,
        )
    except Exception as e:
        logger.warning(f"[Metadata] 读取影像地理信息失败 ({p.name}): {e}")


def _rand_suffix(n: int = 4) -> str:
    """生成长度 n 的小写字母数字随机后缀 (防同会话同名文件覆盖)"""
    import random
    import string
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=n))


# ==================== WebSocket 端点 ====================

@app.websocket("/api/v1/agent/ws/chat")
async def websocket_chat(websocket: WebSocket, session_id: str = None):
    """
    WebSocket 全双工聊天端点

    ★ v2.1 多对话并行:
      同一 WebSocket 连接可同时运行多个对话。每条下行消息均携带
      conversation_id, 前端据此路由到对应的对话 Tab。

    消息协议:
      客户端 → 服务端:
        {type: "chat_request", prompt: "...", conversation_id: "...", ...}
        {type: "stop_chat", conversation_id: "..."}     ← 可选, 不传则停止全部
        {type: "tool_result", request_id: "...", ...}
        {type: "get_active_tasks"}                       ← 查询当前活跃对话
        {type: "pong"}

      服务端 → 客户端:
        {type: "thinking", content: "...", conversation_id: "..."}
        {type: "tool_call", tool_calls: [...], conversation_id: "..."}
        {type: "frontend_action", ..., conversation_id: "..."}
        {type: "content", content: "...", conversation_id: "..."}
        {type: "error", content: "...", conversation_id: "..."}
        {type: "done", conversation_id: "..."}
        {type: "chat_stopped", conversation_id: "..."}
        {type: "active_tasks_list", conversations: [...]}
        {type: "ping"}
    """
    ws_manager = get_ws_manager()
    sid = await ws_manager.connect(websocket, session_id)

    try:
        while True:
            data = await websocket.receive_json()
            msg_type = data.get("type", "unknown")
            logger.info(f"[WS] Received: type={msg_type}")

            if msg_type == "chat_request":
                # ★ conversation_id 贯通: 前端带则续接, 不带则新建并回传
                user_id = data.get("user_id", "study_user")
                prompt_text = data.get("prompt", "")
                conv_id = data.get("conversation_id")
                project_id = data.get("project_id")  # ★ 可选: 所属项目
                images = data.get("images") or []
                selected_layers = data.get("selected_layers") or []

                if conv_id:
                    # 续接已有会话 (agent_db 模式下校验存在性, 不存在也允许新建记录)
                    if not agent_db.get_conversation(conv_id):
                        agent_db.create_conversation(conv_id, user_id, title=prompt_text[:20], project_id=project_id)

                    # ★ v2.1: 检查该对话是否已在运行中
                    active_convs = ws_manager.get_active_conversations(sid)
                    if conv_id in active_convs:
                        await ws_manager.send_to_session(sid, {
                            "type": "error",
                            "content": f"对话 {conv_id[:8]}... 正在处理中, 请等待完成或先停止",
                            "conversation_id": conv_id,
                        })
                        continue
                else:
                    # 新建会话
                    conv_id = str(uuid.uuid4())
                    agent_db.create_conversation(conv_id, user_id, title=prompt_text[:20], project_id=project_id)

                # ★ 回传 conversation_id 让前端保存 (前端在 session_started 事件里接住)
                await ws_manager.send_to_session(sid, {
                    "type": "session_started",
                    "conversation_id": conv_id,
                })

                req = ToolChatRequest(
                    prompt=prompt_text,
                    model=data.get("model", settings.dashscope_model),
                    temperature=data.get("temperature", settings.default_temperature),
                    conversation_id=conv_id,
                    user_id=user_id,
                    project_id=project_id,  # ★ 所属分组 (沙盒 work_dir 来源)
                    images=images,
                    selected_layers=selected_layers,
                    multi_round=data.get("multi_round", True),
                )

                # ★ 记录 checkpoint (本轮开始前的最后一条消息 id, 供停止回滚用)
                checkpoint_id = agent_db.get_last_message_id(conv_id)

                # ★ v2.1: 在后台任务中执行 Agent 推理, 注册 task (带 conversation_id) 以供取消
                task = asyncio.create_task(
                    chat_service.tool_chat_ws(req, sid, ws_manager, checkpoint_id=checkpoint_id)
                )
                ws_manager.register_task(sid, conv_id, task)
                logger.info(f"[WS] Task started: session={sid}, conv={conv_id}, "
                           f"active_count={ws_manager.get_task_count(sid)}")

            elif msg_type == "stop_chat":
                # ★ v2.1: 支持按 conversation_id 精确停止, 不传则停止全部
                conv_id = data.get("conversation_id")
                if conv_id:
                    cancelled = ws_manager.cancel_task(sid, conversation_id=conv_id)
                    logger.info(f"[WS] stop_chat: conv={conv_id}, cancelled={cancelled}")
                    # 停止确认必须与模型线程清理解耦，先恢复前端交互，再由取消分支完成回滚和资源收尾。
                    await ws_manager.send_to_session(sid, {
                        "type": "chat_stopped",
                        "conversation_id": conv_id,
                        "cancelled": cancelled,
                    })
                else:
                    # 停止该 session 下所有活跃对话
                    cancelled = ws_manager.cancel_task(sid)
                    logger.info(f"[WS] stop_chat (all): cancelled={cancelled}, sid={sid}")
                    if cancelled == 0:
                        await ws_manager.send_to_session(sid, {
                            "type": "chat_stopped",
                            "session_id": sid,
                        })

            elif msg_type == "get_active_tasks":
                # ★ v2.1 新增: 前端查询当前 session 下有哪些对话在后台运行
                active_convs = ws_manager.get_active_conversations(sid)
                await ws_manager.send_to_session(sid, {
                    "type": "active_tasks_list",
                    "conversations": active_convs,
                    "count": len(active_convs),
                })

            elif msg_type == "tool_result":
                # ★ 前端工具执行结果回传 → 解除 wait_for_frontend_result 阻塞
                await ws_manager.handle_client_message(sid, data)

            elif msg_type == "pong":
                pass

    except WebSocketDisconnect:
        ws_manager.disconnect(sid)
        logger.info(f"[WS] Disconnected: {sid}")


# ==================== 前端诊断日志（写入根目录 wms-debug.log）====================
@app.post("/api/v1/debug/log")
async def debug_log(request: Request):
    """前端诊断日志 → 追加写入项目根目录 wms-debug.log（排查地图渲染，用完可删）"""
    import json
    from datetime import datetime
    try:
        body = await request.json()
        msg = str(body.get("msg", ""))
    except Exception:
        msg = ""
    with open(ROOT_DIR / "wms-debug.log", "a", encoding="utf-8") as f:
        f.write(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}\n")
    return {"status": "ok"}


# ==================== 启动入口 ====================


if __name__ == "__main__":
    import uvicorn
    # ★ 热加载配置:
    #   - reload_dirs=["backend"]: 明确只监听 backend/ 下的 .py 变化, 避免误触 node_modules 等。
    #   - reload_delay=1.0: Windows 下文件写入有延迟, 等 1s 确保写完再重载 (防半写状态导入失败)。
    #   - reload_includes=["*.py"]: 只关心 .py 文件。
    backend_dir = str(Path(__file__).resolve().parent)
    uvicorn.run(
        "backend.main:app",
        host="0.0.0.0",
        port=settings.agent_service_port,
        reload=True,
        reload_dirs=[backend_dir],
        reload_includes=["*.py"],
        reload_delay=1.0,
    )
