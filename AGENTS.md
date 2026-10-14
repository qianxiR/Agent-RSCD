# Agent-RSCD 工作区说明

基于 WebSocket 的遥感影像解译智能体系统（品牌名：国土智察）。双服务后端（Java 管业务 + Python 管 AI）+ Vue 3 前端，LLM（Qwen via DashScope）驱动 ReAct 推理、工具编排、三层记忆与技能编排。

## 目录结构

- `backend/` — Python AI 服务。三层子包 `agent` / `model` / `data`，外加 `api`（REST/WS 路由，含 `/internal/*` 内部接口）、`utils`；入口 `backend/main.py`，配置集中在 `backend/config.py`。
- `backend-java/` — Java 业务服务（Spring Boot 3 + MyBatis-Plus + Spring Security/JWT，端口 8081）：项目管理（直管 `rsiis_business` 库）、登录认证、工作区浏览、会话与任务代理（转发 Python `/internal/*`）。构建运行见 `backend-java/README.md`。
- `frontend-vue/` — 当前前端（Vue 3 + Vite + Element Plus + Pinia + OpenLayers）。
- `frontend/` — 旧版前端，勿改。
- `docs/` — `architecture.md` 总纲及子文档；`docs/agent/`（core / evaluation / method / roadmap）；`docs/PROGRESS.md` 阶段进度（22 个阶段的演进记录）。
- `tests/` — Python 测试脚本 + `tests/e2e/`（Playwright）。
- `deploy/nginx.conf` — 生产统一入口分流配置（开发期由 Vite proxy 等价承担）。
- `scripts/` — 一次性/运维脚本（如 `migrate_project_to_java.py` 双服务库迁移）。
- `GeoCD-Agent/`、`agent-files/` — 参考资料（论文 PDF、数据集、SamSeg 源码副本）与运行时产物，非本项目源码，勿当作依赖修改。

## 与本项目无关的根目录文件

根目录的 `package.json`、`node_modules/`、`package-lock.json` 属于一个 Electron 桌面应用模板，与本项目无关；本项目的 npm 前端在 `frontend-vue/`。唯一例外是根目录 `playwright.config.ts`，它是本项目 E2E 测试的真实配置。

## 运行与验证

```powershell
python -m backend.main                                    # Python AI 服务，127.0.0.1:8020
java -jar backend-java\target\rsiis-business.jar          # Java 业务服务，8081（先 mvn package）
npm --prefix frontend-vue run dev                         # 前端，http://127.0.0.1:5173（proxy 自动分流）
```

- Python 环境优先使用 conda 环境 `sam3`（含 torch/CUDA/pycocotools，SamSeg 遥感推理必需；缺失时 SamSeg 优雅降级，其余功能可用）。
- `DASHSCOPE_API_KEY` 环境变量必填；记忆库 PostgreSQL 不可用时自动降级内存模式。
- 双服务分工：`projects`/`auth`/`workspace` 归 Java；会话、消息、记忆、任务数据归 Python（`agent_db`），Java 经 `/internal/*`（`X-Internal-Token` 认证，两端默认值一致）代理读写；推理 WebSocket（`/api/v1/agent/ws/*`）直达 Python。
- Python 测试不使用 pytest：各测试脚本自带 `sys.path` 引导，直接 `python tests\xxx_test.py` 运行，成功时打印 `xxx: PASS`。
- Playwright E2E（`tests/e2e/*.spec.ts`）：`npx playwright test`，使用 msedge channel，baseURL 为 Vite (5173)，webServer 自动拉起 Java + Python + Vite 三个服务（可复用已运行实例）。注意 `ui.spec.ts` 与 `map-container.spec.ts` 部分用例针对已废弃的 `frontend/` 旧前端编写（依赖其全局函数），属存量失效用例。

## 架构边界（改动前必读）

- 依赖方向严格自上而下：`agent → model → data`，不得反向导入。
- 双服务依赖单向：Java → Python（内部 REST）；Python 不反向调用 Java。
- 主控 Agent 是唯一决策中心和唯一用户可见回复者；仅保留确定性 `verification_agent` 与分割链路内同步调用的 `report_agent`，不新增专业 worker。
- 工具通过 `@register_tool` 注册进统一注册表，LLM 暴露边界由工具目录控制。
- 只有 verification 为 `passed` 的 lesson 才能进入长期记忆；技能缺必填输入、契约不合格或工具越权时不执行。
- SamSeg 模型权重（3.3GB）按需加载：启动不预加载，首次遥感请求加载后全局缓存。
- Java 侧认证三档开关（`SECURITY_MODE`: off/observe/enforce），默认 off 保证迁移期与 E2E 兼容。

## 文档阅读顺序

改动敏感区域前先读：`docs/architecture.md`（总纲索引）→ 对应子文档（agent / tools / samseg / platform，位于 `docs/agent/core/` 与 `docs/`）→ `docs/communication.md`（WS 协议与 `request_id` 闭环）。查历史决策看 `docs/PROGRESS.md`。

## 约定

- 注释、文档、commit message 与既有代码保持中文。
- 配置改动集中在 `backend/config.py`，支持环境变量覆盖，不散落硬编码。
- `backend/data/business_db.py` 是通用 CRUD，表名作为参数传入，不得硬编码具体业务表结构。
