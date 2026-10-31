# -*- coding: utf-8 -*-
"""
旧 agent 库 → 当前 Agent_study 记忆库迁移脚本

背景:
  项目早期使用独立库 `agent`（conversations/messages/tool_calls 表），
  当前代码已统一使用 `Agent_study`（conversation/message/... 表）。
  本脚本把历史会话/消息迁入当前表结构，保证前端“历史会话”可见。

映射:
  conversations.id                → conversation.id
  conversations.user_id           → conversation.user_id（统一为 study_user）
  conversations.title             → conversation.title
  conversations.started_at        → conversation.created_at
  conversations.updated_at        → conversation.updated_at
  conversations.is_active         → conversation.status（true=active / false=completed）
  messages.message_id             → message.node_id（UUID 稳定复用）
  messages.role user/assistant    → human/ai
  messages.content                → message.content
  messages.thinking               → message.thinking_content
  messages.created_at             → message.created_at
  相邻消息按 created_at 串成 parent_id 链，最后一条写入 conversation.active_leaf_node

不迁移:
  - tool_calls 表：旧表没有与 assistant 消息的 tool_calls 结构配对，
    单独导入会成为 orphan tool message，导致 LLM 请求报错；历史展示只需 human/ai 文本。
  - user_memories / conversation_summaries：旧表为空，且字段语义与当前不同。

用法:
  python scripts/migrate_legacy_agent_db.py            # 只迁移，打印统计
  python scripts/migrate_legacy_agent_db.py --dry-run  # 只统计，不写库
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import psycopg2  # noqa: E402

from backend.config import settings  # noqa: E402

DEFAULT_TARGET_USER = "study_user"


def _legacy_conn():
    """连接旧 `agent` 库（与当前 Agent_study 同实例，仅库名不同）。"""
    return psycopg2.connect(
        host=settings.agent_db_host,
        port=settings.agent_db_port,
        dbname="agent",
        user=settings.agent_db_user,
        password=settings.agent_db_password,
        connect_timeout=5,
    )


def _target_conn():
    """连接当前 Agent_study 库。"""
    return psycopg2.connect(
        host=settings.agent_db_host,
        port=settings.agent_db_port,
        dbname=settings.agent_db_name,
        user=settings.agent_db_user,
        password=settings.agent_db_password,
        connect_timeout=5,
    )


def migrate(dry_run: bool = False, target_user: str = DEFAULT_TARGET_USER) -> dict:
    """
    入参: dry_run / target_user。
    方法: 按 created_at 顺序迁移 22 个会话和 202 条消息，幂等（conversation 冲突跳过，
          message 按 node_id 查重）。
    出参: 统计 dict。
    """
    stats = {"conversations": 0, "messages": 0, "skipped_conversations": 0,
             "skipped_messages": 0, "errors": 0}

    src = _legacy_conn()
    src.set_client_encoding("UTF8")
    dst = _target_conn()
    dst.set_client_encoding("UTF8")
    try:
        with src.cursor() as scur:
            scur.execute("""
                SELECT conversation_id, user_id, title, started_at, updated_at, is_active
                FROM conversations ORDER BY started_at ASC
            """)
            conversations = scur.fetchall()

        with src.cursor() as scur:
            scur.execute("""
                SELECT message_id, conversation_id, role, content, thinking, created_at
                FROM messages ORDER BY conversation_id, created_at ASC, message_id ASC
            """)
            messages = scur.fetchall()

        by_conv = {}
        for m in messages:
            by_conv.setdefault(m[1], []).append(m)

        with dst.cursor() as dcur:
            for cid, old_uid, title, started_at, updated_at, is_active in conversations:
                status = "active" if is_active else "completed"
                if dry_run:
                    stats["conversations"] += 1
                else:
                    dcur.execute("""
                        INSERT INTO conversation
                            (id, user_id, project_id, title, status, created_at, updated_at)
                        VALUES (%s, %s, NULL, %s, %s, %s, %s)
                        ON CONFLICT (id) DO NOTHING
                    """, (cid, target_user, title, status, started_at, updated_at))
                    if dcur.rowcount == 0:
                        stats["skipped_conversations"] += 1
                    else:
                        stats["conversations"] += 1

                prev_node = None
                for mid, _cid, role, content, thinking, created_at in by_conv.get(cid, []):
                    new_role = {"user": "human", "assistant": "ai"}.get(role, role)
                    thinking_text = None if thinking in (None, "", "None") else thinking
                    if dry_run:
                        stats["messages"] += 1
                    else:
                        dcur.execute(
                            "SELECT 1 FROM message WHERE node_id = %s LIMIT 1", (mid,)
                        )
                        if dcur.fetchone():
                            stats["skipped_messages"] += 1
                            prev_node = mid
                            continue
                        dcur.execute("""
                            INSERT INTO message
                                (conversation_id, role, content, thinking_content,
                                 node_id, parent_id, is_active, created_at)
                            VALUES (%s, %s, %s, %s, %s, %s, TRUE, %s)
                        """, (cid, new_role, content, thinking_text,
                              mid, prev_node, created_at))
                        stats["messages"] += 1
                    prev_node = mid

                if prev_node and not dry_run:
                    dcur.execute(
                        "UPDATE conversation SET active_leaf_node = %s WHERE id = %s",
                        (prev_node, cid),
                    )

        if not dry_run:
            dst.commit()
        print(f"[migrate] {stats}")
        return stats
    except Exception:
        dst.rollback()
        raise
    finally:
        src.close()
        dst.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="旧 agent 库 → Agent_study 迁移")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--user", default=DEFAULT_TARGET_USER)
    args = parser.parse_args()
    migrate(dry_run=args.dry_run, target_user=args.user)
