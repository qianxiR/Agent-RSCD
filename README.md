# Agent-RSCD

> 基于 WebSocket 的**遥感影像解译分析**智能体（Agent）系统。
> LLM（Qwen3.7 via DashScope，多模态视觉）驱动的 ReAct 推理、显式计划、证据验证、结构化反思记忆、技能编排与前端双向指令执行。

---

## 电梯演讲

一句话定位：**「以遥感影像解译分析师身份，用自然语言对遥感影像进行视觉理解、语义分割、变化检测与定量分析；Agent 自动推理、调用工具、在卡片/表格/影像面板上渲染结果，产物带坐标系、可追溯、可验证」**。

- 🧠 **Agent 层**：ReAct 推理引擎、显式 plan state、WebSocket 连接管理、工作记忆/会话摘要/长期记忆（含 accepted lesson）与 verification/repair 闭环
- 🤖 **模型层**：统一工具注册表、LLM 客户端、v1.0 技能契约与选择器、SamSeg 遥感推理封装、视觉理解与连通域分析
- 💾 **数据层**：GeoServer 空间服务、PostgreSQL 业务库、知识库骨架、掩膜分析子包、矢量化子包
- 🖥️ **表现层**：聊天 UI + **OpenLayers 地图容器**（影像/掩膜/矢量图层叠加 + WMS + GetFeatureInfo 属性弹窗 + 图例浮层）+ 数据面板 + 富表格（排序/复制）

> **依赖方向**（自上而下，不反向）：agent → model → data；agent 内部自洽（memory ↔ agent_db ↔ memory_tools）。

---

## 功能模块导航

本项目按 **10 个功能模块** 组织，详见 [docs/architecture.md](./docs/architecture.md)（总纲 + 索引）。下表为快速索引：

| # | 模块 | 一句话职责 | 主文档 |
|---|------|-----------|--------|
| 1 | [**会话与项目管理**](./docs/agent/core/architecture-agent.md#1-会话与项目管理) | 项目/会话/消息的 REST CRUD + 状态机 | `architecture-agent.md` |
| 2 | [**Agent ReAct 推理引擎**](./docs/agent/core/architecture-agent.md#2-agent-react-推理引擎) | 流式 thinking、工具调度循环、停止回滚 | `architecture-agent.md` |
| 3 | [**工具系统**](./docs/model/architecture-tools.md#3-工具系统) | `@register_tool` 注册、统一 Schema 与暴露边界 | `architecture-tools.md` |
| 4 | [**记忆与计划系统**](./docs/agent/core/architecture-agent.md#4-三层记忆系统) | 工作记忆、会话摘要、长期记忆、plan state 与 lesson 准入 | `architecture-agent.md` |
| 5 | [**System Prompt 与上下文缓存**](./docs/agent/core/architecture-agent.md#5-system-prompt-与上下文缓存) | 洋葱式三层结构（成功条件/ReAct/能力边界）+ 显式缓存块 | `architecture-agent.md` |
| 6 | [**WebSocket 双向通信**](./docs/agent/core/architecture-agent.md#6-websocket-双向通信) | `request_id` 配对、阻塞等待、多对话并行 | `architecture-agent.md` |
| 7 | [**前端指令执行层**](./docs/platform/architecture-platform.md#7-前端指令执行层) | OpenLayers 地图容器 + `event_type` 路由 → 渲染函数 → 回执回传 | `architecture-platform.md` |
| 8 | [**GeoServer 与数据集成**](./docs/platform/architecture-platform.md#8-geoserver-与数据集成) | WMS/REST/WFS 客户端 + 业务库 CRUD + 知识库骨架 | `architecture-platform.md` |
| 9 | [**技能编排（长任务）**](./docs/platform/architecture-platform.md#9-技能编排长任务) | 契约校验、确定性单技能选择、计划检查点与失败回退 | `architecture-platform.md` |
| 10 | [**SamSeg 遥感分析**](./docs/model/architecture-samseg.md#10-samseg-遥感分析) | 分割/变化检测/矢量化（runner·geoio·visualize 三层） | `architecture-samseg.md` |

### Agent 执行边界

- 主控 Agent 是唯一决策中心和唯一用户可见回复者。
- 保留确定性 `verification_agent` 与分割链路内同步调用的 `report_agent`。
- 不再拆分新的专业 worker，也不把同步报告生成改造成派发、查询式任务卡。
- 工具任务由显式 plan state 约束；只有必需步骤通过 verification 后才能完成。
- 只有成功修复且 verification 为 `passed` 的 lesson 才能进入长期记忆。
- 技能缺少必填输入、契约不合格或工具越权时不会执行。


---

## 项目目录

```
Agent-RSCD/
├── backend/                     # 🔧 后端总包 (统一入口 + 三层子包)
│   ├── main.py                  # FastAPI 入口 (HTTP + WS 端点)
│   ├── config.py                # 全局配置 (LLM/DB/GeoServer/记忆参数)
│   ├── agent/                   # 🧠 Agent 层
│   │   ├── chat_service.py      #   ReAct 推理循环 (流式 thinking + 工具调度)
│   │   ├── ws_manager.py        #   WebSocket 连接/任务管理 (多对话并行)
│   │   ├── memory/              #   工作记忆、plan state、lesson_policy 与 agent_db
│   │   ├── prompt/              #   System Prompt (动态工具目录 + CoT)
│   │   ├── runtime/             #   Observation、重试防护、技能步骤与多对话隔离
│   │   ├── team/                #   verification_agent、repair_policy 与同步 report_agent
│   │   └── tools/               #   记忆工具 (view/clear memory)
│   ├── model/                   # 🤖 模型层
│   │   ├── llm_client.py        #   LLM 实例构造 (ChatOpenAI + DashScope)
│   │   ├── tools/               #   工具箱、注册表与 LLM 暴露边界
│   │   │   └── samseg_tools.py  #     segment_image / detect_change / understand_image (遥感解译工具)
│   │   ├── skills/              #   三个 v1.0 技能、契约加载器与确定性选择器
│   │   └── SamSeg/              #   🛰️ SamSeg 遥感推理封装 (SegEarth-OV3/SAM3)
│   │       ├── runner.py        #     推理层: 模型缓存 + 分割/变化检测 + 连通域统计 + 类别图注
│   │       └── SamSeg/          #     子项目源码 (含 sam3 权重 3.3GB)
│   └── data/                    # 💾 数据层
│       ├── business_db.py       #   业务库通用 CRUD (表名作为参数, 不绑定具体业务表)
│       ├── geoserver_client.py  #   GeoServer REST + WMS 客户端
│       └── knowledge/           #   知识库 (骨架占位)
├── frontend/                   # 🖥️ 旧版前端
├── frontend-vue/               # 🖥️ 当前 Vue 3 + Vite + Element Plus + OpenLayers 前端
│   ├── src/                     #   页面、组件、状态与 WebSocket 逻辑
│   └── public/                  #   静态资源
├── agent.md                     # 后端 Agent 架构说明 (按当前代码整理)
├── docs/
│   ├── architecture.md          #   架构总纲 (总述+架构图+索引到子文档)
│   ├── PROGRESS.md              #   项目总体进度文档
│   ├── agent/                   #   Agent 建设文档
│   │   ├── core/                #     architecture-agent / design-overview / impl-agent
│   │   ├── evaluation/          #     评估集 / 基线报告 / 指标与命令
│   │   ├── method/              #     harness 方法论
│   │   └── roadmap.md           #     建设路线
│   ├── model/                   #   architecture-tools / architecture-samseg / impl-model
│   ├── platform/                #   architecture-platform / communication / playwright-usage
│   ├── data/                    #   impl-data
│   └── archive/                 #   归档的历史设计文档
└── scripts/                     # 记忆库维护 / 迁移 / 清理脚本
```

---

## 运行方式

### 1. 环境准备

```powershell
pip install -r requirements-backend.txt
npm --prefix frontend-vue install
```

SamSeg 遥感分析为可选能力，推荐使用包含 torch、CUDA、pycocotools、scipy 和 scikit-image 的 `sam3` conda 环境；缺失时其他能力仍可运行。

### 2. 配置

在 `backend\config.py` 中确认（或通过环境变量覆盖）：

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `DASHSCOPE_API_KEY` | — | **必填**，DashScope API Key（从环境变量读取） |
| `dashscope_model` | `qwen3.7-plus` | 主对话/Agent 推理模型（多模态视觉模型） |
| `vision_model` | `qwen3.7-plus` | 视觉理解工具 `understand_image` 专用模型 |
| `summary_model` | `deepseek-v4-flash` | 短期记忆摘要模型（走百炼免费额度） |
| `long_term_memory_model` | `deepseek-v4-flash` | 长期记忆画像提取模型 |
| `agent_service_port` | 8020 | 后端端口 |
| `agent_db_*` | — | 记忆库 PostgreSQL 连接（不可用时自动降级内存模式） |
| `summarize_threshold` | 8000 | 短期记忆摘要触发阈值（token） |
| `context_max_tokens` | 16000 | trim_messages 上限（token） |
| `enable_context_cache` | true | 显式缓存开关（命中后按 10% 计费） |
| `samseg_enabled` | true | SamSeg 遥感分析开关（**模型按需加载**：启动不预加载，首次遥感请求时才加载 3.3GB 权重） |
| `samseg_device` | cuda | 推理设备（cuda/cpu，ViT-L 在 cpu 上极慢） |

> **备选模型清单**：`config.py` 的 `available_models` 字段记录了百炼免费额度模型（含剩余额度、到期日、适用场景），切换模型时改对应字段即可。注意各模型"免费额度用完即停"未开启，超额自动按量付费。

### 3. 启动

```powershell
python -m backend.main
```

另开一个 PowerShell 终端启动 Vue 前端：

```powershell
npm --prefix frontend-vue run dev
```

浏览器访问 `http://127.0.0.1:5173`，后端默认监听 `http://127.0.0.1:8020`。

---

## 数据与状态存储

### 记忆库 `Agent_study`（Agent 自身状态）

| 表 | 用途 | 记忆层级 |
|----|------|---------|
| `project` | 项目组织（user_id + name + description） | 项目管理 |
| `conversation` | 会话元数据（含 project_id 外键 + status 状态机） | 会话管理 |
| `message` | 完整消息（含 tool_calls JSONB） | **工作记忆** |
| `conversation_summary` | 旧消息的 LLM 摘要 | **短期记忆** |
| `user_memory` | 跨会话用户偏好、实体与 accepted lesson | **长期记忆** |
| `ai_task` | 工具任务、worker 状态与显式 plan state 快照 | **执行状态** |

### 业务库（外部数据）

| 表/存储 | 库 | 用途 |
|---------|-----|------|
| 业务表（由实际部署决定） | 业务库 | 业务数据（具体表结构由运行环境决定，`business_db.py` 是通用 CRUD，表名作为参数传入，不硬编码） |
| GeoServer 图层 | — | 空间数据发布（WMS） |

> **关键设计**：`message.tool_calls` 用 JSONB 保存工具调用结构；显式计划复用 `ai_task.input/output` 保存初始状态和最新快照，不建立第二套任务表。

---

## API 端点速览

### HTTP REST

| 方法 | 路径 | 功能 |
|------|------|------|
| `GET` | `/api/v1/projects` | 列出用户的所有项目 |
| `POST` | `/api/v1/projects` | 创建新项目 |
| `PATCH` | `/api/v1/projects/{id}` | 编辑项目 |
| `DELETE` | `/api/v1/projects/{id}` | 删除项目 |
| `GET` | `/api/v1/conversations` | 列出会话（支持 project_id 过滤） |
| `GET` | `/api/v1/conversations/{id}/messages` | 加载会话历史消息 |
| `PATCH` | `/api/v1/conversations/{id}` | 重命名 / 移动会话 |
| `DELETE` | `/api/v1/conversations/{id}` | 删除会话（CASCADE） |
| `GET` | `/api/v1/geoserver/feature-info` | WMS GetFeatureInfo 代理 |
| `GET` | `/api/v1/vector/shp-to-geojson` | Shapefile → GeoJSON（geopandas 读 shp，供前端矢量渲染，阶段22新增） |
| `GET` | `/api/v1/download/{filepath}` | 下载已生成的栅格/分割结果文件（读 `downloads/`） |
| `GET` | `/api/v1/upload/{filepath}` | 读取 `uploads/` 下的原图（SamSeg 输入影像展示用） |
| `POST` | `/api/v1/samseg/upload` | 上传 1-2 张图片（multipart），返回绝对路径供 agent 工具使用 |

### WebSocket

| 端点 | 功能 |
|------|------|
| `ws://host/api/v1/agent/ws/chat` | 全双工 Agent 对话（支持多对话并行） |

---

## 遥感解译分析能力

> 以**遥感影像解译分析师**身份提供三类核心能力，System Prompt 按任务类型自动分流到对应工具。

### 三类遥感任务 × 三个工具

| 能力 | 触发方式 | 工具 | 前端展示 |
|------|---------|------|---------|
| **视觉理解**（影像解译） | "这张图里有什么"/"描述一下"/"分析地物" | `understand_image(image_path, question?)` | 视觉解译文本（地物类型、空间分布、形态纹理） |
| **语义分割** | "分割这张图"/"提取建筑和道路" + 上传 1 张图 | `segment_image(image_path, classes?)` | 原图 + 彩色分割 PNG + 类别色块图注 + 连通域统计 |
| **变化检测** | "对比这两张图的变化" + 上传 2 张图 | `detect_change(t1_path, t2_path, classes?)` | T1/T2 原图 + 变化 PNG + 图注 + 变化区域统计 |

### 视觉理解工具 `understand_image`（v2.2 新增）

- **真正"看图"**：内部调用 qwen3.7-plus 多模态视觉模型，把图片以 base64 形式喂给模型，返回专业解译文本
- **适用场景**：用户问"影像里有什么内容"等**理解类**问题（与分割/变化检测的**指令类**严格区分）
- **实现**：读图 → base64 data URI → 创建视觉 LLM（`settings.vision_model`）→ 发多模态消息 → 返回解译结论
- 单图 ≤20MB，调用 qwen-vl 视觉模型按分辨率计费

### SamSeg 分割/变化检测（基于 SegEarth-OV3 / SAM 3）

- **默认 7 类**：background / building / road / water / bareland / vegetation / farmland（支持中文类别名映射）
- **优雅降级**：torch/权重缺失时 `samseg_available()` 返回 False，工具返回明确错误，不影响其他已注册工具
- **按需加载**：模型权重（3.3GB）首次遥感请求时才加载，启动不预加载（不做遥感时零开销），加载后全局缓存复用
- **数据流**：前端点「🖼️ 上传分割」→ POST `/api/v1/samseg/upload` → 立即显示原图 → WS 发指令 → LLM 调 `segment_image` → runner 推理（首次加载模型）→ 上色存 PNG + 连通域统计 + 图注 → `frontend_action(render_image)` → 前端追加结果图 → LLM 据统计做定量描述
- 详细实现见 [docs/PROGRESS.md](./docs/PROGRESS.md) 阶段 2-6

---

## 相关文档

- 📄 [**docs/architecture.md**](./docs/architecture.md) — 架构总纲（总述 + 总架构图 + 索引到子文档：agent/core、model、platform）
- 📄 [**docs/platform/communication.md**](./docs/platform/communication.md) — 前后端通信机制（WS 协议 + 前端执行层 + `request_id` 闭环）
- 📄 [**docs/PROGRESS.md**](./docs/PROGRESS.md) — 项目总体进度文档
- 📄 [**agent.md**](./agent.md) — 后端 Agent 架构说明（上下文、记忆、工具、技能设计，按当前代码整理）
- 📄 [**docs/agent/roadmap.md**](./docs/agent/roadmap.md) — Agent 建设路线；评估方法与基线报告见 `docs/agent/evaluation/`

---

## 版本

| 版本 | 日期 | 主要内容 |
|---|---|---|
| v2.8 | 2026-08-16 | docs 按模块重组（agent/core·evaluation·method、model、platform、data、archive），README 链接与目录树同步更新 |
| v2.7 | 2026-07-18 | 阶段 5-8 收口：同步自然资源监测报告、显式 plan state、证据准入 lesson、技能契约与选择器 |
| v2.6 | 2026-06-24 | OpenLayers 地图容器、矢量原生渲染与端到端测试 |
| v2.5 | 2026-06 | verification、反伪装成功防御、连通域分析与空间产物增强 |
| v2.4 | 2026-06 | 数据存储、变化判读、报表导出、地图交互与自纠学习 |
| v2.2 | 2026-06 | 遥感分析师定位、视觉理解、SamSeg 按需加载与任务分流 |
| v2.1 | 2026-06 | agent/model/data 三层重构、多对话、上下文缓存与 SamSeg 接入 |
