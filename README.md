# Agent-Flow-Study

> 基于 WebSocket 的**遥感影像解译分析**智能体（Agent）系统。
> LLM（Qwen3.7 via DashScope，多模态视觉）驱动的 ReAct 推理 + 工具编排 + 三层记忆 + 反伪装成功防御 + 前端双向指令执行。

---

## 电梯演讲

一句话定位：**「以遥感影像解译分析师身份，用自然语言对遥感影像进行视觉理解、语义分割、变化检测与定量分析；Agent 自动推理、调用工具、在卡片/表格/影像面板上渲染结果，产物带坐标系、可追溯、可验证」**。

- 🧠 **Agent 层**：ReAct 推理引擎（洋葱式三层 prompt：成功条件→ReAct→能力边界）、WebSocket 连接管理、四层记忆（工作/短期/长期/经验教训）、反伪装成功三层防御（工具层 verification + prompt 铁律 + 传输层兜底）
- 🤖 **模型层**：工具箱（**39 个工具，10 大类**）+ LLM 客户端 + 技能编排方案 + SamSeg 遥感推理封装 + 视觉理解 + 连通域分析
- 💾 **数据层**：GeoServer 空间服务、PostgreSQL 业务库、知识库骨架、掩膜分析子包、矢量化子包
- 🖥️ **表现层**：聊天 UI + **OpenLayers 地图容器**（影像/掩膜/矢量图层叠加 + WMS + GetFeatureInfo 属性弹窗 + 图例浮层）+ 数据面板 + 富表格（排序/复制）

> **依赖方向**（自上而下，不反向）：agent → model → data；agent 内部自洽（memory ↔ agent_db ↔ memory_tools）。

---

## 功能模块导航

本项目按 **9 大功能模块** 组织，详见 [docs/architecture.md](./docs/architecture.md)（总纲 + 索引）。下表为快速索引：

| # | 模块 | 一句话职责 | 主文档 |
|---|------|-----------|--------|
| 1 | [**会话与项目管理**](./docs/architecture-agent.md#1-会话与项目管理) | 项目/会话/消息的 REST CRUD + 状态机 | `architecture-agent.md` |
| 2 | [**Agent ReAct 推理引擎**](./docs/architecture-agent.md#2-agent-react-推理引擎) | 流式 thinking、工具调度循环、停止回滚 | `architecture-agent.md` |
| 3 | [**工具系统**](./docs/architecture-tools.md#3-工具系统) | 47 个工具 + `@register_tool` 注册机制 + 10 大分类 | `architecture-tools.md` |
| 4 | [**三层记忆系统**](./docs/architecture-agent.md#4-三层记忆系统) | 工作/短期/长期/经验教训记忆的编排与持久化 | `architecture-agent.md` |
| 5 | [**System Prompt 与上下文缓存**](./docs/architecture-agent.md#5-system-prompt-与上下文缓存) | 洋葱式三层结构（成功条件/ReAct/能力边界）+ 显式缓存块 | `architecture-agent.md` |
| 6 | [**WebSocket 双向通信**](./docs/architecture-agent.md#6-websocket-双向通信) | `request_id` 配对、阻塞等待、多对话并行 | `architecture-agent.md` |
| 7 | [**前端指令执行层**](./docs/architecture-platform.md#7-前端指令执行层) | OpenLayers 地图容器 + `event_type` 路由 → 渲染函数 → 回执回传 | `architecture-platform.md` |
| 8 | [**GeoServer 与数据集成**](./docs/architecture-platform.md#8-geoserver-与数据集成) | WMS/REST/WFS 客户端 + 业务库 CRUD + 知识库骨架 | `architecture-platform.md` |
| 9 | [**技能编排（长任务）**](./docs/architecture-platform.md#9-技能编排长任务) | 文件扫描检索 + 数据入库流水线方案 | `architecture-platform.md` |
| 10 | [**SamSeg 遥感分析**](./docs/architecture-samseg.md#10-samseg-遥感分析) | 分割/变化检测/矢量化（runner·geoio·visualize 三层） | `architecture-samseg.md` |


---

## 项目目录

```
agent-flow-study/
├── backend/                     # 🔧 后端总包 (统一入口 + 三层子包)
│   ├── main.py                  # FastAPI 入口 (HTTP + WS 端点)
│   ├── config.py                # 全局配置 (LLM/DB/GeoServer/记忆参数)
│   ├── agent/                   # 🧠 Agent 层
│   │   ├── chat_service.py      #   ReAct 推理循环 (流式 thinking + 工具调度)
│   │   ├── ws_manager.py        #   WebSocket 连接/任务管理 (多对话并行)
│   │   ├── memory/              #   记忆子包 (编排 + agent_db 记忆数据库)
│   │   ├── prompt/              #   System Prompt (动态工具目录 + CoT)
│   │   ├── runtime/             #   ContextVar 多对话隔离
│   │   └── tools/               #   记忆工具 (view/clear memory)
│   ├── model/                   # 🤖 模型层
│   │   ├── llm_client.py        #   LLM 实例构造 (ChatOpenAI + DashScope)
│   │   ├── tools/               #   工具箱 (30 个工具 + 注册表)
│   │   │   └── samseg_tools.py  #     segment_image / detect_change / understand_image (遥感解译工具)
│   │   ├── skills/              #   技能编排方案 (Markdown, 当前为空)
│   │   └── SamSeg/              #   🛰️ SamSeg 遥感推理封装 (SegEarth-OV3/SAM3)
│   │       ├── runner.py        #     推理层: 模型缓存 + 分割/变化检测 + 连通域统计 + 类别图注
│   │       └── SamSeg/          #     子项目源码 (含 sam3 权重 3.3GB)
│   └── data/                    # 💾 数据层
│       ├── business_db.py       #   业务库通用 CRUD (表名作为参数, 不绑定具体业务表)
│       ├── geoserver_client.py  #   GeoServer REST + WMS 客户端
│       └── knowledge/           #   知识库 (骨架占位)
├── frontend/                   # 🖥️ 表现层 (按四个功能区拆分)
│   ├── index.html               #   主页面 (聊天 + 地图 + 数据面板)
│   ├── styles.css               #   全局样式 (配色方案 + OpenLayers 地图容器定制)
│   ├── image.svg / image.png    #   品牌资源
│   ├── core/                    #   基础设施 (通信/状态/启动)
│   │   ├── ws-chat.js           #     WebSocket 传输层 (WsChatClient 类)
│   │   ├── state.js             #     全局状态管理 (convStates/activeConvId)
│   │   ├── ws-send.js           #     WS 初始化 + 消息发送
│   │   └── init.js              #     启动编排 (最后加载)
│   ├── project/                 #   项目管理 (左侧栏)
│   │   ├── sidebar.js           #     会话/分组/项目树 + 右键 CRUD
│   │   └── workspace.js         #     工作区文件浏览器 (文件夹树)
│   ├── map/                     #   地图/影像
│   │   ├── wms-render.js        #     OpenLayers 地图引擎 (影像/掩膜/矢量图层 + GetFeatureInfo + 富表格)
│   │   └── task-monitor.js      #     任务日志监控面板
│   └── chat/                    #   AI 聊天面板
│       ├── msg-ui.js            #     消息气泡渲染 + Modal + 布局
│       ├── thinking.js          #     Thinking 聚合 + frontend_action 派发
│       ├── conv-core.js         #     对话切换/回调/历史重建
│       └── samseg.js            #     SamSeg 上传 + 对话动作
├── docs/
│   ├── architecture.md          #   架构总纲 (总述+架构图+索引到子文档)
│   ├── architecture-agent.md    #   Agent 内核 (会话/ReAct/记忆/Prompt/WS)
│   ├── architecture-tools.md    #   工具系统 (47 工具 + 统一 Schema)
│   ├── architecture-samseg.md   #   SamSeg 遥感分析 (runner/geoio/visualize 三层)
│   ├── architecture-platform.md #   平台层 (前端/GeoServer/技能/视觉设计)
│   └── communication.md         #   前后端通信机制 (WS 协议 + 前端执行层)
└── scripts/
    └── migrate_agent_db.py      # 记忆库迁移脚本
```

---

## 运行方式

### 1. 环境准备

```powershell
# 基础依赖 (FastAPI 服务 + LLM + 数据库 + 工具箱)
pip install fastapi uvicorn httpx langchain-openai psycopg2-binary tiktoken pandas requests

# ★ SamSeg 遥感分析 (可选, 缺失时自动降级):
#   需要 conda 环境 (推荐 sam3), 含 torch+CUDA / pycocotools / scipy / scikit-image
#   详见 docs/PROGRESS.md 阶段 2
```

### 2. 配置

在 `backend/config.py` 中确认（或通过环境变量覆盖）：

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
cd agent-flow-study
python -m backend.main
# 或
uvicorn backend.main:app --reload --port 8020
```

浏览器访问 `http://localhost:8020/`。

---

## 三层数据库设计

### 记忆库 `Agent_study`（Agent 自身状态）

| 表 | 用途 | 记忆层级 |
|----|------|---------|
| `project` | 项目组织（user_id + name + description） | 项目管理 |
| `conversation` | 会话元数据（含 project_id 外键 + status 状态机） | 会话管理 |
| `message` | 完整消息（含 tool_calls JSONB） | **工作记忆** |
| `conversation_summary` | 旧消息的 LLM 摘要 | **短期记忆** |
| `user_memory` | 跨会话用户偏好/实体（UNIQUE(user_id,key)） | **长期记忆** |

### 业务库（外部数据）

| 表/存储 | 库 | 用途 |
|---------|-----|------|
| 业务表（由实际部署决定） | 业务库 | 业务数据（具体表结构由运行环境决定，`business_db.py` 是通用 CRUD，表名作为参数传入，不硬编码） |
| GeoServer 图层 | — | 空间数据发布（WMS） |

> **关键设计**：`message.tool_calls` 用 JSONB 完整保存工具调用结构，下次 `load_messages` 可零损耗还原 `AIMessage(tool_calls=[...])`，这是多轮工具编排的基础。

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
- **优雅降级**：torch/权重缺失时 `samseg_available()` 返回 False，工具返回明确错误，**不影响其他 27 个工具**
- **按需加载**：模型权重（3.3GB）首次遥感请求时才加载，启动不预加载（不做遥感时零开销），加载后全局缓存复用
- **数据流**：前端点「🖼️ 上传分割」→ POST `/api/v1/samseg/upload` → 立即显示原图 → WS 发指令 → LLM 调 `segment_image` → runner 推理（首次加载模型）→ 上色存 PNG + 连通域统计 + 图注 → `frontend_action(render_image)` → 前端追加结果图 → LLM 据统计做定量描述
- 详细实现见 [docs/PROGRESS.md](./docs/PROGRESS.md) 阶段 2-6

---

## 相关文档

- 📄 [**docs/architecture.md**](./docs/architecture.md) — 架构总纲（总述 + 总架构图 + 索引到 4 个子文档：agent/tools/samseg/platform）
- 📄 [**docs/communication.md**](./docs/communication.md) — 前后端通信机制（WS 协议 + 前端执行层 + `request_id` 闭环）
- 📄 [**docs/PROGRESS.md**](./docs/PROGRESS.md) — 项目进度文档（SamSeg 接入全过程、阶段 1-6 改动清单）

---

## 版本

- **v2.6**（2026-06-24）：**OpenLayers 地图容器 + 矢量渲染增强 + 端到端测试**（阶段 21-22）
  - ★ **撤销 v2.5"移除地图引擎"决策**：重新引入 **OpenLayers 9**（替代卡片墙），影像/掩膜/矢量作为图层叠加；原 flex 堆叠 + CSS transform 缩放废弃
  - **砍掉 overlay/edge 自动产物**：原图层（底图）+ 掩膜半透明叠加层（透明度 0.5）替代旧 overlay 混色图；`edge_utils.py` + `overlay_edge_on_image` 手动工具保留
  - **矢量原生渲染**：新增 `showVectorFile()` 支持 GeoJSON（OL Vector 图层）+ Shapefile（走新增端点 `/api/v1/vector/shp-to-geojson` 转换）
  - **系统提示词增强**：新增"结构化呈现（表格优先）"规则（≥3 项同类结果强制 Markdown 表格）
  - **无 CRS 影像虚拟坐标铺画布**：extent=[0,0,W,H] 统一进地图容器，不搞双模式
  - **端到端测试体系**：后端脚本 `tests/map_container_backend.py`（28 项验证）+ Playwright `tests/e2e/map-container.spec.ts`（4 项全过）
  - 联调修复 3 个 OL bug（controls API / imageExtent CRS / view.fit 投影）
- **v2.5**（2026-06）：**架构强化 + 可视化增强 + 反伪装成功防御**（阶段 14，注：v2.6 撤销了其中"移除地图模式"一项）
  - **前端地图模式移除**（★ v2.6 已撤销）：删除 Leaflet + map-core.js（645 行），仅保留卡片墙模式
  - **连通域分析工具**：新增 `analyze_connected_components`（10 大类第 39 个工具），支持坐标系保存
  - **三层反伪装防御**：`_verification.py`（5 校验器）+ prompt 洋葱式重构（成功条件→ReAct→能力边界）+ chat_service 传输层兜底
  - **0KB ZIP bug 修复**：Fiona 1.10+ 要求 `.shp` 扩展名，原代码传无扩展名路径被当作目录
  - **GeoTIFF 掩码带坐标系**：分割/变化检测新增带 CRS 的 `.tif` 掩膜（叠加图已被 v2.6 砍除，改图层透明度叠加）
  - **卡片坐标显示**：新增 `/api/v1/image/meta` 端点 + 前端坐标条（坐标范围 + CRS + 尺寸）
  - **沙盒路径回传**：`render_sandbox_image` 返回 `data.local_path`，AI 告知用户本地保存路径
  - 工具箱从 38 → **39 个 10 大分类**
- **v2.4**（2026-06）：**14 条核心功能点对接 + 自纠学习**（阶段 12-13）
  - CSV 清单精炼为 14 条（删除规则套合 #8/#9/#10 + 空间检索 #14 + 用户权限 #18）
  - 工具箱扩展到 **38 个 9 大分类**（新增 preprocess 3 + report 4 + database 4 元数据检索）
  - **数据存储层**（#2/#4）：image_metadata/vector_layer 表 + PostGIS ST_Transform + 时空检索
  - **业务类型 VLM 判读**（#7）：detect_change 自动调 qwen-vl 输出"新增建筑/耕地转林地/推土"
  - **报表生成**（#15/#16/#17）：统计图表 + PDF/Word + 矢量/Excel 导出
  - **前端 Leaflet**（#11/#12/#13）：多图层/卷帘/图斑弹窗/GeoJSON 叠加 + 任务日志面板
  - **沙盒镜像**新增 rio-cogeo/reportlab/python-docx/jinja2（镜像 2.71GB）
  - **三层记忆系统**：自纠学习教训融入长期记忆，Agent 跨会话记住踩坑+修复方法
  - SRID 自动转换 bug 修复（Web Mercator Auxiliary Sphere 识别）
- **v2.2**（2026-06）：**遥感化 + 视觉理解**
  - 角色定位从"数据管理系统助手"改为**遥感影像解译分析师**（System Prompt 遥感化 + 工具优先级重排 + CoT 删示例）
  - 新增 `understand_image` 视觉理解工具（qwen3.7-plus 多模态，真正"看图"解译）
  - 模型切换为百炼免费额度（主对话 qwen3.7-plus / 摘要 deepseek-v4-flash）+ 备选模型清单
  - SamSeg 改为按需加载（启动不预加载 3.3GB 权重，不做遥感零开销）
  - 三类遥感任务自动分流（理解 → understand_image / 分割 → segment_image / 变化 → detect_change）
- **v2.1**（2026-06）：三层重构（agent/model/data）+ 多对话并行 + 显式上下文缓存 + 三层记忆 + **SamSeg 遥感分析接入（含连通域统计 + 类别图注 + 原图即时显示）**
