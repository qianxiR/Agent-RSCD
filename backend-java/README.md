# rsiis-business (Java 业务服务)

国土智察双服务架构中的 Java 侧：业务功能 + 未来权限管理承载地。
AI 能力（Agent 推理 / SamSeg / 沙盒 / WebSocket 聊天）在 Python 服务（`backend/`）。

## 职责

| 模块 | 路由 | 说明 |
|---|---|---|
| auth | `/api/v1/auth/**` | 登录、MFA 绑定/校验、refresh 旋转、logout |
| me | `/api/v1/me` | 当前用户 + 角色 + 权限（前端守卫/菜单用） |
| admin | `/api/v1/admin/**` | 用户、角色/权限、审计、运营总览（方法级权限强制） |
| project | `/api/v1/projects` | 项目 CRUD，直管 PostgreSQL `rsiis_business` 库 |
| workspace | `/api/v1/workspace/list` | 工作区文件夹浏览（黑名单 + 穿越防护） |
| conversation | `/api/v1/conversations` | 代理 → Python `/internal/conversations`（会话数据归 Python） |
| task | `/api/v1/tasks` | 只读代理 → Python `/internal/tasks` |

## 构建与运行

```powershell
cd backend-java
mvn package -DskipTests
java -jar target\rsiis-business.jar --server.port=8081
```

依赖：PostgreSQL（库 `rsiis_business`，首次执行 `python scripts\migrate_project_to_java.py` 完成建库建表）；Python AI 服务运行在 8020（代理目标）。

## 关键配置（application.yml，均支持环境变量覆盖）

| 变量 | 默认 | 说明 |
|---|---|---|
| `RSIIS_DB_*` | localhost/rsiis_business | 业务库连接 |
| `PYTHON_BASE_URL` | http://127.0.0.1:8020 | Python AI 服务地址 |
| `INTERNAL_API_TOKEN` | rsiis-dev-internal-token | 服务间令牌，需与 Python 侧一致 |
| `SECURITY_MODE` | off | off / observe / enforce（enforce 强制 JWT） |
| `JWT_SECRET` | dev 默认 | HS256 密钥（生产必须改） |
| `ACCESS_TTL_MINUTES` | 30 | Access Token 有效期 |
| `REFRESH_TTL_DAYS` | 14 | Refresh Token 有效期 |
| `MFA_REQUIRED` | true | 管理端强制 TOTP（生产必须 true） |
| `MFA_ISSUER` | 国土智察 | 认证器显示名 |
| `MFA_ENCRYPTION_KEY` | 空 | Base64 32 字节；空时由 JWT_SECRET 派生 |

## 认证模式

- `off`：HTTP 层全放行（迁移期默认）；**管理端 `/api/v1/admin/**` 仍要求方法级权限**
- `observe`：解析 token 填充上下文，不拦截
- `enforce`：除 `/api/v1/auth/**` 外均需有效 JWT

## MFA（管理端强制）

1. `POST /api/v1/auth/login`：admin 首次登录返回 `mfa_setup_required` + `setup_token`；
2. `POST /api/v1/auth/mfa/setup`：返回 secret / otpauth URI / 恢复码（只显示一次）；
3. `POST /api/v1/auth/mfa/setup/confirm`：提交 6 位 TOTP，启用 MFA 并返回 tokens；
4. 后续登录：`mfa_required` + `mfa_token` → `POST /api/v1/auth/mfa/verify`；
5. Refresh：`POST /api/v1/auth/refresh`（旋转，旧 token 立即失效）。

种子管理员：`admin/admin123`，首次登录必须完成 MFA 绑定；生产请立即改密并配置 `MFA_ENCRYPTION_KEY`。

## 测试

```powershell
cd backend-java
mvn test
```
