"""
自纠捕捉器 (Self-Correction Detector) 测试
======================================================================
3 个测试:
  1. 构造"犯错→自纠"消息序列 → 验证 detect_self_corrections 识别正确
  2. 真实沙盒字体场景复现 → 验证完整 detect + save 流程写入全局记忆
  3. system_prompt 注入验证 → 全局教训记忆被 build_user_profile_text 加载

运行:
  conda activate sam3
  python tests/self_correction_test.py
"""
import sys
import os
import json
import uuid
import asyncio
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"

from langchain_core.messages import AIMessage, ToolMessage, HumanMessage


def section(name):
    print("\n" + "=" * 60)
    print(f"  {name}")
    print("=" * 60)


def check(cond, msg):
    sym = "✓" if cond else "✗"
    print(f"  [{sym}] {msg}")
    return cond


def main():
    overall_ok = True
    from backend.agent.memory import agent_db
    from backend.agent.memory.self_correction import (
        detect_self_corrections, save_lesson_to_memory, scan_and_save_corrections,
        GLOBAL_LESSON_USER_ID,
    )

    agent_db.init_schema()

    # ==================== 测试 1: 构造自纠序列识别 ====================
    section("测试 1: 构造'犯错→自纠'消息序列 → 识别")
    # 模拟沙盒字体场景: run_python_code 失败 → AI thinking "字体缺失, 重新修复" → 重调成功
    tc_id_1 = "tc_" + uuid.uuid4().hex[:8]
    tc_id_2 = "tc_" + uuid.uuid4().hex[:8]

    messages = [
        HumanMessage(content="用 matplotlib 画个中文标题的图"),
        # 第一次调用: 成功生成图, 但中文字体缺失 (这里模拟 error 返回)
        AIMessage(
            content="",
            tool_calls=[{"name": "run_python_code", "args": {"code": "plt.savefig('x.png')"}, "id": tc_id_1}],
        ),
        ToolMessage(
            content=json.dumps({
                "type": "error",
                "msg": "Glyph missing from current font (Noto Sans CJK SC), 中文标签显示为方块",
                "stderr": "findfont: Font family 'Noto Sans CJK SC' not found",
            }),
            tool_call_id=tc_id_1,
        ),
        # AI 反思
        AIMessage(
            content="",
            additional_kwargs={"thinking_content": "已生成但中文字体缺失,标题会显示为方块。我来修复字体问题重新生成"},
        ),
        # 第二次调用: 用 shell 装字体
        AIMessage(
            content="",
            tool_calls=[{"name": "run_shell_command", "args": {"command": "pip install fonts-noto-cjk"}, "id": tc_id_2}],
        ),
        ToolMessage(
            content=json.dumps({"type": "success", "summary": "安装成功"}),
            tool_call_id=tc_id_2,
        ),
        AIMessage(content="图已修复"),
    ]

    corrections = detect_self_corrections(messages)
    overall_ok &= check(len(corrections) >= 1, f"识别到 {len(corrections)} 个自纠事件 (期望 >=1)")
    if corrections:
        c = corrections[0]
        overall_ok &= check(c["tool_name"] == "run_python_code", f"失败工具: {c['tool_name']}")
        overall_ok &= check("字体" in c["error_msg"] or "font" in c["error_msg"].lower(), f"错误信息含字体关键词")
        overall_ok &= check("修复" in c["correction_thinking"], f"thinking 含修复关键词")
        overall_ok &= check(c["fixed_by_tool"] == "run_shell_command", f"修复工具: {c['fixed_by_tool']}")
        overall_ok &= check(c["severity"] == "high", f"严重度: {c['severity']} (字体问题应为 high)")
        print(f"  识别详情: severity={c['severity']}, thinking={c['correction_thinking'][:60]}...")

    # ==================== 测试 2: 完整 detect + save 流程 ====================
    section("测试 2: scan_and_save_corrections → 写入全局记忆")
    # 清掉之前的同类教训 (避免去重干扰测试)
    existing = agent_db.load_user_memory(GLOBAL_LESSON_USER_ID)
    deleted = 0
    for m in existing:
        if "lesson_run_python_code" in m["key"]:
            agent_db.delete_user_memory(GLOBAL_LESSON_USER_ID, m["key"])
            deleted += 1
    print(f"  清理了 {deleted} 条旧 lesson 记忆 (仅 lesson_run_python_code_*)")

    # 清除去重缓存 (测试用)
    from backend.agent.memory import self_correction as sc_mod
    sc_mod._recent_lessons.clear()

    saved = asyncio.run(scan_and_save_corrections(messages, user_id="test_user"))
    overall_ok &= check(len(saved) >= 1, f"沉淀了 {len(saved)} 条教训")
    if saved:
        s = saved[0]
        overall_ok &= check(s["severity"] == "high", f"沉淀的严重度: {s['severity']}")

    # 验证记忆库
    global_memories = agent_db.load_user_memory(GLOBAL_LESSON_USER_ID)
    lesson_memories = [m for m in global_memories if m["category"] == "lesson"]
    print(f"  全局记忆共 {len(global_memories)} 条, 其中 lesson 类 {len(lesson_memories)} 条")
    overall_ok &= check(len(lesson_memories) >= 1, f"lesson 记忆数: {len(lesson_memories)}")
    if lesson_memories:
        lm = lesson_memories[-1]
        print(f"  最新教训: key={lm['key'][:50]}")
        print(f"    value: {lm['value'][:120]}...")

    # 测试去重: 再扫一次, 不应写入
    saved_again = asyncio.run(scan_and_save_corrections(messages, user_id="test_user"))
    overall_ok &= check(len(saved_again) == 0, f"去重生效: 第二次扫描写入 {len(saved_again)} 条 (期望 0)")

    # ==================== 测试 3: system_prompt 注入验证 ====================
    section("测试 3: build_user_profile_text 注入全局教训")
    from backend.agent.memory.memory_context import build_user_profile_text
    profile = build_user_profile_text("test_user")
    overall_ok &= check("教训" in profile or "lesson" in profile.lower(), "用户画像文本含教训记忆")
    overall_ok &= check("字体" in profile or "font" in profile.lower(), "用户画像文本含字体教训内容")
    print(f"  用户画像文本 (前 300 字):\n    {profile[:300]}")

    # ==================== 测试 4: 负向测试 (无自纠不应误报) ====================
    section("测试 4: 负向测试 (无自纠序列不应误报)")
    tc_neg = "tc_" + uuid.uuid4().hex[:8]
    clean_messages = [
        HumanMessage(content="列出数据库表"),
        AIMessage(content="", tool_calls=[{"name": "list_database_tables", "args": {}, "id": tc_neg}]),
        ToolMessage(content=json.dumps({"type": "success", "summary": "5 张表"}), tool_call_id=tc_neg),
        AIMessage(content="数据库有 5 张表"),
    ]
    neg_corrections = detect_self_corrections(clean_messages)
    overall_ok &= check(len(neg_corrections) == 0, f"正常序列误报数: {len(neg_corrections)} (期望 0)")

    # 负向测试 2: 失败但没修复 (Agent 放弃了)
    tc_fail = "tc_" + uuid.uuid4().hex[:8]
    give_up_messages = [
        HumanMessage(content="下载图层"),
        AIMessage(content="", tool_calls=[{"name": "download_raster_layer", "args": {"name": "x"}, "id": tc_fail}]),
        ToolMessage(content=json.dumps({"type": "error", "msg": "图层不存在"}), tool_call_id=tc_fail),
        AIMessage(content="抱歉, 该图层不存在, 无法下载"),  # 没有修复动作
    ]
    give_up_corrections = detect_self_corrections(give_up_messages)
    overall_ok &= check(len(give_up_corrections) == 0, f"放弃序列误报数: {len(give_up_corrections)} (期望 0)")

    # ==================== 总结 ====================
    section("总结")
    sym = "✓ 全部通过" if overall_ok else "✗ 有失败项"
    print(f"  结果: {sym}")
    print(f"  全局记忆总数: {len(agent_db.load_user_memory(GLOBAL_LESSON_USER_ID))}")
    return 0 if overall_ok else 1


if __name__ == "__main__":
    sys.exit(main())
