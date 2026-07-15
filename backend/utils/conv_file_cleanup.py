"""
会话级本地缓存文件清理 (v2.4 统一存储配套)

职责:
  删除对话时, 连带删除 agent-files/ 下按该会话 ID 组织的全部产物目录,
  避免 "DB 记录删了但磁盘文件残留" 的不一致。

目录约定 (见 config.py 注释 + _paths.py 短化规则):
  - 单层: agent-files/samseg/send/{conv完整UUID}/      → 用户上传原图
  - 双层: agent-files/{source}/{proj短8位}/{conv短8位}/ → 业务产物
          source ∈ {samseg/generate, samseg/vector, geoserver/generate,
                    report, analysis, preprocess, sandbox/workspace}

★ 短化规则复用 _paths.py (safe_id 清洗 + UUID 前 8 位 + _default/_anonymous 兜底),
  保证删除时的目录名计算与写入时完全一致, 不删错也不删漏。
"""
import logging
import shutil
from pathlib import Path

from backend.config import settings
from backend.model.tools._paths import (
    safe_id, PROJECT_FALLBACK, CONVERSATION_FALLBACK, ID_SHORT_LEN,
)

logger = logging.getLogger(__name__)

# 单层目录 source: 会话目录用完整 UUID (与 main.py 上传命名一致, 不截断)
_SINGLE_LAYER_SOURCES = ("samseg/send",)

# 双层目录 source: 项目在外 / 会话在内, 均取 UUID 前 8 位
_DOUBLE_LAYER_SOURCES = (
    "samseg/generate",
    "samseg/vector",
    "geoserver/generate",
    "report",
    "analysis",
    "preprocess",
    "sandbox/workspace",
)


def _short_pair(project_id: str, conversation_id: str):
    """
    入参:
      - project_id (str): 会话所属项目 ID (删 DB 前已查到, 可空)
      - conversation_id (str): 完整会话 UUID
    方法:
      复用 _paths 短化规则: safe_id 清洗 → 兜底常量原样保留 / 真 UUID 取前 8 位。
      算法与 samseg/geoserver/report 等工具写入目录名时完全对齐。
    出参:
      (proj_short, conv_short) 两个目录名安全串。
    """
    proj_full = safe_id(project_id or "", fallback=PROJECT_FALLBACK)
    conv_full = safe_id(conversation_id or "", fallback=CONVERSATION_FALLBACK)
    # 兜底常量保持语义完整不截断, 真 UUID 截断到前 8 位
    proj = proj_full if proj_full == PROJECT_FALLBACK else proj_full[:ID_SHORT_LEN]
    conv = conv_full if conv_full == CONVERSATION_FALLBACK else conv_full[:ID_SHORT_LEN]
    return proj, conv


def _safe_rmtree(target: Path, storage_root: Path) -> bool:
    """
    入参:
      - target (Path): 待删的会话级目录 (绝对路径)
      - storage_root (Path): file_storage_root, 越权校验基准
    方法:
      先做越权校验 (resolve 后必须落在 storage_root 内), 通过后 rmtree。
      ★ best-effort: 单目录失败仅记日志返回 False 不抛异常,
        保证一个产物删不掉 (如沙盒容器仍占用 workspace) 不影响其它产物删除。
    出参:
      True=已删或本就不存在; False=删除失败或越权 (已记日志)。
    """
    if not target.exists():
        return True
    # 越权防线: 目标必须落在统一存储根下, 杜绝 ../ 逃逸误删根目录以外内容
    try:
        target.resolve().relative_to(storage_root.resolve())
    except ValueError:
        logger.warning(f"[Cleanup] 越权拒绝删除 (不在 file_storage_root 下): {target}")
        return False
    try:
        shutil.rmtree(target)
        return True
    except Exception as e:
        logger.warning(f"[Cleanup] 删除失败 (可能被占用或权限不足): {target} -> {e}")
        return False


def cleanup_conversation_files(conversation_id: str, project_id: str = "") -> dict:
    """
    入参:
      - conversation_id (str): 完整会话 UUID (路由参数)
      - project_id (str): 会话所属项目 ID, 双层目录定位用 (可空, 空走 _default 兜底)
    方法:
      1. 按 _paths 规则算出 (proj_short, conv_short) 与 send 用的完整 conv 名;
      2. 单层 send 目录: {root}/samseg/send/{conv完整}/;
      3. 双层产物目录: 遍历 7 个 source 拼 {root}/{source}/{proj_short}/{conv_short}/;
      4. 逐个 _safe_rmtree (越权校验 + best-effort)。
    出参:
      {"removed": [...已删相对路径], "skipped": [...删不掉/越权/不存在的相对路径]}
      全程不抛异常, 供 fire-and-forget 异步任务安全调用。
    """
    if not conversation_id:
        return {"removed": [], "skipped": []}

    root = Path(settings.file_storage_root).resolve()
    proj_short, conv_short = _short_pair(project_id, conversation_id)
    # send 目录用完整会话 ID (清洗后不截断), 与 main.py 上传命名一致
    conv_full_name = safe_id(conversation_id, fallback=CONVERSATION_FALLBACK)

    removed, skipped = [], []

    # 单层: 上传原图目录 (会话级独立, 完整 UUID 命名)
    for src in _SINGLE_LAYER_SOURCES:
        target = root / src / conv_full_name
        rel = f"{src}/{conv_full_name}"
        if _safe_rmtree(target, root):
            removed.append(rel)
        else:
            skipped.append(rel)

    # 双层: 各业务产物目录 (项目在外 / 会话在内)
    for src in _DOUBLE_LAYER_SOURCES:
        target = root / src / proj_short / conv_short
        rel = f"{src}/{proj_short}/{conv_short}"
        if _safe_rmtree(target, root):
            removed.append(rel)
        else:
            skipped.append(rel)

    logger.info(
        f"[Cleanup] 会话 {conversation_id[:8]} 本地缓存清理完成: "
        f"删除 {len(removed)} 个目录, 跳过 {len(skipped)} 个"
    )
    return {"removed": removed, "skipped": skipped}
