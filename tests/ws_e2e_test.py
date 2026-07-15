"""
WebSocket 端到端联调 (Phase G-3)
模拟前端: 上传 t1/t2 → 发 chat_request "对比变化" → 收 detect_change 结果 → 验证任务日志

前置: 后端已在 localhost:8021 (或指定端口) 运行, DASHSCOPE_API_KEY 已配置.
运行:
  conda activate sam3
  python tests/ws_e2e_test.py [--port 8021]
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

T1 = str(ROOT / "tests" / "data" / "test_t1.tif")
T2 = str(ROOT / "tests" / "data" / "test_t2.tif")


async def upload_images(session, base_url, conv_id):
    """上传 t1/t2 到 /api/v1/samseg/upload, 返回 paths."""
    form = aiohttp.FormData()
    form.add_field("images", open(T1, "rb"), filename="test_t1.tif")
    form.add_field("images", open(T2, "rb"), filename="test_t2.tif")
    form.add_field("conversation_id", conv_id)
    async with session.post(f"{base_url}/api/v1/samseg/upload", data=form) as resp:
        data = await resp.json()
        print(f"  [upload] status={data.get('status')}, paths={len(data.get('paths', []))}")
        return data.get("paths", [])


async def run_ws_chat(base_url, conv_id, prompt, paths, timeout=300):
    """
    通过 WebSocket 发 chat_request, 收集所有消息直到 done.
    返回收到的消息列表.
    """
    ws_url = f"{base_url.replace('http', 'ws')}/api/v1/agent/ws/chat"
    messages = []
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(ws_url, heartbeat=30) as ws:
            # 把图片路径拼进 prompt
            full_prompt = prompt
            if paths and len(paths) >= 2:
                full_prompt = (
                    f"{prompt}\nT1 时相影像路径: {paths[0]}\n"
                    f"T2 时相影像路径: {paths[1]}"
                )
            req = {
                "type": "chat_request",
                "prompt": full_prompt,
                "conversation_id": conv_id,
                "user_id": "ws_e2e_tester",
            }
            print(f"  [ws] send chat_request: {prompt[:60]}...")
            await ws.send_json(req)

            start = time.time()
            async for msg in ws:
                if time.time() - start > timeout:
                    print(f"  [ws] ⚠ 超时 {timeout}s, 强制结束")
                    break
                if msg.type == aiohttp.WSMsgType.TEXT:
                    data = json.loads(msg.data)
                    mtype = data.get("type")
                    messages.append(data)
                    cidx = (data.get("conversation_id") or "")[:8]
                    if mtype == "thinking":
                        print(f"  [ws] 💭 thinking ({cidx}): {data.get('content', '')[:80]}")
                    elif mtype == "tool_call":
                        tools = [t.get("name", "?") for t in data.get("tool_calls", [])]
                        print(f"  [ws] 🔧 tool_call ({cidx}): {tools}")
                    elif mtype == "frontend_action":
                        et = data.get("event_type")
                        edata = data.get("event_data", {})
                        keys = list(edata.keys())[:6]
                        print(f"  [ws] 🎨 frontend_action ({cidx}): event_type={et}, params keys={keys}")
                        # 自动回 tool_result (前端执行完毕)
                        request_id = data.get("request_id")
                        if request_id:
                            await ws.send_json({
                                "type": "tool_result",
                                "request_id": request_id,
                                "status": "success",
                                "data": {"loaded": True},
                            })
                    elif mtype == "content":
                        print(f"  [ws] 💬 content ({cidx}): {data.get('content', '')[:200]}")
                    elif mtype == "done":
                        print(f"  [ws] ✅ done ({cidx})")
                        break
                    elif mtype == "error":
                        print(f"  [ws] ❌ error ({cidx}): {data.get('content', '')[:200]}")
                        break
                    elif mtype == "session_started":
                        print(f"  [ws] 📌 session_started: conv={data.get('conversation_id', '')[:12]}...")
                    else:
                        print(f"  [ws] 📨 {mtype} ({cidx})")
                elif msg.type == aiohttp.WSMsgType.ERROR:
                    print(f"  [ws] ❌ connection error")
                    break
    return messages


def verify_task_log(base_url, conv_id):
    """查 ai_task / task_log 是否有记录."""
    import urllib.request
    try:
        url = f"{base_url}/api/v1/tasks?conversation_id={conv_id}&limit=10"
        with urllib.request.urlopen(url, timeout=5) as resp:
            data = json.loads(resp.read())
        tasks = data.get("tasks", [])
        print(f"  [task] 该会话共 {len(tasks)} 个任务:")
        for t in tasks:
            elapsed = "-"
            if t.get("started_at") and t.get("finished_at"):
                try:
                    s = time.mktime(time.strptime(t["started_at"][:19], "%Y-%m-%dT%H:%M:%S"))
                    e = time.mktime(time.strptime(t["finished_at"][:19], "%Y-%m-%dT%H:%M:%S"))
                    elapsed = f"{e-s:.1f}s"
                except Exception:
                    pass
            print(f"    - #{t['id']} {t.get('tool_name')} status={t['status']} elapsed={elapsed}")
            if t.get("error"):
                print(f"      error: {t['error'][:120]}")
        return tasks
    except Exception as e:
        print(f"  [task] 查询失败: {e}")
        return []


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8021)
    parser.add_argument("--prompt", default="请对这两张双时相影像做变化检测, 并告诉我变化业务类型")
    args = parser.parse_args()

    base_url = f"http://localhost:{args.port}"
    conv_id = f"ws_e2e_{int(time.time())}"

    print("=" * 60)
    print(f"  WebSocket E2E 联调 (port={args.port}, conv={conv_id[:18]})")
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
    print("✓ 后端可达")

    # 1. 上传
    print("\n--- Step 1: 上传 t1/t2 ---")
    async with aiohttp.ClientSession() as session:
        paths = await upload_images(session, base_url, conv_id)
    if len(paths) < 2:
        print(f"❌ 上传失败, 仅 {len(paths)} 张")
        return 1

    # 2. WS 对话
    print("\n--- Step 2: WS 对话 (变化检测) ---")
    messages = await run_ws_chat(base_url, conv_id, args.prompt, paths, timeout=300)

    # 3. 验证任务日志
    print("\n--- Step 3: 验证任务日志 ---")
    tasks = verify_task_log(base_url, conv_id)

    # 4. 统计
    print("\n--- 总结 ---")
    fa_count = sum(1 for m in messages if m.get("type") == "frontend_action")
    has_done = any(m.get("type") == "done" for m in messages)
    has_error = any(m.get("type") == "error" for m in messages)
    print(f"  收到消息: {len(messages)} 条")
    print(f"  frontend_action: {fa_count} 个")
    print(f"  任务记录: {len(tasks)} 条")
    print(f"  done: {'✓' if has_done else '✗'}")
    print(f"  error: {'✓' if has_error else '✗'}")

    # 5. 检查是否有 detect_change / 业务类型
    detect_call = any(
        m.get("type") == "tool_call" and
        any(t.get("name") == "detect_change" for t in m.get("tool_calls", []))
        for m in messages
    )
    print(f"  调用 detect_change: {'✓' if detect_call else '✗ (LLM 可能选了别的工具或反问)'}")

    return 0 if has_done and not has_error else 1


if __name__ == "__main__":
    rc = asyncio.run(main())
    sys.exit(rc)
