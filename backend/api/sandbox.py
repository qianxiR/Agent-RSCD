"""沙盒文件导出路由 (/api/v1/sandbox)。"""
from fastapi import APIRouter

from backend.api.schemas import SandboxDownloadRequest

router = APIRouter()


@router.post("/api/v1/sandbox/download-to-host")
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
