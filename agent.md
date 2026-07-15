# 后端 Agent 架构说明

本文根据 `docs/architecture.md`、`docs/architecture-agent.md`、`docs/architecture-tools.md`、`docs/design-overview.md`、`docs/communication.md` 以及 `backend/agent/`、`backend/model/tools/`、`backend/model/skills/` 当前代码整理，聚焦后端 Agent 的上下文、记忆、工具、技能设计。

## 1. 总体定位

本项目后端 Agent 是面向遥感影像解译的 ReAct 智能体执行引擎。核心职责不是直接完成单个函数调用，而是把用户自然语言目标转成可验证的多轮执行链：

1. WebSocket 接收用户请求，确定 `conversation_id`、`user_id`、`project_id`。
2. 注入运行时上下文，构造 LLM 上下文消息。
3. 绑定工具白名单，流式调用 Qwen/DashScope 模型。
4. 若 LLM 返回 `tool_calls`，执行对应工具，并把结果作为 `ToolMessage` 回灌。
5. 若工具需要前端协作，后端发送 `frontend_action` 并按 `request_id` 等待前端 `tool_result`。
6. LLM 不再返回工具调用时，输出最终回复，持久化消息并异步触发摘要、长期记忆和自纠学习。

关键文件：

| 模块 | 文件 | 职责 |
|---|---|---|
| 推理循环 | `backend/agent/chat_service.py` | ReAct 主循环、流式 thinking、工具调度、停止回滚、异步收尾 |
| 上下文与记忆编排 | `backend/agent/memory/memory_context.py` | 构造上下文、trim、工具结果折叠、摘要、长期记忆提取 |
| 记忆数据库 | `backend/agent/memory/agent_db.py` | project / conversation / message / summary / user_memory / ai_task 持久化 |
| System Prompt | `backend/agent/prompt/system_prompt.py` | 成功条件、ReAct 规则、工具目录、动态记忆块 |
| 运行时上下文 | `backend/agent/runtime/context_vars.py` | 用 `ContextVar` 隔离当前会话、用户、项目 |
| 工具注册表 | `backend/model/tools/tool_registry.py` | 工具注册、分类、LLM 暴露白名单、按名称查工具 |
| 技能工具 | `backend/model/tools/skill_tools.py` | `lookup_skill` 按需检索长任务方案 |
| 技能加载器 | `backend/model/skills/loader.py` | 扫描 `model/skills/*.md`，按关键词检索工作流 |
| WebSocket 管理 | `backend/agent/ws_manager.py` | 连接管理、多对话任务管理、前端回执等待 |

## 2. 上下文设计

上下文分成两类：推理上下文和运行时上下文。

### 2.1 推理上下文

推理上下文由 `memory_context.build_context_messages()` 构造，最终形成：

```text
[SystemMessage] + 历史消息 + [当前 HumanMessage]
```

`SystemMessage` 不是固定字符串，而是由三部分组成：

| 部分 | 来源 | 作用 |
|---|---|---|
| 静态规则 | `system_prompt.py` | 定义遥感解译角色、成功条件、行为红线、ReAct 策略 |
| 工具目录 | `tool_registry.get_tools_catalog_for_llm()` | 与真实 `bind_tools()` 白名单保持一致 |
| 动态记忆块 | summary / task_state / user_profile | 注入短期摘要、当前任务状态、长期记忆 |

上下文构建步骤：

1. 读取长期记忆：`user_memory`，格式化为用户画像和已学教训。
2. 读取短期记忆：`conversation_summary`，避免旧消息重复塞入窗口。
3. 读取工作记忆：`message` 表中 `summarized_upto` 之后的活跃消息。
4. 生成任务状态卡：`task_state.build_task_state_text()` 提炼当前目标、前序目标、最近动作、最近观察、重工具任务状态。
5. 拼装 system prompt、历史消息和当前输入。
6. 在 trim 前执行 `fold_tool_messages()`，把旧工具结果压成短摘要，但保留 `tool_call_id` 配对。
7. 用 `trim_messages()` 控制 `context_max_tokens`，默认 16000。
8. 用 `_repair_tool_pairing()` 修复 trim 后可能残留的孤儿 `tool_calls`。
9. 强制确保当前 HumanMessage 不会被裁掉。

设计重点：

- system prompt 承载规则和记忆，不承载完整历史。
- 历史消息以 LangChain 消息列表传入，保留 `AIMessage.tool_calls` 与 `ToolMessage` 链路。
- 旧工具结果使用可逆折叠降低 token，而不是直接删除。
- token 超限时，`chat_service._force_compress_and_retrim()` 会触发强制摘要并重建上下文。

### 2.2 运行时上下文

运行时上下文由 `backend/agent/runtime/context_vars.py` 管理：

| 变量 | 含义 | 使用场景 |
|---|---|---|
| `current_conversation_id` | 当前对话 ID | 记忆工具、沙盒工具定位会话资源 |
| `current_user_id` | 当前用户 ID | 长期记忆读写 |
| `current_project_id` | 当前项目 ID | 沙盒工作目录和项目级资源隔离 |

`chat_service.tool_chat_ws()` 在每次对话任务开始时调用 `set_runtime_context()`。由于使用 `ContextVar`，同一个 WebSocket 连接里多个对话并行运行时不会串号。

这层上下文解决的问题是：LangChain `@tool` 只能接收 LLM 给出的显式参数，拿不到当前会话、用户、项目等运行时信息，因此通过 `ContextVar` 让工具在执行期读取当前上下文。

## 3. 记忆设计

记忆采用三层结构：

| 层级 | 表 / 来源 | 生命周期 | 注入方式 | 主要内容 |
|---|---|---|---|---|
| 长期记忆 | `user_memory` | 跨会话 | system prompt 用户画像 | 偏好、事实、环境事实、已学教训、已学流程 |
| 短期记忆 | `conversation_summary` | 单会话 | system prompt 会话摘要 | 早期对话压缩摘要 |
| 工作记忆 | `message` + `ai_task` | 当前任务链 | 消息列表 + 任务状态卡 | 近期消息、工具调用链、最近任务状态 |

### 3.1 长期记忆

长期记忆通过 `agent_db.save_user_memory()` 写入 `user_memory` 表，按 `user_id + key` UPSERT。

来源有三类：

1. 用户或 Agent 主动调用记忆工具：`save_user_memory_item()`。
2. 对话收尾时 `extract_and_save_long_term_memory()` 提取偏好/事实。
3. 自纠捕捉器 `self_correction.scan_and_save_corrections()` 识别“工具失败 -> 修正 -> 成功”模式，沉淀 `lesson` 或 `workflow`。

当前实现中，偏好/事实提取由 `settings.enable_preference_extraction` 控制，默认值来自 `ENABLE_PREFERENCE_EXTRACTION`，代码默认是 `true`。如果设为 `false`，注入 prompt 时只保留 `lesson`、`workflow`、`envfact`，用于减少低价值偏好污染。

全局教训通过 `user_id='global'` 存储，所有用户构建长期记忆时都会合并读取。

### 3.2 短期记忆

短期记忆用于解决长对话 token 膨胀。`maybe_summarize()` 在对话收尾或 token 超限时触发：

| 级别 | 策略 | 特点 |
|---|---|---|
| Level 1 | 折叠旧工具消息 | 可逆，保留消息结构和工具配对 |
| Level 2 | LLM 摘要旧消息 | 不可逆，保存摘要后删除被摘要覆盖的旧消息 |

`conversation_summary` 记录：

- `summary`：摘要正文。
- `summarized_upto`：摘要覆盖到的最大 message id。
- `compress_level`：压缩级别。

下次构建上下文时，只加载 `summarized_upto` 之后的消息，避免摘要内容和旧消息重复注入。

### 3.3 工作记忆

工作记忆由两部分组成：

1. `message` 表中的近期 Human / AI / Tool 消息。
2. `ai_task` 表中的重工具任务状态。

`task_state.build_task_state_text()` 会从近期历史中提炼：

- 当前目标。
- 前序目标。
- 最近动作。
- 最近观察。
- 持久化任务状态。
- 是否存在早期摘要。

这张状态卡被放入 system prompt 的“当前任务状态”段，目的是把隐式历史变成显式进度，降低长链任务中途跑偏或过早收尾的概率。

## 4. 工具设计

工具系统由注册表、白名单、执行器、结果协议四部分组成。

### 4.1 注册机制

工具使用 LangChain `@tool` 包装，再由 `@register_tool("category")` 注册到全局表。

装饰器顺序必须是：

```python
@register_tool("category")
@tool
def some_tool(...):
    ...
```

原因是 Python 装饰器从下往上执行，`register_tool` 需要拿到已被 `@tool` 包装后的 `StructuredTool`。

注册表维护两份数据：

| 数据 | 作用 |
|---|---|
| `_tool_registry: category -> {tool_name -> tool}` | 分类保存全部工具 |
| `_tool_category_map: tool_name -> category` | 反查工具分类，用于 prompt 和重工具判断 |

### 4.2 LLM 暴露白名单

注册表保存全部工具，但不是全部暴露给 LLM。`_LLM_EXPOSED_TOOL_NAMES` 控制哪些工具会进入：

- `create_llm_with_tools(...).bind_tools()`。
- system prompt 的工具目录。

这样做的目的是收窄 Agent 决策空间，保留内部工具、兼容工具和诊断工具，但不让 LLM 主动乱选。

当前白名单覆盖：

- 遥感解译：`segment_image`、`detect_change`、`understand_image`。
- GeoServer：服务查询、图层下载、栅格/矢量发布、删除图层。
- 前端地图：加载图层、定位、显隐控制。
- 分析报告：连通域分析、矢量导出、统计图表、监测报告。
- 数据检索：影像/矢量目录与时空查询。
- 沙盒：Python/Shell 执行、图片渲染、文件导入导出。
- 长任务技能：`lookup_skill`。
- 记忆管理：查看、保存、删除、清空长期记忆和会话摘要。

注意：`list_available_skills` 已注册为 skill 工具，但当前没有进入 LLM 暴露白名单，普通 ReAct 循环不会主动绑定它。

### 4.3 执行机制

`chat_service.tool_chat_ws()` 的工具执行流程：

1. LLM 流式返回完整 `AIMessage`。
2. 若存在 `tool_calls`，先把 `AIMessage` 加入 `all_messages`。
3. 对每个工具调用发送 `tool_call` 事件给前端。
4. 根据工具分类判断是否属于重工具。
5. 重工具写入 `ai_task`，记录输入、状态、进度、日志。
6. 查注册表得到工具对象。
7. 异步工具使用 `tool_func.ainvoke()`，同步工具使用 `asyncio.to_thread(tool_func.invoke, args)`，避免阻塞事件循环。
8. 工具异常被转成结构化 error dict，并包含 `error_type`。
9. 若返回 `frontend_action`，后端发送前端事件；当 `wait_for_result=true` 时等待前端 `tool_result`。
10. 若工具结果含产物路径但缺少 `verification`，传输层自动补路径校验。
11. 结果序列化成 `ToolMessage` 回灌给 LLM，进入下一轮 ReAct。

### 4.4 工具结果协议

工具结果统一使用 dict：

| `type` | 含义 |
|---|---|
| `success` | 纯后端成功结果 |
| `error` | 工具失败，交给 LLM 诊断和修正 |
| `frontend_action` | 需要前端执行渲染、加载、定位、下载等动作 |

关键字段：

- `summary`：给 LLM 和用户看的简要结论。
- `data`：结构化结果。
- `instruction`：前端动作类型和参数。
- `wait_for_result`：是否阻塞等待前端回执。
- `verification`：产物存在性、数量、路径等客观校验。
- `error_type`：异常类型，供自纠学习识别。

反伪装成功由三层共同保证：

1. Prompt 层要求不能只看 `summary`，必须看 verification、数量、产物可用性。
2. 工具层尽量返回结构化 verification。
3. 传输层在 `chat_service` 中为缺失 verification 的路径产物补校验。

## 5. 技能设计

这里的“技能”不是 Python 函数，也不是外部 Agent 插件，而是长任务工作流文档。

目录：

```text
backend/model/skills/
├── loader.py
├── README.md
├── wf1-segment-visualize-report.md
├── wf2-change-detection-visualize-report.md
└── wf4-upload-geoserver-display.md
```

设计原则：

- 普通工具解决“一个动作”。
- 技能解决“多步骤流程”。
- 技能内容较长，不常驻 system prompt，避免持续消耗上下文。
- Agent 识别到复杂长任务后，调用 `lookup_skill(query)` 检索方案全文，再按步骤调用普通工具执行。

运行链路：

```text
用户提出复合任务
  -> LLM 判断需要长任务方案
  -> 调用 lookup_skill(query)
  -> skill_tools.py 调用 skills.loader.retrieve_skills()
  -> loader.py 扫描并匹配 backend/model/skills/*.md
  -> 返回最佳方案全文
  -> LLM 按方案逐步调用 segment / geoserver / report / frontend 等工具
```

`loader.py` 当前使用关键词重叠计数，不依赖向量库。每个 skill 文档通过首个一级标题、文件名、标题行和“触发场景”段落生成关键词。新增技能只需要增加 `.md` 文件，无需改代码。

已有技能：

| 技能 | 文件 | 典型任务 |
|---|---|---|
| 一键分割 + 可视化 + 报告 | `wf1-segment-visualize-report.md` | 单图分割、地物识别、生成报告 |
| 一键变化检测 + 可视化 + 报告 | `wf2-change-detection-visualize-report.md` | 双时相变化检测、违建监测 |
| 上传影像到 GeoServer + 显示 | `wf4-upload-geoserver-display.md` | 发布图层、上传到地图服务并展示 |

## 6. WebSocket 与多对话并行

`ws_manager.py` 支持同一 WebSocket 连接中并行多个 conversation：

| 数据结构 | 作用 |
|---|---|
| `connections[session_id]` | 当前 WebSocket 连接 |
| `active_tasks[session_id][conversation_id]` | 每个对话对应的 Agent 推理任务 |
| `pending_events[request_id]` | 等待前端回执的异步事件 |
| `frontend_results[request_id]` | 前端返回的执行结果 |

所有下行消息都带 `conversation_id`，前端据此路由到对应对话 Tab。

前端协作协议：

1. 后端发 `frontend_action`，携带 `event_type`、`event_data`、`request_id`。
2. 前端执行对应 handler。
3. 前端回传 `tool_result`，携带相同 `request_id`。
4. 后端 `wait_for_frontend_result()` 解除阻塞，把前端结果合并进工具结果。

停止机制：

- 用户发送 `stop_chat` 后，按 `conversation_id` 精确取消任务。
- `chat_service` 捕获 `CancelledError`。
- 根据 checkpoint 删除本轮 AI/Tool 消息，保留已提前持久化的 HumanMessage。
- 推送 `chat_stopped`。
- 必要时清理空会话和沙盒资源。

## 7. 架构边界

依赖方向保持为：

```text
agent -> model -> data
```

具体边界：

- `agent` 层负责推理、prompt、记忆、WebSocket、运行时上下文。
- `model` 层负责 LLM 客户端、工具箱、技能加载、SamSeg 推理封装。
- `data` 层负责业务库、GeoServer、文件与知识数据。
- 记忆工具留在 `backend/agent/tools/`，因为它操作 Agent 自身记忆，避免出现 `model -> agent` 的反向依赖。
- 普通业务工具在 `backend/model/tools/`，由工具注册表统一暴露。

## 8. 当前设计结论

后端 Agent 的核心设计方式可以概括为：

| 问题 | 当前方案 |
|---|---|
| 上下文怎么设计 | system prompt 动态生成，注入工具目录、会话摘要、任务状态、长期记忆；历史消息以 LangChain 消息列表传入；token 超限通过折叠、trim、摘要和配对修复处理 |
| 记忆怎么设计 | 三层记忆：长期 `user_memory`、短期 `conversation_summary`、工作记忆 `message + ai_task`；偏好/事实提取可开关，自纠教训跨会话共享 |
| 工具怎么设计 | `@tool + @register_tool` 注册；完整注册表和 LLM 白名单分离；工具结果标准化；支持前端动作回执；重工具持久化任务状态 |
| 技能怎么设计 | 技能是 Markdown 长任务编排方案，不常驻 prompt；通过 `lookup_skill` 按需检索全文，再由 LLM 按方案逐步调普通工具执行 |

这套架构的重点不是让 LLM 一次性回答，而是让 LLM 在受控工具空间内持续执行、观察、验证和修正，直到用户目标闭环或明确遇到不可继续的阻塞。
