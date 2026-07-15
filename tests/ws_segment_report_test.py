"""
WebSocket 真实分割 → report_agent 自动派发 端到端验证。

验证 B2 解耦: 经 WS 发真实 chat_request (分割), chat_service 在 ReAct 循环里
触发 hook → 自动派发 report_agent worker → worker 完成后推 frontend_action/download
事件到前端。这是 chat_service_report_dispatch_test (mock) 与 e2e (直调) 都未覆盖的
真实运行时链路。

前置: 后端 localhost:8020 运行 (sam3), GeoServer + DB 就绪, DASHSCOPE_API_KEY 配置。
运行:
  conda run -n sam3 python tests/ws_segment_report_test.py
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

# 单张分割测试影像 (tests/data, playwright-usage 文档登记的测试数据)
IMAGE = str(ROOT / "tests" / "data" / "r000_c006_t1.tif")


async def upload_one(session, base_url, conv_id):
    """
    入参: session, base_url, conv_id。
    方法: POST /api/v1/samseg/upload 单张影像, 返回宿主机绝对路径列表。
    出参: list[str], 上传后的影像路径。
    """
    form = aiohttp.FormData()
    form.add_field("images", open(IMAGE, "rb"), filename="r000_c006_t1.tif")
    form.add_field("conversation_id", conv_id)
    async with session.post(f"{base_url}/api/v1/samseg/upload", data=form) as resp:
        data = await resp.json()
        print(f"  [upload] status={data.get('status')}, paths={len(data.get('paths', []))}")
        return data.get("paths", [])


async def run_ws_chat(base_url, conv_id, prompt, image_path, timeout=600):
    """
    入参: base_url, conv_id, prompt, image_path, timeout。
    方法:
      - WS 发 chat_request (prompt 含影像路径), 收集所有消息。
      - 自动回 frontend_action 的 tool_result (render_image 等前端执行回执)。
      - done 不立即退出: report worker 是异步派发, download 事件可能晚于对话 done。
        持续等到收到 report download (带 report_task_id) 或总超时。
    出参: 收到的消息列表。
    """
    ws_url = f"{base_url.replace('http', 'ws')}/api/v1/agent/ws/chat"
    messages = []
    full_prompt = f"{prompt}\n影像路径: {image_path}"
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(ws_url, heartbeat=30) as ws:
            await ws.send_json({
                "type": "chat_request",
                "prompt": full_prompt,
                "conversation_id": conv_id,
                "user_id": "ws_seg_report_tester",
            })
            print(f"  [ws] send chat_request: {prompt[:60]}")
            start = time.time()
            async for msg in ws:
                if time.time() - start > timeout:
                    print(f"  [ws] ⚠ 总超时 {timeout}s"); break
                if msg.type == aiohttp.WSMsgType.TEXT:
                    data = json.loads(msg.data)
                    mtype = data.get("type")
                    messages.append(data)
                    cidx = (data.get("conversation_id") or "")[:8]
                    if mtype == "thinking":
                        print(f"  [ws] 💭 ({cidx}): {data.get('content','')[:90]}")
                    elif mtype == "tool_call":
                        tools = [t.get("name", "?") for t in data.get("tool_calls", [])]
                        print(f"  [ws] 🔧 tool_call ({cidx}): {tools}")
                    elif mtype == "frontend_action":
                        et = data.get("event_type")
                        edata = data.get("event_data", {}) or {}
                        url = edata.get("url", "")
                        print(f"  [ws] 🎨 frontend_action ({cidx}): event_type={et}, url={url[:60]}")
                        if data.get("report_task_id"):
                            print(f"  [ws] ★★★ report download 事件! task_id={data.get('report_task_id')}")
                            break  # 收到 report download, 验证成功, 退出
                        rid = data.get("request_id")
                        if rid and et != "download":
                            await ws.send_json({"type": "tool_result", "request_id": rid, "status": "success", "data": {"loaded": True}})
                    elif mtype == "content":
                        print(f"  [ws] 💬 ({cidx}): {data.get('content','')[:150]}")
                    elif mtype == "done":
                        print(f"  [ws] ✅ done ({cidx}) — 继续等异步 report download...")
                    elif mtype == "error":
                        print(f"  [ws] ❌ error ({cidx}): {data.get('content','')[:200]}"); break
                    elif mtype == "session_started":
                        print(f"  [ws] 📌 session_started conv={data.get('conversation_id','')[:12]}")
                    else:
                        print(f"  [ws] 📨 {mtype} ({cidx})")
                elif msg.type == aiohttp.WSMsgType.ERROR:
                    print(f"  [ws] ❌ conn error"); break
    return messages


async def main():
    """
    入参: --port, --prompt (命令行)。
    方法: 上传单张 → WS 分割对话 → 收事件 → 统计 segment/render_image/report download。
    出参: 进程退出码 (0=report 自动派发成功, 1=失败)。
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8020)
    parser.add_argument("--prompt", default="请对这张影像做语义分割, 提取建筑物")
    args = parser.parse_args()
    base_url = f"http://localhost:{args.port}"
    conv_id = f"ws_seg_{int(time.time())}"

    print("=" * 64)
    print(f"  WS 分割 → report_agent 自动派发 验证 (conv={conv_id[:16]})")
    print("=" * 64)

    import urllib.request
    try:
        with urllib.request.urlopen(f"{base_url}/api/v1/projects", timeout=3) as r:
            assert r.status == 200
    except Exception as e:
        print(f"❌ 后端不可达: {e}"); return 1
    print("✓ 后端可达")

    if not Path(IMAGE).is_file():
        print(f"❌ 测试影像不存在: {IMAGE}"); return 1

    print("\n--- Step 1: 上传单张影像 ---")
    async with aiohttp.ClientSession() as session:
        paths = await upload_one(session, base_url, conv_id)
    if not paths:
        print("❌ 上传失败"); return 1

    print("\n--- Step 2: WS 真实分割对话 ---")
    messages = await run_ws_chat(base_url, conv_id, args.prompt, paths[0], timeout=600)

    seg_called = any(
        m.get("type") == "tool_call" and any(t.get("name") == "segment_image" for t in m.get("tool_calls", []))
        for m in messages
    )
    render_image = any(m.get("type") == "frontend_action" and m.get("event_type") == "render_image" for m in messages)
    report_download = any(m.get("type") == "frontend_action" and m.get("report_task_id") for m in messages)
    has_done = any(m.get("type") == "done" for m in messages)
    has_error = any(m.get("type") == "error" for m in messages)

    print("\n--- 总结 ---")
    print(f"  segment_image 调用:        {'✓' if seg_called else '✗'}")
    print(f"  render_image 推送:         {'✓' if render_image else '✗'}")
    print(f"  ★ report download 自动派发: {'✓ 已收到 (B2 链路通)' if report_download else '✗ 未收到'}")
    print(f"  done: {'✓' if has_done else '✗'}  error: {'✓' if has_error else '✗'}")

    return 0 if (seg_called and report_download) else 1


if __name__ == "__main__":
    rc = asyncio.run(main())
    sys.exit(rc)
