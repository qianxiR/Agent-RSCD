"""
沙盒文件工具 (Model 层 / 工具箱)
- render_sandbox_image: 沙盒产物图片 → 前端渲染
- import_file_to_sandbox: 本地文件 → 复制进沙盒工作区 → 返回容器内路径

★ 解决的问题 (AI 认知修正):
  之前 AI 误以为"无法访问本地文件", 强制要求用户手动上传。但实际上:
    - 宿主机后端 (Python) 直接跑在用户的操作系统上, 能读各盘符下的本地文件
    - 沙盒容器 (Linux Docker) 才是隔离的, 只能看到挂载的 /workspace
  本工具由宿主机后端执行: 把本地文件复制进沙盒工作区, 让 AI 拥有"操作本地文件"的能力。

★ 路径映射关系 (三者必须一致):
    容器内视角: /workspace/{filename}                  ← LLM 在代码里 savefig 的路径
    宿主机实际: agent-files/sandbox/workspace/{pid}/{cid}/{filename}  ← resolve_work_dir 返回
    前端 URL:   /api/v1/download/sandbox/workspace/{pid}/{cid}/{filename}
                          ↑ 走通用下载端点 (main.py), 复用现有 HTTP 通道

依赖方向: model.tools → data, 严禁反向依赖 agent 层。
"""
import logging
import time
from pathlib import Path
from typing import Dict, Any

from langchain_core.tools import tool

from backend.model.tools.tool_registry import register_tool
from backend.model.tools.sandbox_manager import sandbox_manager
from backend.model.tools._paths import session_subdirs
from backend.model.tools._result import build_success, build_error, build_chat_image_action
from backend.config import settings

logger = logging.getLogger(__name__)


def _runtime_ids() -> tuple:
    """
    从运行时上下文获取 (project_id, conversation_id)。
    v2.4: 复用 _paths.session_subdirs (统一兜底常量 _default/_anonymous)。
    """
    return session_subdirs()


@register_tool("sandbox")
@tool
def render_sandbox_image(filename: str, caption: str = "") -> Dict[str, Any]:
    """
    将沙盒中生成的图片渲染到前端工作区面板 (右侧影像预览)。

    ★ 触发场景: 通过 run_python_code 用 matplotlib / Pillow / seaborn 等库生成的
      图片 (PNG/JPG/SVG), 需要展示给用户时调用本工具。

    ★★ 严禁: 不要用 toggle_layer_visibility / load_geoserver_layer 来展示沙盒图片。
      它们只用于 GeoServer 图层, 会把文件路径当成图层名查询 GeoServer 并报错。

    入参:
        - filename (str): 沙盒工作区内的文件名或相对路径
          (如 "dimaotype_colorbar.png" 或 "output/chart.png")。
          ★ 不要传 "/workspace/..." 前缀或绝对路径, 那是容器内路径;
            传文件名或相对工作区根的路径即可。
        - caption (str): 图片标题/说明, 留空则用文件名 (不含扩展名)。
    出参: frontend_action(render_image) 指令, 前端右侧面板展示该图。
    """
    # 1) 清洗: 去掉容器内路径前缀, 只保留相对工作区根的部分
    fname = (filename or "").strip().lstrip("/")
    for prefix in ("workspace/", "/workspace/"):
        if fname.startswith(prefix):
            fname = fname[len(prefix):]
            break
    if not fname:
        return {"type": "error", "msg": "filename 不能为空"}

    # 2) 解析当前会话的宿主机工作区路径 (含 project_id/{conv_id} 两层)
    project_id, conv_id = _runtime_ids()
    work_dir = sandbox_manager.resolve_work_dir(project_id, conv_id)
    local_path = (Path(work_dir) / fname).resolve()

    if not local_path.is_file():
        return {
            "type": "error",
            "msg": (
                f"沙盒中未找到文件: {filename} "
                f"(宿主机期望路径: {local_path})。"
                f"请确认代码已将文件保存到工作区根目录 (容器内 /workspace/), "
                f"且文件名/路径拼写正确。"
            ),
        }

    # 3) 防路径越权: 文件必须在统一存储根 agent-files/ 之下 (复用通用下载端点的安全边界)
    storage_root = Path(settings.file_storage_root).resolve()
    try:
        rel = local_path.relative_to(storage_root).as_posix()
    except ValueError:
        return {
            "type": "error",
            "msg": f"文件不在可访问的存储区内: {local_path}",
        }

    # 4) 拼前端可访问 URL (走通用下载端点 /api/v1/download/{path}),
    #    加时间戳防浏览器缓存 (避免同名新图被缓存成旧图)
    image_url = f"/api/v1/download/{rel}?t={int(time.time() * 1000)}"
    cap = caption.strip() or local_path.stem

    return build_chat_image_action(
        image_url=image_url,
        caption=cap,
        summary=(
            f"已将沙盒生成的图片 {local_path.name} 渲染到聊天窗口。\n"
            f"本地保存路径: {local_path}\n"
            f"请在回复中告知用户上述本地保存路径, 便于其在文件管理器中查看。"
        ),
        description=f"正在渲染沙盒图片: {local_path.name}",
        data={
            "local_path": str(local_path),
            "filename": local_path.name,
            "image_url": image_url,
        },
    )


@register_tool("sandbox")
@tool
def import_file_to_sandbox(file_path: str) -> Dict[str, Any]:
    """
    把用户本地文件复制到沙盒工作区, 让后续沙盒代码能读取它。

    ★ 适用场景: 用户给出本地文件路径 (如 "G:/arcgis/成都/成都市地貌类型.tif"),
      需要在沙盒代码 (run_python_code) 里处理该文件时, 先用本工具把文件搬进沙盒。
      搬入后, 沙盒代码里用返回的容器内路径 (如 /workspace/成都市地貌类型.tif) 即可读取。

    ★ 本工具由宿主机后端执行 (直接跑在用户操作系统上), 能读 Windows 任意盘符的本地文件,
      不受沙盒容器隔离限制。复制后文件在宿主机和容器内都可见 (通过 bind mount 同步)。

    入参:
        - file_path (str): 用户本地文件的绝对路径。
          支持 Windows (如 "G:/arcgis/成都/x.tif" 或 "G:\\arcgis\\x.tif")
          和 Unix 路径。中文路径和文件名都支持。

    出参: {type:"success", summary, data:{artifact_path/sandbox_path, container_path, original_path, filename, size}}
        - artifact_path/sandbox_path: 宿主机侧的沙盒工作区路径 (供 upload_raster_layer 等宿主机工具用)
        - container_path: 容器内路径 /workspace/{filename} (供沙盒代码用)
      失败时 {type:"error", msg}
    """
    import shutil
    import platform

    src = Path(file_path).expanduser()
    if not src.is_file():
        return build_error(
            msg=(
                f"本地文件不存在: {file_path}。"
                f"请确认路径正确 (当前系统: {platform.system()}, 路径区分大小写)。"
            ),
        )

    # 解析当前会话的沙盒工作区 (含 project_id/{conv_id} 两层)
    project_id, conv_id = _runtime_ids()
    work_dir = Path(sandbox_manager.resolve_work_dir(project_id, conv_id))
    work_dir.mkdir(parents=True, exist_ok=True)

    # 目标文件名: 只取 basename, 防路径穿越; 保留中文原名 (沙盒容器支持 UTF-8 文件名)
    dest_name = src.name
    dest = work_dir / dest_name

    # 同名文件覆盖 (用户重新导入最新版本)
    file_size = src.stat().st_size
    try:
        shutil.copy2(src, dest)
    except Exception as e:
        return build_error(msg=f"复制文件失败: {e}")

    logger.info(
        f"[import_file_to_sandbox] {src} -> {dest} ({file_size / 1024:.1f}KB) "
        f"pid={project_id or '-'} cid={conv_id[:8] or '-'}"
    )

    # ★ v2.5: 用统一 schema (artifact_path 规范化命名)
    return build_success(
        summary=(
            f"已把本地文件 {dest_name} 导入沙盒 ({file_size // 1024}KB)。\n"
            f"- 在沙盒代码 (run_python_code) 里用容器路径: /workspace/{dest_name}\n"
            f"- 在 upload_raster_layer 等宿主机工具里用本地路径: {dest}\n"
            f"  (注: upload_raster_layer 也接受原始本地路径 {src}, 无需先导入沙盒)"
        ),
        data={
            "artifact_path": str(dest),                      # 宿主机侧 (规范命名, 兼容旧 sandbox_path)
            "sandbox_path": str(dest),                       # 旧字段保留 (向后兼容)
            "container_path": f"/workspace/{dest_name}",     # 容器内 (供 run_python_code 代码)
            "original_path": str(src),
            "filename": dest_name,
            "size": file_size,
        },
    )
