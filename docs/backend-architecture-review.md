# 后端架构梳理与整改建议

> 基于 `E:\1代码\系统\Agent-RSCD\backend` 全量代码盘点（2026-08-08），定位真实职责分布、耦合关系、死代码与混乱点，给出可落地的整理方案。
> 所有结论附 `file_path:line_number` 锚点。

---

## ★ 重构进度（2026-08-08 执行）

| 项目 | 状态 | 说明 |
|------|------|------|
| **P0 拆 main.py** | ✅ 已完成 | `main.py` 从 1618 行 → 229 行（降 86%），路由拆到 `backend/api/` 下 11 个文件。32 个业务路由（31 HTTP + 1 WS）全部保留，TestClient 验证全部可达。 |
| P0 lifespan + shutdown | ✅ 已完成 | 废弃 `@app.on_event("startup")` 改为 `lifespan`，补 shutdown 连接池清理（`agent_db.close_pool()` / `business_db.close_pool()` 新增）。 |
| P0 全局异常处理 | ✅ 已完成 | 新增 `@app.exception_handler(Exception)` 统一 500 响应格式。 |
| **P1 拆 chat_service / agent_db** | ⏸ 未做 | 涉及核心推理/数据层，风险更高，待单独评审后推进。 |
| P2 删 context 死代码 | 🔄 改为保留 | 盘点发现 `context_priority.py` 有测试覆盖（`tests/agent_guardrail_eval.py` G1/G2），不删，改为在 `__init__.py` 标注"未接入主流程"状态。 |
| P2 team docstring | ✅ 已完成 | `team/__init__.py` 更新：已开放 verification + report 两个 worker。 |
| 回归验证 | ✅ 通过 | 护栏测试 10/10、plan_state、lesson_policy、change_detection_contract、agent_evaluation_dataset 全过。 |

### 拆分后的 api/ 目录

```
backend/api/
├── __init__.py          30 行   导出全部 router 模块
├── deps.py              36 行   共享依赖（路径常量 + agent_db/business_db/geoserver_client 单例）
├── schemas.py           76 行   8 个 Pydantic 请求模型集中
├── projects.py          41 行   /api/v1/projects（4 路由）
├── workspace.py         87 行   /api/v1/workspace/list（1 路由 + 递归列表 helper）
├── conversations.py    193 行   /api/v1/conversations（6 路由 + fork/regenerate/branches + 资源回收）
├── tasks.py             49 行   /api/v1/tasks（3 路由）
├── geoserver.py        227 行   /api/v1/geoserver（5 路由：WMS代理/feature-info/图层管理）
├── files.py            663 行   下载/上传/meta/shp（7 路由 + CRS预检/元数据登记 helper）
├── sandbox.py           27 行   /api/v1/sandbox/download-to-host（1 路由）
├── system.py            26 行   /api/v1/debug/log（1 路由）
└── ws.py               164 行   WebSocket /api/v1/agent/ws/chat（chat_service 单例注入）
```

### main.py 现在只做 4 件事（229 行）

1. 环境变量 workaround（OMP）+ sys.path + 工具注册 import
2. `lifespan` 生命周期（schema 初始化 / 存储目录 / 沙盒诊断 / 连接池关闭）
3. app 实例化 + CORS + 全局异常处理 + `include_router` 挂载
4. `__main__` uvicorn 启动入口

---


---

## 一、当前真实全景（不是文档描述的版本）

现有 `docs/architecture.md` 还停留在早期版本（15 轮循环、25 工具），与代码严重脱节。下面是**当前实际状态**。

### 1.1 物理分层与规模

```
backend/
├── main.py              1618 行  ★ 巨型入口：24+ HTTP路由 + 1 WS路由全堆此文件
├── config.py             305 行  全局配置 dataclass
├── agent/                        🧠 智能体层（8 个子包，最复杂）
│   ├── chat_service.py  1613 行  ★ 推理循环 + 业务编排 + 资源回收 全在里面
│   ├── ws_manager.py     185 行  WebSocket 连接/任务管理
│   ├── memory/                   记忆系统（agent_db.py 2217 行 ★ 最大文件）
│   │   ├── agent_db.py  2217 行  ★ 会话/消息/记忆/任务/产物 5 类职责过载
│   │   ├── memory_context.py 916 行  上下文构建+持久化+摘要+长期记忆提取
│   │   ├── task_state.py 568 行  计划状态机
│   │   ├── self_correction.py 445 行
│   │   ├── lesson_policy.py 209 行
│   │   └── pattern_tracker.py 200 行
│   ├── prompt/                   提示工程（4 文件，清晰）
│   ├── runtime/                  执行运行时（observation_builder/retry_guard/skill_execution/task_spec/context_vars）
│   ├── team/                     多智能体协作（8 文件，全活）
│   ├── observability/            可观测性（3 文件 JSONL 审计）
│   ├── context/                  ★ 死代码子包（context_priority 零引用）
│   └── tools/                    agent 自身工具（memory_tools + team_tools）
├── model/                        🤖 模型/工具层
│   ├── llm_client.py    99 行   LLM 客户端封装
│   ├── tools/                    47 工具 10 大类（16 文件，已治理）
│   ├── skills/                   技能编排（loader + selector + 3 个 .md 方案）
│   └── SamSeg/                   遥感分割算法封装（runner 951 行 + SamSeg 子项目）
├── data/                         💾 数据层（最干净）
│   ├── business_db.py  826 行   业务库通用 CRUD
│   ├── geoserver_client.py 1458 行  GeoServer REST/WMS 客户端
│   ├── knowledge/       ★ 空壳占位（knowledge_available 硬编码 False）
│   ├── mask/region.py          连通域分析（有 3 份克隆，见 §3.3）
│   └── vector/vectorize.py     栅格转矢量算法（底层引擎，被复用）
└── utils/                        async_subprocess + conv_file_cleanup
```

### 1.2 真实依赖关系图

```
            ┌─────────────────────────────────────────────────┐
            │  main.py (FastAPI 入口 + 24路由 + WS + 启动)      │
            └──────┬───────────────────────────┬──────────────┘
                   │ 实例化                      │ HTTP/WS 端点直接调
                   ▼                            ▼
    ┌──────────────────────┐         ┌──────────────────┐
    │ agent/chat_service   │◄────────┤ agent_db (CRUD)  │
    │ (推理循环+编排)       │         │ business_db      │
    └──┬──┬──┬──┬──┬───────┘         │ geoserver_client │
       │  │  │  │  │                  └──────────────────┘
       │  │  │  │  └─► model/tools (47工具) ──► data/, SamSeg/
       │  │  │  └────► model/llm_client
       │  │  └───────► agent/team (验证+修复+report worker)
       │  └──────────► agent/observability (审计)
       └─────────────► agent/memory (记忆+任务状态)
```

**依赖方向整体正确**：`agent → model → data` 不反向（`agent/tools/__init__.py:4-6` 有显式约束）。问题不在"方向错"，而在**单文件职责过载**和**残留死代码**。

---

## 二、核心混乱点（按严重程度排序）

### 2.1 ★★★ main.py：1618 行巨型文件，路由零拆分

**问题**（`main.py`）：
- **24+ HTTP 路由 + 1 WebSocket 路由全部堆在一个文件**，全仓搜索 `APIRouter` 命中数为 0。
- 路由横跨 **8 个业务域**：项目 / 工作区 / 会话 / 任务 / GeoServer / 文件上传下载 / 沙盒 / 调试，仅靠注释块 `# ====================` 分隔。
- 顶层还散落 **8 个 Pydantic 请求模型**（`CreateProjectRequest` 等）和 **5 个业务 helper**（`_reclaim_conversation_resources`、`_reject_ungeoreferenced`、`_try_register_image_metadata`、`_rand_suffix`、`_list_recursive`），与路由穿插定义。
- 用已废弃的 `@app.on_event("startup")`（`main.py:86`），**无 lifespan、无 shutdown 事件**，连接池无显式清理。
- **无全局异常处理中间件**（只有 CORS），错误处理靠每个路由内部 `try/except` 返回不一致的 `{status:"error"}` 或抛 `HTTPException`，风格不统一。

**影响**：改任何一个业务域的接口都要在 1600 行文件里定位；新增路由找不到放哪；路由与业务逻辑耦合无法单独测试。

### 2.2 ★★★ chat_service.py：1613 行，推理与业务编排未分离

**问题**（`chat_service.py:993` `tool_chat_ws`）：
主循环 `tool_chat_ws` 单个方法超 600 行，混合了至少 6 类职责：
1. **推理循环**（LLM 调用、重试、流式）——核心职责
2. **工具执行调度**（同步/异步分流、frontend_action 阻塞）
3. **业务编排**（`segment_image` 成功后派发 report worker，`chat_service.py:1469`）
4. **资源回收**（停止时删消息、回收沙盒容器，`chat_service.py:1545`）
5. **下载短路**（"下载报告"复用最近产物，`chat_service.py:1071-1112`）
6. **持久化编排**（persist_user_message、plan state、artifact 登记）

其中 3-6 是**业务策略**，本该外提为独立的编排/handler 模块，现在全嵌在推理循环里，导致主流程难以阅读和维护。

### 2.3 ★★★ agent_db.py：2217 行，5 类数据职责过载

**问题**（`agent_db.py`）：
单文件承载了 **5 类完全不同的数据职责**：
- 会话管理（conversation，`:391`）
- 消息 + DAG 分支（message + node_id/parent_id，`:413` fork）
- 短期/长期记忆（conversation_summary、user_memory，`:1246`）
- 任务/计划（ai_task + ai_task_log，`:1638`）
- 产物血缘（artifact，`:329` 的登记逻辑在 chat_service，schema 在这里）

虽然它们共用一个 PostgreSQL 连接池（合理），但 **CRUD 逻辑全堆在一个文件**导致：找任何一张表的接口都要在 2000 行里翻；修改任务表接口可能误碰消息表逻辑。

### 2.4 ★★ context 子包：整个是死代码

**问题**（`agent/context/context_priority.py`）：
- `context_priority.py` 设计了"上下文冲突裁决"（`resolve_context_conflict`，`:38`），但**全仓零外部引用**（grep 确认）。
- `context/__init__.py` 只有一行 docstring，连 import 都没有。
- 讽刺的是，`validation_stage.py:93-156` 的 `build_repair_execution_card` 正在做本该它做的事（写 `latest_evidence_overrides`），却是**硬编码实现**，没调用这个模块。
- 判定：**整个 context 子包是未完工的规划残留**。

### 2.5 ★★ 文档与实现严重脱节

| 文档描述 | 实际代码 |
|---------|---------|
| architecture.md："最多 15 轮工具调度" | `chat_service.py:1114-1118` 明确注释"无工具调用轮次上限" |
| architecture.md："25 个工具" | 实际 47 工具 10 大类（`tool_registry.py`） |
| architecture.md 总图 | 缺 team/observability/context 子包，缺 plan state |
| `team/__init__.py:13-18` docstring："仅开放 verification_agent" | 实际已开放 report_agent（`agent_roles.py:11`） |
| architecture-agent.md：retry_guard"已定义未调用" | 实际已在 `chat_service.py:1530` 激活 |

### 2.6 ★☆ 知识库空壳 + mask 算法克隆

- **`data/knowledge/knowledge_store.py`**：`knowledge_available()` 硬编码返回 `False`（`:30`），`search` 返回 `[]`，无任何业务调用方。属于**有意保留的占位**，不是 bug，但占据心智负担。
- **mask 连通域分析有 3 份克隆**：
  - `data/mask/region.py`（后端工具链用，活）
  - `model/SamSeg/SamSeg/utils/tools/cd1_extract_regions.py:52,193`（SamSeg 推理用）
  - `model/SamSeg/SamSeg/utils/tools/cd2_clip_classify.py:253`（SamSeg 推理用）
  - 同一算法三套独立实现，各自维护。

---

## 三、整理方案（按优先级）

### 优先级 P0：拆 main.py（收益最高、风险可控）

按业务域拆成 `APIRouter`，保持路由行为不变（纯结构重组）：

```
backend/
├── main.py                 ← 只留 app 实例化 + lifespan + 路由挂载（~150 行）
├── api/                    ← 新增
│   ├── __init__.py
│   ├── schemas.py          ← 8 个 Pydantic 请求模型集中
│   ├── deps.py             ← 共享依赖（agent_db/ws_manager/sandbox 注入）
│   ├── projects.py         ← router: /api/v1/projects（4 路由）
│   ├── workspace.py        ← router: /api/v1/workspace（1 路由）
│   ├── conversations.py    ← router: /api/v1/conversations（6 路由，含 fork/regenerate）
│   ├── tasks.py            ← router: /api/v1/tasks（3 路由）
│   ├── geoserver.py        ← router: /api/v1/geoserver（5 路由）
│   ├── files.py            ← router: 文件上传/下载/meta/shp-to-geojson（6 路由）
│   ├── sandbox.py          ← router: /api/v1/sandbox（1 路由）
│   ├── system.py           ← router: /api/v1/debug（1 路由）
│   └── ws.py               ← WebSocket /api/v1/agent/ws/chat（1 路由）
```

main.py 收敛为：
```python
app = FastAPI(lifespan=lifespan_handler)  # 用 lifespan 替代废弃的 on_event
app.include_router(projects.router)
# ... 其余 router
```

同时：把 `_reclaim_conversation_resources` 等 helper 移到对应业务域模块；加一个全局 `@app.exception_handler` 统一错误响应格式。

### 优先级 P1：拆 chat_service.py 的业务编排

把主循环里的非推理职责外提，**推理循环本身保持不动**（改动风险最高）：

- 新增 `agent/runtime/dispatcher.py`：把 `segment_image → report worker` 派发、下载短路等**业务编排策略**从 `tool_chat_ws` 里抽出来，主循环通过钩子/回调调用。
- 新增 `agent/runtime/lifecycle.py`：把停止回滚、资源回收（沙盒容器回收、消息删除）抽成独立函数，`tool_chat_ws` 的 `except CancelledError` 只负责调用。
- `tool_chat_ws` 收敛为：上下文准备 → 推理循环 → 终止处理 三段，每段调用独立模块。

### 优先级 P1：拆 agent_db.py

按数据域拆，共用一个连接池模块：

```
agent/memory/
├── db.py              ← 连接池 + get_conn + init_schema（共享基础设施）
├── conversation_repo.py  ← conversation + message + DAG 分支
├── memory_repo.py        ← conversation_summary + user_memory
├── task_repo.py          ← ai_task + ai_task_log
└── artifact_repo.py      ← artifact 血缘
```

保持 `agent/memory/__init__.py` 导出不变，对外接口向后兼容（`from backend.agent.memory import agent_db` 仍可用，内部转发）。

### 优先级 P2：清理死代码与空壳

| 对象 | 处理 |
|------|------|
| `agent/context/` 整个子包 | **删除**，或把 `resolve_context_conflict` 接进 `validation_stage` 后删整个子包。功能已被硬编码替代。 |
| `data/knowledge/` | 保留但**明确标注"未实现占位"**，或移到 `data/_skeleton/` 表明状态，减少误读。 |
| mask 连通域 3 份克隆 | 让 SamSeg 推理管线也 import `data.mask.region`，删除 SamSeg 下的 2 份克隆（`cd1_extract_regions.py`、`cd2_clip_classify.py` 里的重复函数）。注意 SamSeg 是子项目，改它要确认不破坏其独立运行。 |
| `team/__init__.py` docstring | 更新：已开放 verification + report 两个 worker。 |
| architecture-agent.md retry_guard 描述 | 更新：已激活。 |

### 优先级 P3：同步架构文档

`docs/architecture.md` 与实际代码差距过大，建议：
- 更新工具数（25→47）、循环上限（15 轮→无上限）、新增 team/observability 子包。
- 或在 architecture.md 顶部加醒目标注"本文档截至 2026-06，最新实现以 `docs/agent/core/architecture-agent.md` + 代码为准"。

---

## 四、不建议动的部分（已经合理）

避免过度重构，以下设计经盘点确认**合理，应保留**：

1. **agent/tools 与 model/tools 的两层拆分**：分界依据是依赖方向（model 严禁反向依赖 agent），memory_tools/team_tools 必须留在 agent 层。不是"散落"是"分层"。
2. **model/tools 内部 16 文件**：已做过收敛治理（路径逻辑进 `_paths.py`、shapefile 进 `_shapefile.py`、3 个下载工具收敛为 1 个）。当前状态是治理后结果，无需再归并。
3. **data/vector/vectorize.py 与 model/SamSeg/geoio.py**：是**算法-应用分层**（vectorize 是引擎，geoio 是 SamSeg 的应用壳并复用 vectorize），不是职责重叠。
4. **team 子包 8 文件全部在用**：没有死代码，只有文档/白名单的小噪声。
5. **agent_db 用 PostgreSQL 连接池 + UPSERT + 短事务**：一致性策略合理，无需加显式锁（依赖单会话单用户假设）。

---

## 五、整理后的目标架构

```
backend/
├── main.py                 ~150 行（纯入口）
├── api/                    路由层（按业务域拆 8 router + ws）
├── config.py
├── agent/
│   ├── chat_service.py     推理循环（瘦身，业务编排外提）
│   ├── runtime/
│   │   ├── dispatcher.py   业务编排钩子（新）
│   │   ├── lifecycle.py    停止/回收（新）
│   │   ├── observation_builder.py
│   │   └── ...（retry_guard/skill_execution/task_spec/context_vars）
│   ├── memory/
│   │   ├── db.py           连接池（从 agent_db 拆出）
│   │   ├── conversation_repo.py
│   │   ├── memory_repo.py
│   │   ├── task_repo.py
│   │   ├── artifact_repo.py
│   │   ├── memory_context.py
│   │   └── ...（task_state/self_correction/lesson_policy/pattern_tracker）
│   ├── prompt/             （不变）
│   ├── team/               （不变，更新 docstring）
│   ├── observability/      （不变）
│   └── tools/              （不变）
├── model/                  （不变）
└── data/                   （不变，清理 knowledge 占位标注）
```

---

## 六、行动清单（可勾选）

- [ ] **P0** 拆 main.py → `api/` 8 个 router + schemas.py + deps.py
- [ ] **P0** main.py 启动改 lifespan，补 shutdown 连接池清理
- [ ] **P0** 加全局 exception_handler 统一错误响应
- [ ] **P1** chat_service 业务编排外提（dispatcher + lifecycle）
- [ ] **P1** agent_db 按数据域拆 4 个 repo（保持 __init__ 向后兼容）
- [ ] **P2** 删除 agent/context/ 死代码子包
- [ ] **P2** 清理 mask 连通域 3 份克隆（SamSeg 复用 data.mask.region）
- [ ] **P2** 更新 team/__init__.py docstring（已开放 report）
- [ ] **P3** 同步 architecture.md（工具数/轮次/子包）
```
