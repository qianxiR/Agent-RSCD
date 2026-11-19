"""
长期记忆 v2 迁移脚本。

目标:
  - 删除现有全部 preference（用户要求重置偏好）；
  - fact 迁移为 profile；
  - entity / envfact / workflow 归档，不再注入；
  - lesson 保留；
  - 补 scope / kind / confidence / source / evidence / status 等字段。

用法:
  python scripts/cleanup_user_memory.py            # DRY-RUN，只预览
  python scripts/cleanup_user_memory.py --apply    # 备份 + 执行迁移
"""
from __future__ import annotations

import argparse
import datetime
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import psycopg2  # noqa: E402

from backend.config import settings  # noqa: E402


def _connect():
    return psycopg2.connect(
        host=settings.agent_db_host,
        port=settings.agent_db_port,
        dbname=settings.agent_db_name,
        user=settings.agent_db_user,
        password=settings.agent_db_password,
    )


def _fetch_legacy_rows(conn) -> List[Dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, user_id, key, value, category, created_at "
            "FROM user_memory ORDER BY id"
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def _print_plan(rows: List[Dict[str, Any]]) -> Dict[str, List[int]]:
    groups: Dict[str, List[int]] = {
        "preference": [],
        "fact": [],
        "profile": [],
        "lesson": [],
        "legacy": [],
    }
    for row in rows:
        category = (row.get("category") or "").strip().lower()
        if category == "preference":
            groups["preference"].append(row["id"])
        elif category == "fact":
            groups["fact"].append(row["id"])
        elif category == "profile":
            groups["profile"].append(row["id"])
        elif category == "lesson":
            groups["lesson"].append(row["id"])
        else:
            groups["legacy"].append(row["id"])

    print(f"user_memory 现有总行数: {len(rows)}")
    print(f"  → 删除 preference: {len(groups['preference'])}")
    print(f"  → 迁移 fact → profile: {len(groups['fact'])}")
    print(f"  → 保留 profile: {len(groups['profile'])}")
    print(f"  → 保留 lesson: {len(groups['lesson'])}")
    print(f"  → 归档旧类别 (entity/envfact/workflow): {len(groups['legacy'])}")
    return groups


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="执行迁移；默认只预览")
    args = parser.parse_args()

    conn = _connect()
    try:
        rows = _fetch_legacy_rows(conn)
        groups = _print_plan(rows)
        if not args.apply:
            print("\nDRY-RUN：确认后运行 python scripts/cleanup_user_memory.py --apply")
            return

        backup_dir = ROOT / ".tmp"
        backup_dir.mkdir(exist_ok=True)
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = backup_dir / f"user_memory_backup_{stamp}.json"
        backup_path.write_text(
            json.dumps(rows, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        print(f"\n已备份 {len(rows)} 行到 {backup_path}")
    finally:
        conn.close()

    from backend.agent.memory import agent_db

    if not agent_db.init_schema():
        print("[!] agent_db 不可用，迁移中止")
        sys.exit(1)

    conn = _connect()
    try:
        with conn.cursor() as cur:
            if groups["preference"]:
                cur.execute(
                    "DELETE FROM user_memory WHERE id = ANY(%s)",
                    (groups["preference"],),
                )
                print(f"已删除 preference: {cur.rowcount} 行")
        conn.commit()

        with conn.cursor() as cur:
            cur.execute(
                "SELECT category, status, count(*) FROM user_memory "
                "GROUP BY category, status ORDER BY category, status"
            )
            print("\n迁移后分布:")
            for category, status, count in cur.fetchall():
                print(f"  {category:12s} {status:10s} {count}")
    except Exception as exc:
        conn.rollback()
        print(f"[!] 迁移失败已回滚: {exc}")
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
