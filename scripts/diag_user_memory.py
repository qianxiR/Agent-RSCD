"""
一次性诊断脚本: 量化 user_memory 表的噪声规模。

只读查询, 不改任何数据。运行后可直接删除。
用法: python scripts/diag_user_memory.py
"""
import sys
from collections import Counter

import psycopg2

CONN = dict(host="localhost", port=5432, dbname="Agent_study",
            user="postgres", password="001117")


def q(cur, sql, params=None):
    cur.execute(sql, params or ())
    return cur.fetchall()


def main():
    conn = psycopg2.connect(**CONN)
    try:
        cur = conn.cursor()

        # 0. 表是否存在 / 总行数
        try:
            total = q(cur, "SELECT COUNT(*) FROM user_memory")[0][0]
        except psycopg2.Error as e:
            print(f"[!] user_memory 表查不到: {e}")
            return

        print("=" * 70)
        print(f"user_memory 总行数: {total}")
        print("=" * 70)
        if total == 0:
            print("表为空, 没有数据可分析。")
            return

        # 1. 按 category 分布
        print("\n[1] 按 category 分布")
        print("-" * 70)
        for cat, cnt in q(cur,
                "SELECT category, COUNT(*) FROM user_memory GROUP BY category ORDER BY COUNT(*) DESC"):
            bar = "#" * min(60, int(cnt))
            print(f"  {cat:<14} {cnt:>5}  {bar}")

        # 2. 按 user_id 分布
        print("\n[2] 按 user_id 分布")
        print("-" * 70)
        for uid, cnt in q(cur,
                "SELECT user_id, COUNT(*) FROM user_memory GROUP BY user_id ORDER BY COUNT(*) DESC"):
            print(f"  {uid:<20} {cnt:>5}")

        # 3. category × user_id 交叉 (看清 global 的 lesson/workflow 有多少)
        print("\n[3] category × user_id 交叉")
        print("-" * 70)
        cross = q(cur, """
            SELECT COALESCE(category,'(null)'), COALESCE(user_id,'(null)'), COUNT(*)
            FROM user_memory GROUP BY 1,2 ORDER BY COUNT(*) DESC""")
        for cat, uid, cnt in cross:
            print(f"  {cat:<14} | {uid:<18} | {cnt:>5}")

        # 4. value 长度统计 (长 value 多半是 lesson/workflow, 短的是废话偏好)
        print("\n[4] value 文本长度分布 (按 category)")
        print("-" * 70)
        for cat, avg_len, max_len in q(cur, """
            SELECT category,
                   AVG(LENGTH(COALESCE(value,'')))::int,
                   MAX(LENGTH(COALESCE(value,'')))
            FROM user_memory GROUP BY category ORDER BY category"""):
            print(f"  {cat:<14}  avg={avg_len:>5}  max={max_len:>5}")

        # 5. 抽样: preference/fact 各看最多 8 条
        for cat in ("preference", "fact", "entity"):
            rows = q(cur, """
                SELECT key, value FROM user_memory
                WHERE category=%s ORDER BY id LIMIT 8""", (cat,))
            if not rows:
                continue
            print(f"\n[5] 抽样: category={cat} (最多 8 条)")
            print("-" * 70)
            for k, v in rows:
                v_short = (v or "").replace("\n", " ")[:120]
                print(f"  key={k!r}")
                print(f"    -> {v_short}")

        # 6. 抽样: lesson / workflow (对照: 你想保留的那类)
        for cat in ("lesson", "workflow"):
            rows = q(cur, """
                SELECT key, value FROM user_memory
                WHERE category=%s ORDER BY id LIMIT 3""", (cat,))
            if not rows:
                continue
            print(f"\n[6] 对照抽样: category={cat} (最多 3 条)")
            print("-" * 70)
            for k, v in rows:
                v_short = (v or "").replace("\n", " ")[:200]
                print(f"  key={k!r}")
                print(f"    -> {v_short}")

        # 7. 估算 system prompt 注入压力
        print("\n[7] 注入 system prompt 的总量 (build_user_profile_text 的输出规模)")
        print("-" * 70)
        total_chars = q(cur, "SELECT SUM(LENGTH(COALESCE(value,''))+LENGTH(COALESCE(key,''))+6) FROM user_memory")[0][0] or 0
        # 当前用户 (study_user) + global 合并注入
        user_global = q(cur, """
            SELECT COALESCE(SUM(LENGTH(COALESCE(value,''))+LENGTH(COALESCE(key,''))+6),0)
            FROM user_memory WHERE user_id IN ('study_user','global')""")[0][0] or 0
        print(f"  全表注入总量:   ~{total_chars} chars (~{total_chars//2} tokens 估算)")
        print(f"  study_user+global 注入: ~{user_global} chars (~{user_global//2} tokens)")

    finally:
        conn.close()


if __name__ == "__main__":
    try:
        main()
    except psycopg2.OperationalError as e:
        print(f"[!] 连不上 agent_db: {e}")
        sys.exit(1)
