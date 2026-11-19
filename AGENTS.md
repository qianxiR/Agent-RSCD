# Agent-RSCD 工作区说明

基于 WebSocket 的遥感影像解译智能体系统（品牌名：国土智察）。双服务后端（Java 管业务 + Python 管 AI）+ Vue 3 前端，LLM（GLM-5.3-Flash，DashScope 回退）驱动 ReAct 推理、工具编排、三层记忆与技能编排。

## 目录结构

- `backend/` — Python AI 服务。三层子包 `agent` / `model` / `data`，外加 `api`（REST/WS 路由，含 `/internal/*` 内部接口）、`integrations`（外部 Tools / MCP 适配）、`utils`；入口 `backend/main.py`，配置集中在 `backend/config.py`。
  - `agent/`：编排层。`planner/`（PlanGraph/校验/模板）、`executor/`（ReAct + DAG）、`harness/`（标准运行生命周期）、`tools/`（全部工具实现与注册表）、`skills/`（loader/selector + 标准业务目录）、`mcp/`（MCP 客户端与动态工具）、`memory/`（三层 store + 编排）、`prompt/`（构建器/工具目录）+ `prompt_store/`（唯一静态模板 `static_template2.md`）、`context/`（上下文构建）、`verification/`、`team/`、`runtime/`。
  - `model/`：解译模型与 LLM 适配层。`base.py`/`registry.py` 统一协议，`segmentation/`、`change_detection/`、`understanding/` 放模型适配器，`sample/` 放样本标注契约、任务存储与 COCO/SHP 导出（当前不含模型推理）；具体权重与推理实现仍在 `sam3native/`、`SamSeg/`。
  - `data/`：数据资产与 IO。`base.py`/`catalog.py` 统一资产契约，`imagery` / `vector` / `image` / `text` / `other` 五类域，`connectors/` 放 PostGIS/GeoServer/存储连接器。
- `backend/integrations/vendor/`：从 `E:\1代码\系统\Tools` 复制的数据处理/后处理代码子集（raster_io、spectral、spatial、mask_postprocess），**不含权重和数据**；运行时不再依赖外部 Tools 路径。知识库 Markdown 与 `external_knowledge_query` 检索入口已移除，暂不提供知识库检索（见 `vendor/README.md`）。
- `backend-java/` — Java 业务服务（Spring Boot 3 + MyBatis-Plus + Spring Security/JWT，端口 8081）：项目管理（直管 `rsiis_business` 库）、登录认证、工作区浏览、会话与任务代理（转发 Python `/internal/*`）。构建运行见 `backend-java/README.md`。
- `frontend-vue/` — 当前前端（Vue 3 + Vite + Element Plus + Pinia + OpenLayers）。
- `frontend/` — 旧版前端，勿改。
- `docs/` — `architecture.md` 总纲及子文档；`docs/agent/`（core / evaluation / method / roadmap）；`docs/rules/ui-style.md` 用户端 UI 规则；`docs/PROGRESS.md` 阶段进度与历史决策。
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

- 依赖方向：`agent → model`、`agent → data`、`model → data`；`data` 不反向依赖 `agent/model`。
- 双服务依赖单向：Java → Python（内部 REST）；Python 不反向调用 Java。
- 主控 Agent 是唯一决策中心和唯一用户可见回复者；仅保留确定性 `verification_agent` 与分割链路内同步调用的 `report_agent`，不新增专业 worker。
- 工具实现与注册表位于 `agent/tools/`，通过 `@register_tool` 注册；LLM 暴露边界由工具目录控制。
- 长期记忆（`user_memory`）只接受 `preference` / `profile` / `lesson` 三类：前两者来自对话提取，lesson 必须 verification 为 `passed` 且经 `lesson_policy` 准入；技能缺必填输入、契约不合格或工具越权时不执行。
- 三部分各司其职：**地物提取**（`segment_image`）只走 **SamSeg 本体**（推理在 `backend/model/SamSeg/`：`official.py` 单图/512 滑窗 + 双头融合，`engine.py` 加载 site-packages 官方 `sam3`；权重 `backend/model/SamSeg/SamSeg/sam3/sam3.pt`），无口径开关；**样本制作**当前为人工标注链路（`backend/model/sample/` 契约/存储/导出 + `backend/api/samples.py` + 前端 `SamplePanel`/`SamplePhaseDialog`/`useSampleEditor`），计划接入的 **原生 SAM3**（`backend/model/sam3native/`，接地头批量候选 + 交互头点/框，权重 `backend/model/sam3native/weights/sam3/sam3.pt`，**本地副本，`.gitignore` 排除，不进入 Git 历史**）尚未接入；**变化检测**走 `change_detection/`（change_qwen ONNX 专用权重，唯一口径）。三者互不兜底，原生 SAM3 启动预热已取消；候选接入后在样本制作首次调用时加载。
- Java 侧认证三档开关（`SECURITY_MODE`: off/observe/enforce），默认 off 保证迁移期与 E2E 兼容。

## 文档阅读顺序

改动敏感区域前先读：`docs/architecture.md`（总纲索引）→ 对应子文档（`docs/agent/core/architecture-agent.md`、`docs/model/architecture-tools.md`、`docs/model/architecture-samseg.md`、`docs/platform/architecture-platform.md`）→ `docs/platform/communication.md`（WS 协议与 `request_id` 闭环）。查历史决策看 `docs/PROGRESS.md`。根目录 `agent.md` 是旧版架构说明（其中 `backend/model/tools/` 等路径已失效），以 `docs/` 与现行代码为准。

## 约定

- 注释、文档、commit message 与既有代码保持中文。
- 配置改动集中在 `backend/config.py`，支持环境变量覆盖，不散落硬编码。
- `backend/data/business_db.py` 是通用 CRUD，表名作为参数传入，不得硬编码具体业务表结构。
