"""
沙盒文件导出工具 (Model 层 / 工具箱)
- 入参: 沙盒内文件路径 + 宿主机目标目录
- 方法: 把沙盒可访问的文件复制到用户指定的宿主机任意目录
- 出参: {type, summary, data:{dest_path, size_bytes}, msg(失败时)}

★ 核心价值: 让 Agent 能把沙盒里分析出的结果 (或产物挂载点的文件) "下载"到
  用户指定的宿主机文件夹 (如 E:\\out、G:/results 等任意绝对路径), 满足
  "用户希望结果落到自己指定的目录"的需求。

★ 由宿主机后端执行 (非沙盒内 cp): 沙盒是 Linux 容器无法直接写 Windows E:/G: 盘符,
  本工具在宿主机 Python 进程中做路径解析 + shutil.copy2, 沿用 import_file_to_sandbox
  已验证的"宿主机执行复制"模式。

★ 工具自动收录: @register_tool("sandbox") → _build_tools_catalog() 自动列入工具目录,
  bind_tools 自动绑定, 无需手改 prompt 工具目录。
"""
import logging
import shutil
from pathlib import Path
from typing import Dict, Any

from langchain_core.tools import tool

from backend.config import settings
from backend.model.tools.tool_registry import register_tool
from backend.model.tools._result import build_success, build_error

logger = logging.getLogger(__name__)


def _resolve_runtime_ids():
    """
    从运行时上下文取 (project_id, conversation_id, work_dir)。
    延迟导入避免循环依赖 (context_vars 在 agent 层, 本模块在 model 层)。
    出参: (project_id, conversation_id, work_dir); 无上下文时 conversation_id="_anonymous"。
    """
    from backend.agent.runtime.context_vars import (
        get_current_conversation_id, get_current_project_id,
    )
    from backend.model.tools.sandbox_manager import sandbox_manager
    conversation_id = get_current_conversation_id() or "_anonymous"
    project_id = get_current_project_id() or ""
    work_dir = sandbox_manager.resolve_work_dir(project_id, conversation_id)
    return project_id, conversation_id, work_dir


def export_to_host(file_path: str, dest_dir: str) -> Dict[str, Any]:
    """
    把沙盒可访问的文件复制到用户指定的宿主机任意目录。
    ★ 工具 download_file_from_sandbox 与 HTTP 端点 /sandbox/download-to-host 共用此函数。

    入参:
        - file_path: 容器内路径或 agent-files 相对路径, 三种写法:
            ① /workspace/xxx                → work_dir/xxx
            ② /files/{source_下划线}/xxx     → file_storage_root/{source还原}/{proj8}/{conv8}/xxx
            ③ agent-files/... 或 {source}/... 相对 → file_storage_root/...
        - dest_dir: 宿主机目标文件夹绝对路径 (E:\\out 或 G:/results)。
    方法:
        1. resolve_container_path_to_host 解析沙盒路径 → 宿主机绝对路径
        2. 校验源文件存在 + 必须落在 file_storage_root 内 (防越权读沙盒外文件)
        3. dest_dir 自动创建 (parents=True), shutil.copy2 保留元数据, 同名覆盖
    出参:
        - 成功: {ok:True, dest_path, size_bytes}
        - 失败: {ok:False, msg}
    """
    from backend.model.tools.sandbox_manager import sandbox_manager
    project_id, conversation_id, work_dir = _resolve_runtime_ids()

    # 1. 解析沙盒路径 → 宿主机绝对路径
    host_path = sandbox_manager.resolve_container_path_to_host(
        file_path, project_id, conversation_id, work_dir
    )
    if not host_path:
        return {"ok": False, "msg": f"无法解析路径: {file_path} (不在沙盒可访问范围内)"}

    src = Path(host_path).resolve()

    # 2. 校验源文件
    if not src.exists():
        return {"ok": False, "msg": f"源文件不存在: {src}"}
    if not src.is_file():
        return {"ok": False, "msg": f"源路径不是文件: {src}"}

    # 防越权: 源必须在 file_storage_root 内 (防止读沙盒外宿主机任意文件)
    storage_root = Path(settings.file_storage_root).resolve()
    try:
        src.relative_to(storage_root)
    except ValueError:
        return {"ok": False, "msg": f"源文件越界 (必须在 agent-files 内): {src}"}

    # 3. 处理目标目录 (任意绝对路径, 不在 agent-files 内)
    if not dest_dir or not dest_dir.strip():
        return {"ok": False, "msg": "目标目录不能为空"}
    dest = Path(dest_dir).expanduser()
    try:
        dest.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        return {"ok": False, "msg": f"目标目录创建失败: {dest} ({e!r})"}

    dest_path = dest / src.name

    # 4. 复制 (copy2 保留元数据, 同名覆盖)
    try:
        shutil.copy2(src, dest_path)
    except Exception as e:
        return {"ok": False, "msg": f"复制失败: {src} -> {dest_path} ({e!r})"}

    size = dest_path.stat().st_size if dest_path.exists() else 0
    logger.info(f"[Sandbox] 导出成功: {src.name} -> {dest_path} ({size} bytes)")
    return {"ok": True, "dest_path": str(dest_path), "size_bytes": size}


@register_tool("sandbox")
@tool
async def download_file_from_sandbox(file_path: str, dest_dir: str) -> Dict[str, Any]:
    """
    把沙盒可访问的文件下载（复制）到用户指定的宿主机任意目录。

    适用场景:
    - 把沙盒里分析出的结果（matplotlib 画图、统计 CSV 等）保存到用户指定文件夹
    - 把产物挂载点的分析结果（分割/变化检测 TIF 等）导出到用户指定文件夹
    - 用户希望结果落到自定义目录（如 E:\\out、G:/results）而非 agent-files 默认位置

    ★ 由宿主机后端执行（非沙盒内 cp），可写 Windows 任意盘符。

    Args:
        file_path: 文件路径，支持三种写法：
            ① /workspace/xxx.png             (沙盒写区, 如 agent 画的图)
            ② /files/samseg_generate/x.tif   (产物挂载点, 见 data.path_map)
            ③ agent-files 相对路径 samseg/generate/.../x.tif
            具体可用路径见 run_python_code/run_shell_command 返回的 data.path_map。
        dest_dir: 宿主机目标文件夹绝对路径（如 E:\\out 或 G:/results）。
                  允许任意绝对路径；不存在自动创建；同名覆盖。
    """
    result = export_to_host(file_path, dest_dir)
    if not result.get("ok"):
        return build_error(msg=result.get("msg", "导出失败"))
    data = {
        "dest_path": result["dest_path"],
        "size_bytes": result["size_bytes"],
    }
    summary = f"已下载到 {result['dest_path']} ({result['size_bytes']} 字节)"
    return build_success(summary=summary, data=data)
