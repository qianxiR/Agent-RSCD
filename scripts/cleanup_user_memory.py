"""
清理 user_memory 表的噪声数据 (配合 enable_preference_extraction 关闭后的长期机制).

策略:
  - 删除 preference 类全部 (低价值"用户偏好"空话)
  - 删除 fact 类, 但保留 2 条沙盒字体知识 → 迁移为 envfact (环境事实)
  - 保留 lesson / workflow (错误案例+解决方案, 核心价值)

安全机制:
  1. DRY-RUN 模式 (默认): 只打印将要做什么, 不改数据.
     python scripts/cleanup_user_memory.py            # 预览
     python scripts/cleanup_user_memory.py --apply    # 真正执行
  2. 执行前把所有待删/待改行 dump 到 backup_user_memory_<时间戳>.json (可回滚).
  3. 迁移而非删除字体知识: 只 UPDATE category 字段, value/key/id 不动.

判定"沙盒字体知识"的规则 (可解释, 不靠模糊匹配):
  - category='fact' AND (key ILIKE '%font%' OR key ILIKE '%sandbox%'
    OR value ILIKE '%matplotlib%' OR value ILIKE '%字体%')
"""
import sys
import json
import datetime
import psycopg2

CONN = dict(host="localhost", port=5432, dbname="Agent_study",
            user="postgres", password="001117")

# 沙盒字体知识保留规则 (见上方注释)
KEEP_FONT_PREDICATE = """
    (key ILIKE '%font%' OR key ILIKE '%sandbox%'
     OR value ILIKE '%matplotlib%' OR value ILIKE '%字体%'
     OR value ILIKE '%noto%')
"""


def fetch_all(cur):
    cur.execute("""SELECT id, user_id, key, value, category, created_at
                   FROM user_memory ORDER BY id""")
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def classify(rows):
    """把现有行分成: keep(lesson/workflow), migrate_to_envfact, delete."""
    keep, migrate, delete = [], [], []
    for r in rows:
        cat = (r["category"] or "").lower()
        if cat in ("lesson", "workflow", "envfact"):
            keep.append(r)
        elif cat == "fact":
            # fact 里判断是否沙盒字体知识
            key = r["key"] or ""
            val = r["value"] or ""
            if ("font" in key.lower() or "sandbox" in key.lower()
                    or "matplotlib" in val.lower() or "字体" in val
                    or "noto" in val.lower()):
                migrate.append(r)
            else:
                delete.append(r)
        else:
            # preference / entity / 未知 → 删
            delete.append(r)
    return keep, migrate, delete


def main():
    dry = "--apply" not in sys.argv
    print(f"模式: {'DRY-RUN (预览, 不改数据)' if dry else '★ APPLY (将真正执行)'}")
    print("=" * 70)

    conn = psycopg2.connect(**CONN)
    try:
        cur = conn.cursor()
        rows = fetch_all(cur)
        print(f"user_memory 现有总行数: {len(rows)}")
        keep, migrate, delete = classify(rows)
        print(f"  → 保留 (lesson/workflow/envfact): {len(keep)}")
        print(f"  → 迁移 fact→envfact (沙盒字体知识): {len(migrate)}")
        print(f"  → 删除 (preference/entity/废话fact): {len(delete)}")
        print()

        print("[保留]")
        for r in keep:
            print(f"  id={r['id']:>3} [{r['category']}] {r['key']}")
        print()

        print("[迁移 fact → envfact]")
        for r in migrate:
            print(f"  id={r['id']:>3} key={r['key']!r}")
            print(f"      -> {(r['value'] or '')[:90]}")
        print()

        print("[删除]")
        for r in delete:
            print(f"  id={r['id']:>3} [{r['category']}] {r['key']!r}: {(r['value'] or '')[:60]}")
        print()

        if dry:
            print("这是 DRY-RUN. 确认无误后运行:")
            print("  python scripts/cleanup_user_memory.py --apply")
            return

        # ===== APPLY =====
        # 1. 备份全部行 (回滚用)
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = f"backup_user_memory_{ts}.json"
        with open(backup_path, "w", encoding="utf-8") as f:
            json.dump(rows, f, ensure_ascii=False, indent=2, default=str)
        print(f"已备份全部 {len(rows)} 行到 {backup_path}")

        # 2. 迁移 fact → envfact
        if migrate:
            migrate_ids = [r["id"] for r in migrate]
            cur.execute(
                "UPDATE user_memory SET category='envfact' WHERE id = ANY(%s)",
                (migrate_ids,),
            )
            print(f"已迁移 {cur.rowcount} 行 fact → envfact (id={migrate_ids})")

        # 3. 删除 preference / entity / 废话 fact
        if delete:
            delete_ids = [r["id"] for r in delete]
            cur.execute(
                "DELETE FROM user_memory WHERE id = ANY(%s)",
                (delete_ids,),
            )
            print(f"已删除 {cur.rowcount} 行 (id={delete_ids})")

        conn.commit()
        print()
        print("★ 完成. 清理后剩余:")
        remaining = fetch_all(cur)
        for r in remaining:
            print(f"  id={r['id']:>3} [{r['category']}] {r['key']}")
        print(f"剩余 {len(remaining)} 行.")
    except Exception as e:
        conn.rollback()
        print(f"[!] 失败已回滚: {e}")
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
