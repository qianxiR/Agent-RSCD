"""双服务集成自测: Java 业务服务 (8081) + Python AI 服务 (8020) 全链路。

前置: 两个服务均已启动 (java -jar backend-java/target/rsiis-business.jar;
      python -m uvicorn backend.main:app --port 8020)。
运行: python tests\\integration_dual_service.py (sam3 环境)
"""
import json
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

JAVA = "http://127.0.0.1:8081"
PYTHON = "http://127.0.0.1:8020"

PASSED = []


def req(method, url, body=None, headers=None):
    """统一请求: 返回 (status, json)。非 2xx 不抛异常, 供断言状态码。"""
    data = json.dumps(body).encode("utf-8") if body is not None else None
    r = urllib.request.Request(url, data=data, method=method,
                               headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except Exception:
            return e.code, {}


def check(name, cond, detail=""):
    assert cond, f"{name} 失败: {detail}"
    PASSED.append(name)
    print(f"[OK] {name}")


def cleanup_test_projects():
    """清理本脚本历史残留的测试项目 (集成测试项目/改名后的项目/it-folder 目录项目)。"""
    import psycopg2
    from backend.config import settings
    try:
        conn = psycopg2.connect(
            host="localhost", port=5432, dbname="rsiis_business",
            user=settings.postgres_user, password=settings.postgres_password,
            connect_timeout=3)
    except Exception:
        return
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM project WHERE folder_path LIKE 'D:/it-folder-%' "
                        "OR name IN ('集成测试项目', '改名后的项目')")
        conn.commit()
    finally:
        conn.close()


def main():
    cleanup_test_projects()
    # ---------- 1. 认证 ----------
    s, body = req("POST", f"{JAVA}/api/v1/auth/login",
                  {"username": "admin", "password": "admin123"})
    check("login 成功", s == 200 and body.get("status") == "success" and body.get("token"), body)
    token = body["token"]

    s, body = req("POST", f"{JAVA}/api/v1/auth/login",
                  {"username": "admin", "password": "wrong"})
    check("login 错误口令拒绝", body.get("status") == "error", body)

    auth = {"Authorization": f"Bearer {token}"}

    # ---------- 2. 项目 CRUD (Java 直管, snake_case 字段 + 中文) ----------
    s, body = req("POST", f"{JAVA}/api/v1/projects?user_id=study_user",
                  {"name": "集成测试项目", "description": "双服务自测", "folder_path": ""},
                  headers=auth)
    check("项目创建 (含中文)", s == 200 and body.get("status") == "success", body)
    pid = body["project_id"]

    # folder_path UPSERT: 同路径两次创建 → 复用同一 id
    folder = f"D:/it-folder-{uuid.uuid4().hex[:8]}"
    s, b1 = req("POST", f"{JAVA}/api/v1/projects?user_id=study_user",
                {"name": "目录项目A", "description": "", "folder_path": folder}, headers=auth)
    s, b2 = req("POST", f"{JAVA}/api/v1/projects?user_id=study_user",
                {"name": "目录项目B", "description": "", "folder_path": folder}, headers=auth)
    check("folder_path UPSERT 复用", b1["project_id"] == b2["project_id"], (b1, b2))
    folder_pid = b1["project_id"]

    s, body = req("GET", f"{JAVA}/api/v1/projects?user_id=study_user", headers=auth)
    rows = body.get("projects", [])
    mine = next(p for p in rows if p["id"] == pid)
    check("项目列表 snake_case 字段", "created_at" in mine and "folder_path" in mine, mine)
    check("项目列表含中文名", mine["name"] == "集成测试项目", mine)

    s, body = req("PATCH", f"{JAVA}/api/v1/projects/{pid}",
                  {"name": "改名后的项目"}, headers=auth)
    check("项目改名", body.get("status") == "success", body)

    s, body = req("PATCH", f"{JAVA}/api/v1/projects/no-such-id", {"name": "x"}, headers=auth)
    check("改不存在项目报错", body.get("status") == "error", body)

    # ---------- 3. 会话/任务代理 (Java → Python /internal) ----------
    s, body = req("GET", f"{JAVA}/api/v1/conversations?user_id=study_user", headers=auth)
    check("会话列表代理", s == 200 and "conversations" in body, body)

    s, body = req("GET", f"{JAVA}/api/v1/tasks?limit=5", headers=auth)
    check("任务列表代理", s == 200 and body.get("status") == "success", body)

    s, body = req("GET", f"{JAVA}/api/v1/tasks/999999", headers=auth)
    check("任务不存在透传错误语义", body.get("status") == "error", body)

    s, body = req("GET", f"{JAVA}/api/v1/conversations/it-no-such-conv/branches", headers=auth)
    check("会话分支不存在不 5xx", s == 200 and body.get("status") == "success", body)

    # ---------- 4. workspace (Java 直管) ----------
    s, body = req("GET", f"{JAVA}/api/v1/workspace/list?roots=C:/Windows&depth=2")
    check("workspace 系统目录拒绝", body["roots"][0]["error"] == "系统目录，拒绝访问", body)

    from urllib.parse import quote
    real_dir = quote(str(ROOT / "agent-files"))
    s, body = req("GET", f"{JAVA}/api/v1/workspace/list?roots={real_dir}&depth=2")
    check("workspace 正常目录浏览", body["roots"][0]["error"] is None, body)

    # ---------- 5. 删项目 → 会话解绑 (跨服务协作) ----------
    from backend.agent.memory import agent_db
    conv_id = f"it-dual-{uuid.uuid4().hex[:8]}"
    assert agent_db.create_conversation(conv_id, "study_user", title="解绑验证", project_id=pid)
    s, body = req("DELETE", f"{JAVA}/api/v1/projects/{pid}", headers=auth)
    check("删项目成功", body.get("status") == "success", body)
    meta = agent_db.get_conversation(conv_id)
    check("删项目后会话解绑 (project_id 置空)", not (meta or {}).get("project_id"), meta)
    agent_db.delete_conversation(conv_id)
    req("DELETE", f"{JAVA}/api/v1/projects/{folder_pid}", headers=auth)  # 清理

    # ---------- 6. Python 侧回归: internal 认证 + 推理 WS 路由仍在 ----------
    s, _ = req("GET", f"{PYTHON}/internal/tasks")
    check("internal 无 token 拒绝", s == 401, s)
    s, _ = req("GET", f"{PYTHON}/api/v1/projects")
    check("Python projects 路由已下线", s == 404, s)

    print(f"\nintegration_dual_service: PASS ({len(PASSED)} 项)")


if __name__ == "__main__":
    main()
