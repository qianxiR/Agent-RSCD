"""工作区文件夹浏览路由 (/api/v1/workspace)。"""
import os

from fastapi import APIRouter

router = APIRouter()

# 系统目录黑名单 (Windows)
_SYSTEM_PREFIXES = [
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


@router.get("/api/v1/workspace/list")
async def list_workspace(roots: str = "", depth: int = 3):
    """
    列出指定根目录下的子文件夹 (递归到 depth 层)。
    ★ 安全: 规范化路径 + 拒绝 .. 穿越 + 拒绝系统目录 + 不可读返回空。
    入参: roots (逗号分隔的绝对路径列表), depth (递归深度, 默认3)
    出参: { roots: [{ path, name, children: [{ path, name, children: [...] }] }] }
    """
    if not roots:
        return {"roots": []}

    depth = max(1, min(depth, 5))  # 限制 1-5 层
    root_paths = [r.strip() for r in roots.split(",") if r.strip()]
    result = []

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
            for sp in _SYSTEM_PREFIXES:
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
