"""
agent-files 目录 ID 短化迁移脚本 (v2.4 → v2.5)

背景:
  v2.4 时所有"双层 id"来源目录的层级名用完整 UUID
  (如 samseg/generate/449148e8-d35c-468c-9c0e-b45b53e1ed5b/abc12345-.../)。
  v2.5 把目录名统一改为 UUID 前 8 位 (DB 主键仍是完整 UUID, 不受影响),
  避免目录路径过长。

本脚本做的事:
  1. 扫描所有"双层 id 结构"来源目录 (见 SOURCES) 下每一层,
     把完整 UUID 目录 (36 位 8-4-4-4-12 格式) 重命名为前 8 位前缀。
  2. 幂等: 已是 8 位或更短的目录跳过 (重复运行安全)。
  3. 冲突处理: 若短化后的目录名已存在 (且不是自己), 记日志不覆盖,
     人工介入合并 (理论上 UUID 前 8 位冲突概率极低)。
  4. 输出 scripts/migrate_short_dirs_map.json 记录旧→新对应, 便于回滚。

★ 兜底常量 (_default/_anonymous) 和非 UUID 目录名 (如 _default) 不动。
★ 文件名不动 (历史长文件名有语义); 只改目录层级名。

用法:
  python scripts/migrate_short_dirs.py              # dry-run, 只打印将做什么
  python scripts/migrate_short_dirs.py --apply      # 真正执行
"""
import argparse
import json
import re
import sys
from pathlib import Path

# 项目根 (脚本在 scripts/ 下, 往上两级)
ROOT = Path(__file__).resolve().parent.parent
AGENT_FILES = ROOT / "agent-files"

# 需要短化目录名的来源 (相对 agent-files/ 的路径); 均为双层 {proj}/{conv} 结构
# 注: samseg/send 是单层 conv 目录, 也短化 (它里面的目录名也是完整 UUID)
SOURCES = [
    "samseg/send",
    "samseg/generate",
    "samseg/vector",
    "geoserver/generate",
    "report",
    "analysis",
    "preprocess",
    "sandbox/workspace",
]

# 完整 UUID 正则 (36 位 8-4-4-4-12)
UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)

ID_SHORT_LEN = 8  # 与 backend/model/tools/_paths.py::ID_SHORT_LEN 保持一致


def short_uuid_dir(name: str) -> str:
    """完整 UUID 目录名 → 前 8 位; 非 UUID 原样返回。"""
    if UUID_RE.match(name):
        return name[:ID_SHORT_LEN]
    return name


def migrate_source(src_root: Path, apply: bool, rename_log: list):
    """
    扫描某来源目录下所有层级 (含嵌套子目录), 短化其中的完整 UUID 目录名。
    自底向上重命名 (避免父目录重命名后子路径失效)。
    """
    if not src_root.exists():
        print(f"[跳过] 来源不存在: {src_root.relative_to(ROOT)}")
        return

    # 收集所有"目录名是完整 UUID"的路径 (自底向上排序, 先处理深层)
    uuid_dirs = []
    for p in sorted(src_root.rglob("*"), key=lambda x: len(x.parts), reverse=True):
        if p.is_dir() and UUID_RE.match(p.name):
            uuid_dirs.append(p)

    for d in uuid_dirs:
        new_name = d.name[:ID_SHORT_LEN]
        new_path = d.parent / new_name
        if new_path == d:
            continue  # 已经是短名 (理论上不会, 因 d.name 是完整 UUID)
        if new_path.exists():
            # 冲突: 短名目录已存在, 不覆盖, 记日志
            print(f"[冲突] {d.relative_to(ROOT)} -> {new_name} (目标已存在, 跳过, 需人工合并)")
            rename_log.append({"old": str(d), "new": str(new_path), "status": "conflict"})
            continue
        rel_old = d.relative_to(ROOT)
        rel_new = new_path.relative_to(ROOT) if apply else new_path.relative_to(ROOT)
        print(f"{'[重命名]' if apply else '[DRY-RUN]'} {rel_old} -> {rel_new.name}")
        rename_log.append({"old": str(d), "new": str(new_path), "status": "ok"})
        if apply:
            d.rename(new_path)


def main():
    parser = argparse.ArgumentParser(description="agent-files 目录 ID 短化迁移 (完整 UUID → 前 8 位)")
    parser.add_argument("--apply", action="store_true", help="真正执行重命名 (默认 dry-run)")
    args = parser.parse_args()

    print(f"项目根: {ROOT}")
    print(f"agent-files: {AGENT_FILES}")
    print(f"模式: {'APPLY (执行)' if args.apply else 'DRY-RUN (只打印)'}")
    print("-" * 60)

    rename_log = []
    for src in SOURCES:
        src_root = AGENT_FILES / src
        print(f"\n=== 处理来源: {src} ===")
        migrate_source(src_root, apply=args.apply, rename_log=rename_log)

    # 输出迁移记录 (便于回滚)
    map_file = ROOT / "scripts" / "migrate_short_dirs_map.json"
    if rename_log:
        map_file.write_text(
            json.dumps(rename_log, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"\n迁移记录已写入: {map_file.relative_to(ROOT)}")
    else:
        print("\n无需迁移 (所有目录名已是短格式或非 UUID)。")

    ok_count = sum(1 for r in rename_log if r["status"] == "ok")
    conflict_count = sum(1 for r in rename_log if r["status"] == "conflict")
    print(f"\n汇总: {ok_count} 个重命名, {conflict_count} 个冲突跳过。")

    if not args.apply and rename_log:
        print("\n(以上为 DRY-RUN 预览。加 --apply 参数真正执行。)")


if __name__ == "__main__":
    main()
