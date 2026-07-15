"""
agent-files 目录顺序迁移脚本 (v2.3 → v2.4)

背景:
  v2.3 时 samseg/generate、samseg/vector、geoserver/generate、report、analysis、preprocess
  这 6 个来源的双层 UUID 目录顺序是 {conv_id}/{project_id}/;
  sandbox/workspace 是 {project_id}/{conv_id}/。
  v2.4 统一为 {project_id}/{conv_id}/ (项目在外, 会话在内), 并统一兜底常量
  (_ungrouped→_default, _anon→_anonymous)。

本脚本做的事:
  1. 把上述 6 个来源下的 {conv}/{proj}/ 翻转成 {proj}/{conv}/
  2. 把 sandbox/workspace 下的 _ungrouped → _default, _anon → _anonymous
     (sandbox 顺序本来就对, 只改兜底目录名)
  3. 连 PostgreSQL (Agent_study 库), 扫 ai_task.output JSONB,
     用正则把里面的旧绝对路径段替换成新路径 (一条 UPDATE 搞定)
  4. 输出 scripts/migrate_map.json 记录旧→新对应, 便于回滚

★ 文件名保持不变: 历史长文件名里有语义 (类别/操作类型), 强行精简反而丢失信息且风险高。
  只翻目录顺序, 文件名原样保留。新文件走新命名规则, 新旧共存。

用法:
  python scripts/migrate_agent_files.py              # dry-run, 只打印将做什么
  python scripts/migrate_agent_files.py --apply      # 真正执行
  python scripts/migrate_agent_files.py --apply --skip-db  # 只迁移文件, 不动数据库
"""
import argparse
import json
import os
import re
import shutil
import sys
from pathlib import Path

# 项目根 (脚本在 scripts/ 下, 往上两级)
ROOT = Path(__file__).resolve().parent.parent
AGENT_FILES = ROOT / "agent-files"

# 需要翻转 conv/proj 顺序的 6 个来源 (相对 agent-files/ 的路径)
# 注意: samseg/send 是单层 conv 目录, 不在列表里 (结构不同, 不翻转)
FLIP_SOURCES = [
    "samseg/generate",
    "samseg/vector",
    "geoserver/generate",
    "report",
    "analysis",
    "preprocess",
]

# sandbox/workspace 顺序本来就对, 但兜底常量要改
SANDBOX_WORKSPACE = "sandbox/workspace"
SANDBOX_RENAME = {  # 旧兜底目录名 → 新
    "_ungrouped": "_default",
    "_anon": "_anonymous",
}

# UUID 正则 (8-4-4-4-12) — 用于识别"这一层是会话/项目 id"
UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)

# 兜底常量 (旧结构里第二层常见的)
PROJ_FALLBACKS = {"_default"}


def is_uuid(name: str) -> bool:
    """判断目录名是不是标准 UUID。"""
    return bool(UUID_RE.match(name))


def is_proj_fallback(name: str) -> bool:
    """判断目录名是不是项目兜底常量 (_default 等)。"""
    return name in PROJ_FALLBACKS or name.startswith("_") and name not in ("_anonymous",)


def load_sandbox_pairings():
    """
    从 sandbox/workspace (顺序一直正确: {项目}/{会话}/) 加载权威的 (项目, 会话) 配对集合。
    用于在两层都是 UUID 时, 判定哪个是项目、哪个是会话。
    返回 set of (proj_name, conv_name)。
    """
    pairings = set()
    ws = AGENT_FILES / SANDBOX_WORKSPACE
    if not ws.is_dir():
        return pairings
    for proj_dir in ws.iterdir():
        if not proj_dir.is_dir():
            continue
        for conv_dir in proj_dir.iterdir():
            if conv_dir.is_dir():
                pairings.add((proj_dir.name, conv_dir.name))
    return pairings


def collect_flips(src_root: Path, sandbox_pairings=None):
    """
    扫描 src_root 下的二级目录, 找出"旧结构 {会话}/{项目}/"待翻转的。
    返回 [(old_path, new_path), ...]。

    判定逻辑 (区分哪个 UUID 是项目、哪个是会话):
      - 第二层是 _default → 一定是旧结构 (兜底项目在内层), 翻转
      - 第一层是 _anonymous → 会话兜底在内层? 不可能 (会话应在第一层), 跳过
      - 两层都是 UUID → 用 sandbox_pairings 验证:
          * 如果 (第二层B, 第一层A) 在 sandbox 里存在 (B是项目、A是会话) → 旧结构, 翻转
          * 如果 (第一层A, 第二层B) 在 sandbox 里存在 (A是项目、B是会话) → 新结构, 跳过
          * 都不在 → 无法判定, 跳过 (保守, 避免误翻)
      - 第一层是 _default → 已是新结构, 跳过
    """
    if sandbox_pairings is None:
        sandbox_pairings = set()
    flips = []
    if not src_root.is_dir():
        return flips
    for first_dir in list(src_root.iterdir()):
        if not first_dir.is_dir():
            continue
        first = first_dir.name
        # 第一层已是项目兜底 (_default) → 新结构, 跳过
        if first in PROJ_FALLBACKS:
            continue
        # 第一层应是会话 id (UUID) 或 _anonymous; 否则跳过 (test_p1 等非标准目录)
        if not (is_uuid(first) or first == "_anonymous"):
            continue
        for second_dir in list(first_dir.iterdir()):
            if not second_dir.is_dir():
                continue
            second = second_dir.name
            # 第二层应是项目 id (UUID) 或 _default; 否则跳过
            if not (is_uuid(second) or second in PROJ_FALLBACKS):
                continue

            should_flip = False
            if second in PROJ_FALLBACKS:
                # 第二层是 _default → 旧结构 (兜底项目在内层), 翻转
                should_flip = True
            elif is_uuid(first) and is_uuid(second):
                # 两层都是 UUID, 用 sandbox 权威配对判定方向
                if (second, first) in sandbox_pairings:
                    # (second=项目, first=会话) 在 sandbox 存在 → 当前是旧结构, 翻转
                    should_flip = True
                elif (first, second) in sandbox_pairings:
                    # (first=项目, second=会话) 在 sandbox 存在 → 已是新结构, 跳过
                    should_flip = False
                # else: 无法判定, 保守跳过

            if not should_flip:
                continue
            # 目标路径: src_root/{项目}/{会话}/ = src_root/{second}/{first}/
            new_dir = src_root / second / first
            if new_dir.exists():
                continue
            flips.append((second_dir, new_dir))
    return flips


def collect_sandbox_renames(ws_root: Path):
    """
    扫描 sandbox/workspace, 找需要改兜底目录名的:
      _ungrouped/ → _default/
      _anon/ (在任一层) → _anonymous/
    返回 [(old_path, new_path), ...]。
    """
    renames = []
    if not ws_root.is_dir():
        return renames
    for proj_dir in ws_root.iterdir():
        if not proj_dir.is_dir():
            continue
        # 第一层兜底: _ungrouped → _default
        new_proj_name = SANDBOX_RENAME.get(proj_dir.name, proj_dir.name)
        target_proj = ws_root / new_proj_name
        if new_proj_name != proj_dir.name:
            renames.append((proj_dir, target_proj))
        # 第二层兜底: 扫描子目录里的 _anon → _anonymous
        for conv_dir in proj_dir.iterdir():
            if not conv_dir.is_dir():
                continue
            new_conv_name = SANDBOX_RENAME.get(conv_dir.name, conv_dir.name)
            if new_conv_name != conv_dir.name:
                target_conv = target_proj / new_conv_name
                renames.append((conv_dir, target_conv))
    return renames


def build_path_substitutions(flips, renames):
    r"""
    从文件迁移计划构造"路径片段替换表", 供 DB 更新用。
    返回 [(old_fragment, new_fragment)], 其中 fragment 是相对 agent-files 的路径段。
    DB 里存的是绝对路径, Windows 上可能用 \\\\ 也可能用 / 作分隔符 (取决于写入时的代码),
    所以每条规则同时生成 posix (/) 和 os (\) 两种形式, 确保都能匹配。

    返回的 fragment 不含 agent-files 前缀 (因为前缀里也可能有 \ 或 /),
    只取从来源名开始的相对段, 例:
      ("samseg/generate/conv-uuid/proj-uuid", "samseg/generate/proj-uuid/conv-uuid")
      ("samseg\generate\conv-uuid\proj-uuid", "samseg\generate\proj-uuid\conv-uuid")
    """
    subs = []
    seen = set()
    for old, new in flips + renames:
        try:
            old_rel = old.relative_to(AGENT_FILES)
            new_rel = new.relative_to(AGENT_FILES)
        except ValueError:
            continue
        # posix 形式 (/)
        o_posix = old_rel.as_posix()
        n_posix = new_rel.as_posix()
        if (o_posix, n_posix) not in seen:
            subs.append((o_posix, n_posix))
            seen.add((o_posix, n_posix))
        # os 原生形式 (Windows 上是 \), 但 JSONB::text 会把 \ 转义成 \\
        # 所以替换片段也要用 \\ 才能匹配 DB 里的序列化形式
        o_native = str(old_rel).replace("\\", "\\\\")
        n_native = str(new_rel).replace("\\", "\\\\")
        if o_native != o_posix and (o_native, n_native) not in seen:
            subs.append((o_native, n_native))
            seen.add((o_native, n_native))
    return subs


def migrate_files(flips, renames, apply: bool, log):
    """执行文件搬迁。"""
    moved = 0
    skipped = 0
    failed = 0
    for old, new in flips + renames:
        if new.exists():
            log(f"  SKIP (目标已存在): {old} → {new}")
            skipped += 1
            continue
        if apply:
            try:
                new.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(old), str(new))
                # 清理可能变空的父目录
                try:
                    if old.parent.exists() and not any(old.parent.iterdir()):
                        old.parent.rmdir()
                except OSError:
                    pass
                log(f"  MOVED: {old.relative_to(AGENT_FILES)} → {new.relative_to(AGENT_FILES)}")
                moved += 1
            except Exception as e:
                log(f"  FAILED: {old} → {new}: {e}")
                failed += 1
        else:
            log(f"  [DRY] MOVED: {old.relative_to(AGENT_FILES)} → {new.relative_to(AGENT_FILES)}")
            moved += 1
    return moved, skipped, failed


def update_db(subs, apply: bool, skip_db: bool, log):
    """连 PostgreSQL, 更新 ai_task.output JSONB 里的路径片段。"""
    if skip_db:
        log("\n[DB] --skip-db, 跳过数据库更新")
        return 0, 0
    if not subs:
        log("\n[DB] 无路径替换项, 跳过数据库更新")
        return 0, 0
    try:
        import psycopg2
    except ImportError:
        log("\n[DB] psycopg2 未安装, 跳过数据库更新 (pip install psycopg2-binary)")
        return 0, 0

    # 复用项目配置 (避免硬编码)
    sys.path.insert(0, str(ROOT))
    try:
        from backend.config import settings as _s
        db_host = _s.agent_db_host
        db_port = _s.agent_db_port
        db_name = _s.agent_db_name
        db_user = _s.agent_db_user
        db_password = _s.agent_db_password
    except Exception as e:
        log(f"[DB] 读配置失败, 用默认值: {e}")
        db_host, db_port = "localhost", "5432"
        db_name, db_user, db_password = "Agent_study", "postgres", "001117"

    updated_rows = 0
    total_subs_applied = 0
    try:
        conn = psycopg2.connect(
            host=db_host, port=db_port, dbname=db_name,
            user=db_user, password=db_password,
        )
    except Exception as e:
        log(f"[DB] 连接失败 ({db_name}@{db_host}): {e}")
        log("      文件迁移已完成但 DB 未更新, 历史任务里的旧路径会失效。")
        log("      可后续手动重跑本脚本 (只跑 DB 部分) 或用 migrate_map.json 人工修复。")
        return 0, 0

    try:
        with conn:
            with conn.cursor() as cur:
                # 对每条替换规则跑一次 UPDATE。
                # ★ 不用 LIKE (它的 _ 和 % 是元字符, 路径里的 _default 会被误判),
                #   改用 strpos(...) > 0 做纯子串匹配, replace() 做替换。
                for old_frag, new_frag in subs:
                    if apply:
                        cur.execute(
                            """UPDATE ai_task
                               SET output = replace(output::text, %s, %s)::jsonb
                               WHERE strpos(output::text, %s) > 0""",
                            (old_frag, new_frag, old_frag),
                        )
                        if cur.rowcount > 0:
                            log(f"  DB UPDATE: {old_frag} → {new_frag}  (影响 {cur.rowcount} 行)")
                            updated_rows += cur.rowcount
                            total_subs_applied += 1
                    else:
                        cur.execute(
                            """SELECT count(*) FROM ai_task
                               WHERE strpos(output::text, %s) > 0""",
                            (old_frag,),
                        )
                        cnt = cur.fetchone()[0]
                        if cnt > 0:
                            log(f"  [DRY] DB UPDATE: {old_frag} → {new_frag}  (将影响 {cnt} 行)")
                            updated_rows += cnt
                            total_subs_applied += 1
        log(f"[DB] 完成: {'影响' if apply else '将影响'} {updated_rows} 行, {total_subs_applied} 条规则命中")
    except Exception as e:
        log(f"[DB] 更新失败: {e}")
    finally:
        conn.close()
    return updated_rows, total_subs_applied


def main():
    parser = argparse.ArgumentParser(description="agent-files 目录顺序迁移 v2.3→v2.4")
    parser.add_argument("--apply", action="store_true", help="真正执行 (默认 dry-run)")
    parser.add_argument("--skip-db", action="store_true", help="跳过数据库更新")
    args = parser.parse_args()

    def log(msg):
        print(msg, flush=True)

    mode = "APPLY" if args.apply else "DRY-RUN"
    log("=" * 60)
    log(f"agent-files 目录顺序迁移 ({mode})")
    log(f"  根目录: {AGENT_FILES}")
    log(f"  数据库: {'跳过' if args.skip_db else '更新 ai_task.output'}")
    log("=" * 60)

    if not AGENT_FILES.is_dir():
        log(f"错误: agent-files 目录不存在: {AGENT_FILES}")
        sys.exit(1)

    # 1. 收集 6 个来源的翻转计划
    # 先从 sandbox/workspace 加载权威的 (项目, 会话) 配对, 用于两层都是 UUID 时判定方向
    sandbox_pairings = load_sandbox_pairings()
    log(f"\n[0] 从 sandbox/workspace 加载 {len(sandbox_pairings)} 条权威 (项目, 会话) 配对")

    all_flips = []
    log("\n[1] 扫描需翻转的 {conv}/{proj}/ → {proj}/{conv}/:")
    for src in FLIP_SOURCES:
        src_root = AGENT_FILES / src
        flips = collect_flips(src_root, sandbox_pairings)
        log(f"  {src}: {len(flips)} 个目录待翻转")
        all_flips.extend(flips)

    # 2. 收集 sandbox 兜底常量改名
    log("\n[2] 扫描 sandbox/workspace 兜底常量改名:")
    ws_root = AGENT_FILES / SANDBOX_WORKSPACE
    renames = collect_sandbox_renames(ws_root)
    log(f"  {SANDBOX_WORKSPACE}: {len(renames)} 个目录待改名")

    if not all_flips and not renames:
        log("\n无需迁移, 所有目录已是 v2.4 结构。")
        sys.exit(0)

    # 3. 写映射表 (无论 dry-run 还是 apply, 都写 map 供回滚参考)
    subs = build_path_substitutions(all_flips, renames)
    map_path = ROOT / "scripts" / "migrate_map.json"
    map_data = {
        "mode": mode,
        "flips": [
            {
                "old": str(old.relative_to(AGENT_FILES)),
                "new": str(new.relative_to(AGENT_FILES)),
            }
            for old, new in all_flips
        ],
        "renames": [
            {
                "old": str(old.relative_to(AGENT_FILES)),
                "new": str(new.relative_to(AGENT_FILES)),
            }
            for old, new in renames
        ],
        "path_substitutions": [{"old": o, "new": n} for o, n in subs],
    }
    map_path.write_text(json.dumps(map_data, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"\n[3] 映射表已写: {map_path}")

    # 4. 执行文件迁移
    log(f"\n[4] 执行文件迁移 ({mode}):")
    moved, skipped, failed = migrate_files(all_flips, renames, args.apply, log)
    log(f"  汇总: 移动={moved}, 跳过={skipped}, 失败={failed}")

    # 5. 更新数据库
    log(f"\n[5] 更新数据库 ai_task.output ({mode}):")
    update_db(subs, args.apply, args.skip_db, log)

    log("\n" + "=" * 60)
    if args.apply:
        log("迁移完成。建议:")
        log("  1. 重启后端, 让新代码加载")
        log("  2. 如有运行中的沙盒容器, 调 sandbox_manager.cleanup_all() 清空旧挂载")
        log("  3. 抽查历史任务的下载链接是否还能打开")
    else:
        log("DRY-RUN 完成。确认无误后加 --apply 真正执行。")
    log("=" * 60)


if __name__ == "__main__":
    main()
