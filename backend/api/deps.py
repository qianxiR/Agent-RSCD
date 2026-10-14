"""
路由层共享依赖与存储路径常量。

从 ``backend.config`` 解析统一文件存储根目录, 并集中暴露各业务模块单例,
避免每个 router 文件重复 import。路径常量在 import 阶段创建目录 (与原 main.py 行为一致)。
"""
from pathlib import Path

from backend.config import settings

# ★ v2.3 统一文件存储 (按来源维度): 解析后的绝对路径
FILE_ROOT = Path(settings.file_storage_root).resolve()
SAMSEG_UPLOAD = Path(settings.samseg_upload_root).resolve()
SAMSEG_OUTPUT = Path(settings.samseg_output_root).resolve()
GEOSERVER_DL = Path(settings.geoserver_download_root).resolve()
SANDBOX_ROOT = Path(settings.sandbox_workspace_dir).resolve()
for _d in [FILE_ROOT, SAMSEG_UPLOAD, SAMSEG_OUTPUT, GEOSERVER_DL, SANDBOX_ROOT]:
    _d.mkdir(parents=True, exist_ok=True)

# 业务模块单例 (与原 main.py 顶层 import 一致)
from backend.agent.memory import agent_db  # noqa: E402
from backend.data import business_db  # noqa: E402
from backend.data import geoserver_client  # noqa: E402
from backend.model.tools.sandbox_manager import sandbox_manager  # noqa: E402

__all__ = [
    "FILE_ROOT",
    "SAMSEG_UPLOAD",
    "SAMSEG_OUTPUT",
    "GEOSERVER_DL",
    "SANDBOX_ROOT",
    "agent_db",
    "business_db",
    "geoserver_client",
    "sandbox_manager",
]
