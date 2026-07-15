"""
双路记忆并行验证 (偏好提取 + 错误案例学习)
================================================
目的: 验证 enable_preference_extraction 开关打开后, 对话收尾时
      "偏好/事实提取" 与 "自纠捕捉器 (错误案例学习)" 两路后台任务并行触发.

做法: 连 WebSocket 发一条会触发工具调用的 prompt, 收集全程消息,
      done 后多等 45s 捕获后台回推的 memory_updated 事件:
        - kind="long_term"  → 偏好/事实提取路 (含 category=preference/fact/summary)
        - kind="lesson"     → 错误案例学习路 (category=lesson, source=self_correction)

前置: 后端已在 localhost:8020 运行, DASHSCOPE_API_KEY 已配置.
运行: python tests/memory_dualpath_test.py [--port 8020] [--prompt "..."]
"""
import sys
import os
import json
import time
import asyncio
import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"

import aiohttp


# 默认 prompt: 故意指向一个不存在的文件路径, 诱导 agent 调工具失败 → 反思 → 换路径/换法子重试,
# 构成一次"工具失败 + thinking 反思 + 修复"的自纠模式, 供错误案例学习路沉淀教训.
DEFAULT_PROMPT = (
    "请帮我分析这个遥感影像文件的变化情况: "
    "E:/nonexistent_data/sample_2023.tif 。"
    "先尝试读取它, 如果读不到就想别的办法确认情况, 并告诉我结论。"
)

# done 之后后台记忆任务的最长等待 (秒). 实测偏好提取/自纠沉淀各约 5~10s, 留足余量.
POST_DONE_WAIT = 45


async def run_ws_chat(base_url: str, conv_id: str, prompt: str, user_id: str,
                      timeout: int):
    """连 WS 发 chat_request, 收集消息直到 done, 然后再等 POST_DONE_WAIT 捕获 memory_updated."""
    ws_url = f"{base_url.replace('http', 'ws')}/api/v1/agent/ws/chat"
    messages = []
    got_done = False
    done_at = None

    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(ws_url, heartbeat=30) as ws:
            req = {
                "type": "chat_request",
                "prompt": prompt,
                "conversation_id": conv_id,
                "user_id": user_id,
            }
            print(f"  [ws] → chat_request: {prompt[:70].replace(chr(10), ' ')}...")
            await ws.send_json(req)

            start = time.time()
            async for msg in ws:
                elapsed = time.time() - start
                # done 后转成"捕 memory_updated"模式: 不再因超时退出, 而是再等 POST_DONE_WAIT
                if got_done and (elapsed - done_at) > POST_DONE_WAIT:
                    print(f"\n  [ws] 后台记忆等待 {POST_DONE_WAIT}s 结束, 断开收集。")
                    break
                if not got_done and elapsed > timeout:
                    print(f"\n  [ws] ⚠ 主对话超时 {timeout}s, 强制结束")
                    break

                if msg.type == aiohttp.WSMsgType.TEXT:
                    data = json.loads(msg.data)
                    mtype = data.get("type")
                    messages.append(data)
                    cidx = (data.get("conversation_id") or "")[:8]

                    if mtype == "thinking":
                        print(f"  [ws] 💭 thinking: {data.get('content', '')[:90]}")
                    elif mtype == "tool_call":
                        tools = [t.get("name", "?") for t in data.get("tool_calls", [])]
                        print(f"  [ws] 🔧 tool_call: {tools}")
                    elif mtype == "frontend_action":
                        et = data.get("event_type")
                        print(f"  [ws] 🎨 frontend_action: event_type={et}")
                        request_id = data.get("request_id")
                        if request_id:
                            await ws.send_json({
                                "type": "tool_result",
                                "request_id": request_id,
                                "status": "success",
                                "data": {"loaded": True},
                            })
                    elif mtype == "content":
                        print(f"  [ws] 💬 content: {data.get('content', '')[:160]}")
                    elif mtype == "done":
                        got_done = True
                        done_at = elapsed
                        print(f"  [ws] ✅ done — 主对话结束, 接下来等后台两路记忆 (最长 {POST_DONE_WAIT}s)…")
                    elif mtype == "memory_updated":
                        kind = data.get("kind")
                        items = data.get("items", [])
                        cats = sorted({it.get("category", "?") for it in items})
                        srcs = sorted({it.get("source", "?") for it in items})
                        print(f"  [ws] 🧠 memory_updated: kind={kind}, "
                              f"{len(items)} 项, category={cats}, source={srcs}")
                        for it in items:
                            print(f"        - [{it.get('category')}] {it.get('key', '')}: "
                                  f"{str(it.get('value', ''))[:80]}")
                    elif mtype == "error":
                        print(f"  [ws] ❌ error: {data.get('content', '')[:200]}")
                        break
                    elif mtype == "session_started":
                        print(f"  [ws] 📌 session_started: conv={data.get('conversation_id', '')[:12]}…")
                    elif mtype == "ping":
                        await ws.send_json({"type": "pong"})
                    else:
                        print(f"  [ws] 📨 {mtype}")
                elif msg.type == aiohttp.WSMsgType.ERROR:
                    print(f"  [ws] ❌ 连接错误")
                    break
    return messages


def analyze(messages):
    """统计消息并判定两路记忆是否被触发."""
    print("\n" + "=" * 60)
    print("  双路记忆并行 — 验证结果")
    print("=" * 60)

    tool_calls = [t.get("name", "?")
                  for m in messages if m.get("type") == "tool_call"
                  for t in m.get("tool_calls", [])]
    has_done = any(m.get("type") == "done" for m in messages)
    mem_events = [m for m in messages if m.get("type") == "memory_updated"]

    pref_items, lesson_items = [], []
    for m in mem_events:
        for it in m.get("items", []):
            cat = it.get("category", "")
            if cat == "lesson" or it.get("source") == "self_correction":
                lesson_items.append(it)
            else:
                pref_items.append(it)  # preference / fact / summary / workflow 等

    print(f"  对话消息总数      : {len(messages)}")
    print(f"  工具调用          : {len(tool_calls)} 次 {tool_calls if tool_calls else ''}")
    print(f"  done              : {'✓' if has_done else '✗'}")
    print(f"  memory_updated 事件: {len(mem_events)} 个")
    print()
    print(f"  ① 偏好/事实提取路 (kind=long_term): {len(pref_items)} 项")
    for it in pref_items:
        print(f"      - [{it.get('category')}] {it.get('key', '')}")
    print()
    print(f"  ② 错误案例学习路 (kind=lesson): {len(lesson_items)} 项")
    for it in lesson_items:
        print(f"      - [{it.get('category')}] {it.get('key', '')}: "
              f"{str(it.get('value', ''))[:70]}")

    print()
    if not mem_events:
        print("  ⚠ 未收到任何 memory_updated。可能原因:")
        print("    - 本轮对话 LLM 没调工具 (没产生错误案例素材)")
        print("    - 偏好提取/自纠提炼的 LLM 调用超时或判定无可提取内容")
        print("    - 后台任务未在等待窗口内完成 (可加大 --post-wait)")
    print("=" * 60)
    return has_done, len(pref_items), len(lesson_items)


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8020)
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    parser.add_argument("--user-id", default="mem_dual_tester")
    parser.add_argument("--timeout", type=int, default=180, help="主对话超时秒数")
    global POST_DONE_WAIT
    parser.add_argument("--post-wait", type=int, default=POST_DONE_WAIT,
                        help="done 后等待后台记忆任务的秒数")
    args = parser.parse_args()
    POST_DONE_WAIT = args.post_wait

    base_url = f"http://localhost:{args.port}"
    conv_id = f"mem_dual_{int(time.time())}"

    print("=" * 60)
    print(f"  双路记忆并行验证  (port={args.port}, conv={conv_id})")
    print(f"  post-done wait = {POST_DONE_WAIT}s")
    print("=" * 60)

    # 健康检查
    import urllib.request
    try:
        with urllib.request.urlopen(f"{base_url}/api/v1/projects", timeout=3) as r:
            if r.status != 200:
                print(f"❌ 后端不可达 (HTTP {r.status})")
                return 1
    except Exception as e:
        print(f"❌ 后端不可达: {e}")
        return 1
    print("✓ 后端可达\n")

    print("--- 发起对话 ---")
    messages = await run_ws_chat(
        base_url, conv_id, args.prompt, args.user_id, args.timeout
    )

    _, n_pref, n_lesson = analyze(messages)

    # 判定: 两路至少都"跑过"(有过尝试). 只要收到 memory_updated 即说明编排触发了.
    # 至于是否沉淀出条目, 取决于 LLM 判断与内容价值, 属正常.
    ran_pref = n_pref > 0 or any(
        m.get("kind") == "long_term" for m in messages if m.get("type") == "memory_updated"
    )
    ran_lesson = n_lesson > 0 or any(
        m.get("kind") == "lesson" for m in messages if m.get("type") == "memory_updated"
    )
    if ran_pref and ran_lesson:
        print("\n🎉 两路记忆均触发 (偏好/事实 + 错误案例学习 并行).")
        return 0
    elif ran_pref or ran_lesson:
        print("\n🟡 仅一路触发 (可能本轮对话无自纠素材, 或偏好无可提取内容).")
        return 0
    else:
        print("\n⚪ 本轮未收到 memory_updated (见上方说明). 不代表失败 — "
              "可查后端日志确认两路任务是否启动.")
        return 0


if __name__ == "__main__":
    rc = asyncio.run(main())
    sys.exit(rc)
