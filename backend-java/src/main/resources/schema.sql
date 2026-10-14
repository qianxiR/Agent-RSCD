-- rsiis_business 库表结构 (幂等, 与 scripts/migrate_project_to_java.py 等价)
-- users: 登录账户, role 为后续权限管理 (RBAC) 预留
CREATE TABLE IF NOT EXISTS users (
    id            TEXT PRIMARY KEY,
    username      TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role          TEXT NOT NULL DEFAULT 'user',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- project: 项目表 (自 agent_db.project 迁入, 结构对齐)
CREATE TABLE IF NOT EXISTS project (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL,
    name        TEXT NOT NULL,
    description TEXT DEFAULT '',
    created_at  TIMESTAMPTZ DEFAULT now(),
    updated_at  TIMESTAMPTZ DEFAULT now(),
    folder_path TEXT
);

CREATE INDEX IF NOT EXISTS idx_project_user ON project(user_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_project_folder_path
    ON project(folder_path) WHERE folder_path IS NOT NULL;
