# -*- coding: utf-8 -*-
"""
存量产物回填: vector_layer + artifact

背景:
  历史 SamSeg 产物只落在 agent-files/，业务库 vector_layer 与记忆库 artifact 均为空，
  产物血缘只能靠目录约定。本脚本一次性把磁盘上的矢量/掩膜/报告登记进库，并可重复执行
  （vector_layer 按 layer_name UPSERT；artifact 按 storage_uri 查重）。

用法:
  python scripts/register_vector_outputs.py
  python scripts/register_vector_outputs.py --dry-run
"""
import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.config import settings  # noqa: E402
from backend.data import business_db  # noqa: E402
from backend.data.vector_registry import register_vector_product  # noqa: E402

PRINCIPAL_SUFFIXES = {".geojson", ".shp", ".png", ".tif", ".tiff", ".pdf", ".docx", ".zip"}
_SKIP_PARTS = {"__pycache__", ".git"}


def _infer_kind(path: Path) -> str:
    name = path.name.lower()
    if "change" in name or "变化" in name:
        return "change"
    return "segment"


def _infer_artifact_type(path: Path) -> str:
    rel = path.as_posix().lower()
    suffix = path.suffix.lower()
    if "samseg/vector" in rel or suffix in {".geojson", ".shp", ".zip"}:
        return "change_vector" if _infer_kind(path) == "change" else "segment_vector"
    if "/report/" in rel or suffix in {".pdf", ".docx"}:
        return "report_pdf"
    if "/analysis/" in rel:
        return "stats_table"
    if suffix in {".png", ".tif", ".tiff"}:
        return "change_mask" if _infer_kind(path) == "change" else "segment_mask"
    return "overlay_png"


def _resolve_conversation_id(path: Path) -> str | None:
    """
    从路径中解析会话 id：优先完整 UUID 目录；否则用 8 位短目录前缀在 conversation 表匹配。
    """
    from backend.agent.memory import agent_db

    parts = list(path.parts)
    for part in reversed(parts):
        if re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", part, re.I):
            # 目录里的完整 UUID 未必真在 conversation 表（例如未迁移的上传会话），
            # 直接写入会触发 artifact_conversation_id_fkey。
            with agent_db.get_conn() as conn:
                if conn is None:
                    return None
                try:
                    with conn.cursor() as cur:
                        cur.execute("SELECT 1 FROM conversation WHERE id = %s LIMIT 1", (part,))
                        if cur.fetchone():
                            return part
                except Exception:
                    pass
            continue
    for part in reversed(parts):
        if re.fullmatch(r"[0-9a-f]{8}", part, re.I):
            with agent_db.get_conn() as conn:
                if conn is None:
                    return None
                try:
                    with conn.cursor() as cur:
                        cur.execute(
                            "SELECT id FROM conversation WHERE id LIKE %s LIMIT 1",
                            (part + "%",),
                        )
                        row = cur.fetchone()
                    if row:
                        return row[0]
                except Exception:
                    pass
    return None


def _artifact_exists(storage_uri: str) -> bool:
    from backend.agent.memory import agent_db
    with agent_db.get_conn() as conn:
        if conn is None:
            return False
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM artifact WHERE storage_uri = %s LIMIT 1", (storage_uri,))
                return cur.fetchone() is not None
        except Exception:
            return False


def backfill_vectors(dry_run: bool = False) -> dict:
    root = Path(settings.samseg_vector_root)
    files = sorted(p for p in root.rglob("*.geojson") if p.is_file())
    stats = {"found": len(files), "registered": 0, "failed": 0}
    for f in files:
        if dry_run:
            stats["registered"] += 1
            continue
        vid = register_vector_product(
            str(f), kind=_infer_kind(f), workspace="samseg",
            attrs={"source_path": str(f.resolve())},
        )
        if vid:
            stats["registered"] += 1
        else:
            stats["failed"] += 1
    print(f"[vectors] {stats}")
    return stats


def backfill_artifacts(dry_run: bool = False) -> dict:
    from backend.agent.memory import agent_db

    root = Path(settings.file_storage_root)
    files = [
        p for p in root.rglob("*")
        if p.is_file()
        and p.suffix.lower() in PRINCIPAL_SUFFIXES
        and not any(part in _SKIP_PARTS for part in p.parts)
    ]
    stats = {"found": len(files), "registered": 0, "skipped": 0, "failed": 0}
    for f in files:
        uri = str(f.resolve())
        if _artifact_exists(uri):
            stats["skipped"] += 1
            continue
        if dry_run:
            stats["registered"] += 1
            continue
        conv_id = _resolve_conversation_id(f)
        art_type = _infer_artifact_type(f)
        crs = "EPSG:4490" if art_type.endswith("_vector") else None
        aid = agent_db.register_artifact(
            conversation_id=conv_id,
            artifact_type=art_type,
            storage_uri=uri,
            name=f.name,
            fmt=f.suffix.lstrip(".").lower(),
            crs=crs,
            spatial={"crs_inferred": crs} if crs else None,
            lineage={"source": "backfill", "relative_path": f.relative_to(root).as_posix()},
        )
        if aid:
            stats["registered"] += 1
        else:
            stats["failed"] += 1
    print(f"[artifacts] {stats}")
    return stats


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="回填 vector_layer / artifact")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--skip-vectors", action="store_true")
    parser.add_argument("--skip-artifacts", action="store_true")
    args = parser.parse_args()

    print(f"[db] business_available={business_db.db_available()} postgis={business_db.postgis_available()}")
    if not args.skip_vectors:
        backfill_vectors(args.dry_run)
    if not args.skip_artifacts:
        backfill_artifacts(args.dry_run)
