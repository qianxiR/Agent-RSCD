"""
清理历史执行结果: 数据库表 + 本地产物目录.

★ 删除范围 (用户已确认):
  Agent_study 库: message / conversation_summary / task_log / ai_task / conversation
  cd 库:          image_metadata / vector_layer  (仅登记表, GeoServer 实体不动)
  agent-files/:   generate / vector / send / analysis / report / sandbox / geoserver / preprocess

★ 保留:
  Agent_study.user_memory (教训记忆, 含刚注入的 toggle_layer 结构化经验)
  GeoServer 服务器上的图层实体 (不动)
  smoke_test/ (测试样例)
  nul (Windows 重定向产物, 空文件)

安全机制:
  1. DRY-RUN (默认): 只打印将要做什么, 不改数据/不删文件
     python scripts/purge_history.py            # 预览
     python scripts/purge_history.py --apply    # 真正执行
  2. 数据库: 执行前把被清的表全部 dump 到 backup_purge_<时间戳>.sql
  3. 目录: 移动到 agent-files/.trash_purge_<时间戳>/ 而非直接删 (可恢复)
"""
import os
import sys
import shutil
import subprocess
import datetime
import psycopg2

CONN_AGENT = dict(host="localhost", port=5432, dbname="Agent_study",
                  user="postgres", password="001117")
CONN_CD = dict(host="localhost", port=5432, dbname="cd",
               user="postgres", password="001117")

# Agent_study 库: 清空的表 (user_memory 保留!)
AGENT_TABLES_TO_CLEAR = [
    "message", "conversation_summary", "task_log", "ai_task", "conversation"
]
# cd 库: 清空的表 (GeoServer 实体不动, 仅清登记)
CD_TABLES_TO_CLEAR = ["image_metadata", "vector_layer"]

# agent-files 下: 清空的子目录 (smoke_test 保留!)
PRODUCT_DIRS = [
    "samseg", "analysis", "report", "sandbox", "geoserver", "preprocess"
]
# 保留: smoke_test/, nul

AGENT_FILES = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "agent-files"
)


def count_rows(cur, table):
    cur.execute(f"SELECT COUNT(*) FROM {table}")
    return cur.fetchone()[0]


def dir_info(path):
    if not os.path.exists(path):
        return None
    nfiles = sum(len(fn) for _, _, fn in os.walk(path))
    size = 0
    for dp, _, fn in os.walk(path):
        for f in fn:
            try:
                size += os.path.getsize(os.path.join(dp, f))
            except OSError:
                pass
    return nfiles, size


def main():
    dry = "--apply" not in sys.argv
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    print(f"模式: {'DRY-RUN (预览)' if dry else '★ APPLY (将执行)'}")
    print(f"时间戳: {ts}")
    print("=" * 65)

    # ===== 1. 数据库 Agent_study =====
    print("\n[1] Agent_study 库 (user_memory 保留)")
    c1 = psycopg2.connect(**CONN_AGENT)
    try:
        cur = c1.cursor()
        for t in AGENT_TABLES_TO_CLEAR:
            n = count_rows(cur, t)
            print(f"  将清空 {t:24} {n:>4} 行")
        keep = count_rows(cur, "user_memory")
        print(f"  保留   user_memory            {keep:>4} 行 (教训记忆)")
    finally:
        c1.close()

    # ===== 2. 数据库 cd =====
    print("\n[2] cd 库 (仅登记表, GeoServer 实体不动)")
    c2 = psycopg2.connect(**CONN_CD)
    try:
        cur = c2.cursor()
        for t in CD_TABLES_TO_CLEAR:
            n = count_rows(cur, t)
            print(f"  将清空 {t:24} {n:>4} 行")
    finally:
        c2.close()

    # ===== 3. 本地目录 =====
    print(f"\n[3] 本地产物 {AGENT_FILES}")
    trash = os.path.join(AGENT_FILES, f".trash_purge_{ts}")
    for d in PRODUCT_DIRS:
        full = os.path.join(AGENT_FILES, d)
        info = dir_info(full)
        if info:
            nfiles, size = info
            print(f"  将移动 {d:14} {nfiles:>4} 文件 {size/1024/1024:>6.1f}MB → .trash_purge_{ts}/{d}")
        else:
            print(f"  跳过   {d:14} (不存在)")
    # 保留项
    for keep in ["smoke_test", "user_memory"]:
        pass
    smoke = os.path.join(AGENT_FILES, "smoke_test")
    if os.path.exists(smoke):
        info = dir_info(smoke)
        print(f"  保留   smoke_test         {info[0]:>4} 文件 (测试样例)")

    if dry:
        print("\n" + "=" * 65)
        print("DRY-RUN 完成. 确认无误后运行:")
        print("  python scripts/purge_history.py --apply")
        return

    # ===== APPLY =====
    print("\n" + "=" * 65)
    print("★ 开始执行")

    # --- 数据库备份 (pg_dump 优先, 失败则 JSON) ---
    backup_file = f"backup_purge_{ts}.sql"
    print(f"\n[备份数据库] pg_dump → {backup_file}")
    try:
        for db in ["Agent_study", "cd"]:
            r = subprocess.run(
                ["pg_dump", f"--dbname=postgresql://postgres:001117@localhost:5432/{db}",
                 "-f", f"{db}_{backup_file}"],
                capture_output=True, text=True, timeout=120)
            if r.returncode == 0:
                print(f"  ✓ {db} → {db}_{backup_file}")
            else:
                print(f"  ✗ {db} pg_dump 失败 (无 psql?), 退化到 JSON 备份: {r.stderr[:100]}")
                raise FileNotFoundError
    except (FileNotFoundError, subprocess.TimeoutExpired):
        # 退化: JSON 备份
        print("  → 使用 JSON 备份")
        for conn_cfg, db, tables in [
            (CONN_AGENT, "Agent_study", AGENT_TABLES_TO_CLEAR),
            (CONN_CD, "cd", CD_TABLES_TO_CLEAR),
        ]:
            conn = psycopg2.connect(**conn_cfg)
            cur = conn.cursor()
            dump = {}
            for t in tables:
                cur.execute(f"SELECT * FROM {t}")
                cols = [d[0] for d in cur.description]
                dump[t] = [dict(zip(cols, r)) for r in cur.fetchall()]
            conn.close()
            import json
            jf = f"{db}_backup_purge_{ts}.json"
            with open(jf, "w", encoding="utf-8") as f:
                json.dump(dump, f, ensure_ascii=False, indent=2, default=str)
            print(f"  ✓ {db} → {jf}")

    # --- 清 Agent_study 库 ---
    print("\n[清空 Agent_study]")
    c1 = psycopg2.connect(**CONN_AGENT)
    try:
        cur = c1.cursor()
        # 按外键依赖顺序删 (子表先)
        for t in AGENT_TABLES_TO_CLEAR:
            cur.execute(f"TRUNCATE TABLE {t} RESTART IDENTITY CASCADE")
            print(f"  ✓ TRUNCATE {t}")
        c1.commit()
        cur.execute("SELECT COUNT(*) FROM user_memory")
        print(f"  user_memory 仍剩 {cur.fetchone()[0]} 行 (教训保留)")
    except Exception as e:
        c1.rollback()
        print(f"  ✗ 失败已回滚: {e}")
        sys.exit(1)
    finally:
        c1.close()

    # --- 清 cd 库 ---
    print("\n[清空 cd (仅登记表)]")
    c2 = psycopg2.connect(**CONN_CD)
    try:
        cur = c2.cursor()
        for t in CD_TABLES_TO_CLEAR:
            cur.execute(f"TRUNCATE TABLE {t} RESTART IDENTITY CASCADE")
            print(f"  ✓ TRUNCATE {t}")
        c2.commit()
    except Exception as e:
        c2.rollback()
        print(f"  ✗ 失败已回滚: {e}")
        sys.exit(1)
    finally:
        c2.close()

    # --- 移动本地目录到 trash ---
    print(f"\n[移动产物到 trash]")
    os.makedirs(trash, exist_ok=True)
    moved = 0
    for d in PRODUCT_DIRS:
        full = os.path.join(AGENT_FILES, d)
        if os.path.exists(full):
            dst = os.path.join(trash, d)
            shutil.move(full, dst)
            moved += 1
            print(f"  ✓ {d} → .trash_purge_{ts}/{d}")
    print(f"  共移动 {moved} 个目录到 {trash}")
    print(f"  ★ 确认无误后可手动删除: rmdir /s /q \"{trash}\"")

    print("\n" + "=" * 65)
    print("★ 清理完成")
    print("  - 数据库已备份 (backup_purge_*)")
    print(f"  - 目录已移到 {trash} (未直接删, 可恢复)")
    print("  - user_memory 教训保留")
    print("  - GeoServer 服务器实体未动")


if __name__ == "__main__":
    main()
