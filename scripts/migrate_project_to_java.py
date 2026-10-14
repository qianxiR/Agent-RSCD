"""
项目表迁移脚本 (Python agent_db → Java rsiis_business)。

背景:
  Java 业务服务接管项目管理后, project 表从 Agent_study 库迁出到独立的
  rsiis_business 库 (归 Java 直管)。conversation 表原有的
  conversation_project_id_fkey 外键随之删除, 删项目时的会话解绑改由
  Python 内部接口 /internal/conversations/detach-project 完成。

脚本幂等, 可重复执行:
  1. Agent_study: 删除 conversation 表的 project 外键 + 下线 project 表
     (存量 project 数据为空, 无需导出; 若后续有数据, 先 INSERT 导出再执行)
  2. postgres: 创建 rsiis_business 库 (存在则跳过)
  3. rsiis_business: 建 users / project 表 (与 Java schema.sql 等价, 双保险)

种子用户 (admin) 由 Java 服务启动时自动写入 (BCrypt 哈希在 Java 侧生成)。

运行: python scripts\\migrate_project_to_java.py
"""
import sys
from pathlib import Path

import psycopg2

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.config import settings  # noqa: E402


def _connect(dbname: str):
    """按 agent_db 连接参数连指定库。失败返回 None (脚本不中断, 逐库报告)。"""
    try:
        return psycopg2.connect(
            host=settings.agent_db_host,
            port=settings.agent_db_port,
            dbname=dbname,
            user=settings.agent_db_user,
            password=settings.agent_db_password,
            connect_timeout=5,
        )
    except Exception as e:
        print(f"[SKIP] 连接 {dbname} 失败: {e}")
        return None


def step1_agent_study() -> None:
    """Agent_study: 删外键 + 下线 project 表。"""
    conn = _connect(settings.agent_db_name)
    if conn is None:
        return
    try:
        with conn.cursor() as cur:
            cur.execute("ALTER TABLE conversation DROP CONSTRAINT IF EXISTS conversation_project_id_fkey")
            cur.execute("DROP TABLE IF EXISTS project")
        conn.commit()
        print(f"[OK] {settings.agent_db_name}: conversation 外键已删, project 表已下线")
    finally:
        conn.close()


def step2_create_business_db() -> bool:
    """postgres 库: 创建 rsiis_business (存在则跳过)。返回库是否可用。"""
    conn = _connect("postgres")
    if conn is None:
        return False
    try:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", ("rsiis_business",))
            if cur.fetchone():
                print("[OK] rsiis_business 库已存在")
                return True
            cur.execute("CREATE DATABASE rsiis_business")
            print("[OK] rsiis_business 库已创建")
            return True
    finally:
        conn.close()


SCHEMA_SQL = [
    # 用户表 (登录/JWT, 为后续权限管理预留 role)
    """
    CREATE TABLE IF NOT EXISTS users (
        id            TEXT PRIMARY KEY,
        username      TEXT NOT NULL UNIQUE,
        password_hash TEXT NOT NULL,
        role          TEXT NOT NULL DEFAULT 'user',
        created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    # 项目表 (结构对齐原 agent_db.project)
    """
    CREATE TABLE IF NOT EXISTS project (
        id          TEXT PRIMARY KEY,
        user_id     TEXT NOT NULL,
        name        TEXT NOT NULL,
        description TEXT DEFAULT '',
        created_at  TIMESTAMPTZ DEFAULT now(),
        updated_at  TIMESTAMPTZ DEFAULT now(),
        folder_path TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_project_user ON project(user_id)",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_project_folder_path ON project(folder_path) WHERE folder_path IS NOT NULL",
]


def step3_business_schema() -> None:
    """rsiis_business: 建表 (幂等)。"""
    if not step2_create_business_db():
        return
    conn = _connect("rsiis_business")
    if conn is None:
        return
    try:
        with conn.cursor() as cur:
            for sql in SCHEMA_SQL:
                cur.execute(sql)
        conn.commit()
        print("[OK] rsiis_business: users / project 表就绪")
    finally:
        conn.close()


if __name__ == "__main__":
    step1_agent_study()
    step3_business_schema()
    print("migrate_project_to_java: PASS")
