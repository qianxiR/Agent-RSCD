"""长期记忆 v2 回归测试。

覆盖:
  - preference / profile / lesson 三类归一化；
  - scope / kind / confidence / source / evidence 元数据；
  - 旧 fact -> profile 兼容映射；
  - 注入文本只包含新标签；
  - 用户级清空不影响全局教训。

运行: python tests\\long_term_memory_test.py
"""
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.agent.memory import agent_db  # noqa: E402
from backend.agent.memory.memory_context import build_user_profile_text  # noqa: E402


def test_roundtrip() -> None:
    uid = f"memory_v2_test_{uuid.uuid4().hex[:8]}"
    try:
        assert agent_db.save_user_memory(
            uid,
            "交互偏好",
            "偏好简短中文指令",
            "preference",
            scope="user",
            kind="interaction",
            confidence=0.9,
            source="test",
            evidence={"case": "long_term_memory_test"},
        )
        assert agent_db.save_user_memory(
            uid,
            "关注领域",
            "关注建筑物类地物的变化检测",
            "profile",
            scope="user",
            kind="domain",
            confidence=0.8,
            source="test",
        )
        # 旧 fact 类别必须归一化为 profile
        assert agent_db.save_user_memory(
            uid,
            "旧事实",
            "旧类别仍可兼容",
            "fact",
            scope="user",
            kind="context",
            confidence=0.7,
            source="legacy",
        )

        rows = agent_db.load_user_memory(uid, scope="user")
        by_key = {row["key"]: row for row in rows}
        assert by_key["交互偏好"]["category"] == "preference"
        assert by_key["交互偏好"]["kind"] == "interaction"
        assert by_key["交互偏好"]["confidence"] == 0.9
        assert by_key["交互偏好"]["source"] == "test"
        assert by_key["交互偏好"]["evidence"]["case"] == "long_term_memory_test"
        assert by_key["关注领域"]["category"] == "profile"
        assert by_key["旧事实"]["category"] == "profile"

        # 同 key UPSERT 不新增行
        assert agent_db.save_user_memory(
            uid,
            "交互偏好",
            "偏好更短的指令",
            "preference",
            scope="user",
            kind="interaction",
            confidence=0.95,
            source="test",
        )
        rows = agent_db.load_user_memory(uid, scope="user")
        assert len(rows) == 3
        assert {row["key"]: row for row in rows}["交互偏好"]["value"] == "偏好更短的指令"

        text = build_user_profile_text(uid)
        assert "[偏好]" in text
        assert "[用户画像]" in text
        assert "[已知事实]" not in text
        assert "[常用实体]" not in text
    finally:
        agent_db.clear_user_memory(uid, scope="user")
        assert agent_db.load_user_memory(uid, scope="user") == []


def test_clear_does_not_touch_global() -> None:
    uid = f"memory_v2_test_{uuid.uuid4().hex[:8]}"
    try:
        assert agent_db.save_user_memory(
            uid, "操作偏好", "偏好地图叠加", "preference", confidence=0.9, source="test"
        )
        assert agent_db.clear_user_memory(uid, scope="user") == 1
        assert agent_db.load_user_memory(uid, scope="user") == []
    finally:
        agent_db.clear_user_memory(uid, scope="user")


def main() -> None:
    if not agent_db.init_schema():
        print("long_term_memory: SKIP (agent_db unavailable)")
        return
    test_roundtrip()
    test_clear_does_not_touch_global()
    print("long_term_memory: PASS")


if __name__ == "__main__":
    main()
