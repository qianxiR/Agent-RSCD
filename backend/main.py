"""
FastAPI 入口 - Agent 服务
- 入参: HTTP 请求 / WebSocket 连接
- 方法: 注册路由、中间件、启动事件
- 出参: SSE 流式响应 / WebSocket 双向通道

★ v2.1 多对话并行:
  - 同一 WebSocket 连接可同时运行多个对话的 Agent 推理
  - stop_chat 支持按 conversation_id 精确停止
  - 新增 get_active_tasks 查询当前活跃对话列表

★ 架构 (2026-08 重构): 路由按业务域拆到 backend/api/*.py (APIRouter),
  本文件只保留 app 实例化 + lifespan 生命周期 + 路由挂载 + 全局异常处理。

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
import logging
from contextlib import asynccontextmanager
from pathlib import Path

# 将项目根目录加入 sys.path, 使 from xxx 导入正常工作
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from backend.config import settings
from backend.utils.async_subprocess import run_subprocess

# ★ 导入工具模块, 触发 @register_tool 装饰器执行, 将工具注册到全局注册表
#   model.tools: GeoServer/数据库/图层/遥感工具 (工具箱, 主体)
#   agent.tools: 记忆工具 (跟随记忆留在 agent 层)
import backend.model.tools  # noqa: F401
import backend.agent.tools  # noqa: F401

# 路由层 (api/__init__.py 触发 deps.py 创建存储目录 + 解析路径常量)
# ★ 双服务架构 (2026-10): projects/workspace 已迁 Java 业务服务 (8081),
#   conversations/tasks 由 Java 代理回本服务的 /internal/*; 本服务只保留
#   AI 相关路由 + 内部接口。
from backend.api import (  # noqa: E402
    conversations, files, geoserver, internal, mcp, sandbox, skills, system, tasks, ws,
)
from backend.api.deps import (  # noqa: E402
    FILE_ROOT, SAMSEG_UPLOAD, SAMSEG_OUTPUT, GEOSERVER_DL, SANDBOX_ROOT,
    agent_db, business_db,
)
from backend.agent.chat_service import AgentChatService  # noqa: E402

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# ==================== 生命周期 (lifespan, 替代废弃的 on_event) ====================

@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期: 启动初始化 + 停止清理。"""
    # ---------- startup ----------
    _init_schema()
    _log_storage_paths()
    await _diagnose_sandbox()

    yield

    # ---------- shutdown ----------
    # 显式归还连接池, 避免连接泄漏 (原 on_event 无 shutdown, 此处补齐)。
    _close_pools()


def _init_schema() -> None:
    """启动时初始化 agent_db / business_db schema (不可用则只 warning, 不阻塞)。"""
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


def _log_storage_paths() -> None:
    """★ v2.3: 打印统一文件存储路径 (按来源维度)。"""
    logger.info(f"[Storage] 文件根目录: {FILE_ROOT}")
    logger.info(f"[Storage]   samseg/send:     {SAMSEG_UPLOAD}")
    logger.info(f"[Storage]   samseg/generate: {SAMSEG_OUTPUT}")
    logger.info(f"[Storage]   geoserver/generate: {GEOSERVER_DL}")
    logger.info(f"[Storage]   sandbox/workspace:  {SANDBOX_ROOT}")


async def _diagnose_sandbox() -> None:
    """
    ★ 沙盒状态检查 (启动时一次性诊断, 不阻塞服务启动):
      检查 3 级: 配置开关 → Docker daemon → 沙盒镜像。
      不可用时只 warning, sandbox_manager.is_available() 会在运行时重新检测
      (成功时缓存, 失败时不缓存, 允许后续恢复)。
    """
    if not settings.sandbox_enabled:
        logger.info("[Sandbox] 沙盒已禁用 (SANDBOX_ENABLED=false)")
        return

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
            return
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


def _close_pools() -> None:
    """停止时归还连接池 (best-effort, 失败仅日志)。"""
    try:
        agent_db.close_pool()
    except Exception as e:
        logger.warning(f"[Shutdown] agent_db 连接池关闭异常: {e}")
    try:
        business_db.close_pool()
    except Exception as e:
        logger.warning(f"[Shutdown] business_db 连接池关闭异常: {e}")


# ==================== app 实例化 + 中间件 + 路由挂载 ====================

app = FastAPI(
    title="国土智察-自然资源遥感智能监测系统",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS - 允许前端跨域访问
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ★ 全局异常处理: 统一未捕获异常的响应格式 (原 main.py 无此层, 各路由 try/except 风格不一)。
@app.exception_handler(Exception)
async def _unhandled_exception_handler(request: Request, exc: Exception):
    logger.exception(f"[Unhandled] {request.method} {request.url.path}: {exc}")
    return JSONResponse(
        status_code=500,
        content={"status": "error", "msg": f"服务器内部错误: {exc}"},
    )

# Agent 核心服务单例 (唯一在入口实例化的 service, 注入到 ws router)
chat_service = AgentChatService()
ws.set_chat_service(chat_service)

# 挂载全部业务路由 (projects/workspace 由 Java 服务承载, 不再挂载;
# conversations/tasks 保留对外端点作为迁移期回滚通道, 默认流量经 Java 代理)
for _mod in (conversations, files, geoserver, sandbox, system, tasks, ws, internal, skills, mcp):
    app.include_router(_mod.router)


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
