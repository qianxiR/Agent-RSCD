"""内部接口 (/internal/*) 自测: 令牌校验 + 各端点转发行为。

不依赖真实服务进程, 用 FastAPI TestClient + lifespan 直测 app。
运行: python tests\\test_internal_api.py (sam3 环境)
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from backend.config import settings  # noqa: E402
from backend.main import app  # noqa: E402


def main() -> None:
    with TestClient(app) as client:
        headers = {"X-Internal-Token": settings.internal_api_token}

        # 1. 令牌缺失 / 错误 → 401
        r = client.get("/internal/tasks")
        assert r.status_code == 401, f"无 token 应 401, 实际 {r.status_code}"
        r = client.get("/internal/tasks", headers={"X-Internal-Token": "wrong"})
        assert r.status_code == 401, f"错 token 应 401, 实际 {r.status_code}"
        print("token 校验: PASS")

        # 2. 会话列表 (空库也返回 200 + conversations 键)
        r = client.get("/internal/conversations", headers=headers,
                       params={"user_id": "study_user"})
        assert r.status_code == 200, r.text
        assert "conversations" in r.json()
        print("internal conversations: PASS")

        # 3. 任务列表
        r = client.get("/internal/tasks", headers=headers)
        assert r.status_code == 200 and r.json().get("status") == "success", r.text
        print("internal tasks: PASS")

        # 4. detach-project (不存在项目 → detached 0)
        r = client.post("/internal/conversations/detach-project", headers=headers,
                        json={"project_id": "no-such-project"})
        assert r.status_code == 200 and r.json() == {"status": "success", "detached": 0}, r.text
        print("internal detach-project: PASS")

        # 5. 会话创建 → PATCH 重命名 → 解绑 → 删除 (全链路, 覆盖代理端点全集)
        from backend.agent.memory import agent_db
        conv_id = "it-selftest-conv-001"
        assert agent_db.create_conversation(conv_id, "study_user", title="自测会话",
                                            project_id="it-selftest-proj")
        r = client.post("/internal/conversations/detach-project", headers=headers,
                        json={"project_id": "it-selftest-proj"})
        assert r.json()["detached"] >= 1, r.text
        r = client.patch(f"/internal/conversations/{conv_id}", headers=headers,
                         json={"title": "改名后"})
        assert r.status_code == 200 and r.json()["status"] == "success", r.text
        r = client.get(f"/internal/conversations/{conv_id}/messages", headers=headers)
        assert r.status_code == 200 and r.json()["conversation_id"] == conv_id, r.text
        r = client.get(f"/internal/conversations/{conv_id}/branches", headers=headers)
        assert r.status_code == 200 and r.json()["status"] == "success", r.text
        r = client.delete(f"/internal/conversations/{conv_id}", headers=headers)
        assert r.status_code == 200 and r.json()["status"] == "success", r.text
        print("internal conversations 全链路: PASS")

        # 6. WS token 解码辅助 (无验签, 仅 payload)
        from backend.api.ws import _user_id_from_token
        assert _user_id_from_token(None) is None
        assert _user_id_from_token("garbage") is None
        import base64
        import json
        payload = base64.urlsafe_b64encode(
            json.dumps({"sub": "user-abc"}).encode()).decode().rstrip("=")
        fake_jwt = f"header.{payload}.signature"
        assert _user_id_from_token(fake_jwt) == "user-abc"
        print("ws token 解码: PASS")

    print("test_internal_api: PASS")


if __name__ == "__main__":
    main()
