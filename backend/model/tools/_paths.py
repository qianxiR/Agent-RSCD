"""
统一文件存储路径/命名 helper (v2.4)

历史问题:
  - 6 个工具文件各自复制粘贴了一份 _runtime_dirs/_get_runtime_ids/_conv_id_full/
    _project_id_safe 实现, 导致双层 UUID 目录层级顺序不一致 (samseg/geoserver/
    report/analysis/preprocess 是 {conv}/{proj}, sandbox 是 {proj}/{conv})。
  - 文件名越叠越长 (report/export 用上游长 stem 当新名前缀, 时间戳层层叠加)。

本模块集中定义所有会话级路径/命名逻辑, 一处定义全局生效, 根治层级不一致。

★ 统一目录顺序: {root}/{project_id}/{conversation_id}/  (项目在外, 会话在内)
★ 统一兜底常量: 项目无 -> "_default"; 会话无 -> "_anonymous"
★ 文件名只保留 {会话前缀8位}_{语义}_{4位随机}, 不再吃上游 stem 也不带时间戳。
"""
import random
import re
import string
from pathlib import Path
from typing import Tuple

# ==================== 兜底常量 (统一全模块) ====================
PROJECT_FALLBACK = "_default"       # 无项目 id 时的目录名
CONVERSATION_FALLBACK = "_anonymous"  # 无会话 id 时的目录名


def safe_id(raw: str, fallback: str = "") -> str:
    """
    把任意 id 清洗成文件名/目录名安全串。
    - 保留字母数字 . _ -
    - 其余字符 (含中文/特殊符号/路径分隔符) 全部丢弃
    - 清洗后为空 → 返回 fallback
    """
    s = re.sub(r"[^a-zA-Z0-9._-]", "", raw or "")
    return s or fallback


def _read_context() -> Tuple[str, str]:
    """从运行时上下文取 (project_id, conversation_id), 取不到返回空串。"""
    try:
        from backend.agent.runtime.context_vars import (
            get_current_conversation_id, get_current_project_id,
        )
        return get_current_project_id() or "", get_current_conversation_id() or ""
    except Exception:
        return "", ""


# 目录名 ID 短化长度: UUID 前 8 位足以区分会话, 避免目录路径过长。
# ★ 注意: 只用于"目录名/文件名/容器名"层, DB 主键/WS 路由/消息归属仍用完整 UUID 不变。
ID_SHORT_LEN = 8


def session_subdirs() -> Tuple[str, str]:
    """
    返回当前会话的 (project_id_safe, conversation_id_safe), 已清洗+兜底+短化。
    - 无上下文时返回 ("_default", "_anonymous")。
    - ★ 短化: 仅取 UUID 前 8 位作目录名 (DB 主键不受影响, 仍是完整 UUID)。
      例: "449148e8-d35c-468c-9c0e-b45b53e1ed5b" -> "449148e8"
    - 兜底常量 ("_default"/"_anonymous") 不截断, 保持语义完整。
    """
    pid_raw, cid_raw = _read_context()
    proj_full = safe_id(pid_raw, fallback=PROJECT_FALLBACK)
    conv_full = safe_id(cid_raw, fallback=CONVERSATION_FALLBACK)
    # 兜底常量原样保留, 真 UUID 截断到 8 位
    proj = proj_full if proj_full == PROJECT_FALLBACK else proj_full[:ID_SHORT_LEN]
    conv = conv_full if conv_full == CONVERSATION_FALLBACK else conv_full[:ID_SHORT_LEN]
    return proj, conv


def session_subpath() -> Path:
    """
    返回会话级子目录相对路径 (项目在外, 会话在内)。
    形如 Path("_default/abc12345"), 拼到任何 root 之后即得到完整目录。
    ★ 会话目录名取 UUID 前 8 位 (见 session_subdirs)。
    """
    proj, conv = session_subdirs()
    return Path(proj) / conv


def short_conv() -> str:
    """
    取会话 id 前 8 位 (UUID 前缀足以区分, 避免文件名过长)。
    无会话时返回空串, 由调用方决定兜底 (一般用 "anon")。
    """
    pid_raw, cid_raw = _read_context()
    return safe_id(cid_raw)[:ID_SHORT_LEN]


def rand_suffix(n: int = 4) -> str:
    """
    生成长度 n 的小写字母数字随机后缀。
    用途: 防同会话同类同名文件互相覆盖 (segment/detect_change/report/export 复用)。
    合并了原 main.py 和 samseg_tools.py 两份重复实现。
    """
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=n))


def build_download_url(file_path: Path, storage_root: Path) -> str:
    """
    把任意磁盘绝对路径转成前端可访问的 /api/v1/download/{rel} URL。
    - 入参: file_path (绝对路径), storage_root (file_storage_root)
    - 出参: "/api/v1/download/{相对posix路径}"
    - 校验: file_path 必须落在 storage_root 之下, 否则返回空串 (防路径越权)

    合并了 report_tools._build_download_url 和 sandbox_render_tools 里的反算逻辑。
    """
    try:
        rel = file_path.resolve().relative_to(storage_root.resolve())
        return "/api/v1/download/" + rel.as_posix()
    except (ValueError, OSError):
        return ""
