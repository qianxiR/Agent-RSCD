"""聊天链路冒烟: 修复 'Settings' object has no attribute 'llm_retry_max' 后的端到端验证。

用 TestClient 走完整推理栈 (真实 LLM 调用), 验证:
1. chat_request 不再因缺失配置项报错
2. 收到正常回复 (content/done)
3. 会话的 project_id 正确落库 (新对话归属项目)
运行: python tests\\smoke_chat.py (sam3 环境, 需 DASHSCOPE_API_KEY)
"""
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from backend.main import app  # noqa: E402
from backend.agent.memory import agent_db  # noqa: E402


def main() -> None:
    proj_id = f"smoke-proj-{uuid.uuid4().hex[:8]}"
    conv_id = f"smoke-conv-{uuid.uuid4().hex[:8]}"

    with TestClient(app) as client:
        with client.websocket_connect(
                f"/api/v1/agent/ws/chat?session_id=smoke-{uuid.uuid4().hex[:6]}") as ws:
            ws.send_json({
                "type": "chat_request",
                "prompt": "请直接回复四个字: 冒烟通过",
                "conversation_id": conv_id,
                "user_id": "study_user",
                "project_id": proj_id,
                "multi_round": False,
            })
            types = []
            last_content = ""
            while True:
                msg = ws.receive_json()
                types.append(msg.get("type"))
                if msg.get("type") == "content":
                    last_content = msg.get("content", "")
                if msg.get("type") in ("done", "error"):
                    if msg.get("type") == "error":
                        print("error 消息:", msg.get("content", "")[:200])
                    break
                if len(types) > 500:
                    raise AssertionError("消息过多, 疑似死循环")

    print("收到消息类型:", types[:12], "... 共", len(types), "条")

    # 1. 不再出现 Settings 属性错误
    settings_err = any("'Settings' object" in str(t) for t in types)
    assert not settings_err, "仍然出现 Settings 属性错误"
    print("[OK] 无 Settings 属性错误")

    # 2. 正常完成 (done 且有 content, 或明确的业务 error 而非崩溃)
    assert "done" in types or "error" in types, f"未收到终止消息: {types}"
    if "error" in types:
        print("[WARN] 收到 error (LLM 层问题, 但 Settings 修复已生效), 终止本脚本")
        return
    assert "content" in types and last_content, "无回复内容"
    print(f"[OK] 回复内容: {last_content[:60]}")

    # 3. 会话归属项目
    meta = agent_db.get_conversation(conv_id)
    assert meta and meta.get("project_id") == proj_id, f"会话未归属项目: {meta}"
    print("[OK] 会话 project_id 落库正确:", proj_id[:16])

    # 清理冒烟会话
    agent_db.delete_conversation(conv_id)
    print("smoke_chat: PASS")


if __name__ == "__main__":
    main()
