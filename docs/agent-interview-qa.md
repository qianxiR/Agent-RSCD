# Agent-RSCD 面试题与答案

> 面向 Agent 开发岗位，从智能体基础概念 → 记忆与上下文 → 算法核心 → 鲁棒性与自修复 → 工程化应用。
> 每题锚定本仓库（`E:\1代码\系统\Agent-RSCD`）的真实实现，引用具体 `file_path:line_number`。

---

## 目录

| 模块 | 题号 | 考察能力 |
|------|------|----------|
| 一、基础概念 | Q1–Q3 | 理解 Agent 本质、ReAct vs Function Calling、主循环 |
| 二、记忆与上下文 | Q4–Q6 | 三层记忆、检索策略取舍、上下文压缩 |
| 三、算法核心 | Q7–Q9 | 工具系统设计、SAM 推理、结果验证 |
| 四、鲁棒性与自修复 | Q10–Q11 | 验证-诊断-修复闭环、确定性优先哲学 |
| 五、工程化应用 | Q12–Q15 | 并发模型、可观测性、评测体系、系统 trade-off |
| 加分题 | Q16 | 代码审阅与潜在缺陷定位 |

---

## 模块一：基础概念（智能体是什么、怎么转）

### Q1. 什么是智能体（Agent）？它和普通 LLM 调用的本质区别是什么？

**要点**：Agent = LLM（大脑）+ 工具（手脚）+ 循环（自主决策）+ 状态（记忆）。

**参考答案**：普通 LLM 调用是"一问一答"的无状态函数；Agent 是一个**有状态的循环系统**——LLM 在每一轮自主决定"继续思考 / 调用工具 / 给出最终答案"，并能根据工具返回的观测（Observation）修正下一步行动。本项目的体现（`backend/agent/chat_service.py:993` `tool_chat_ws`）是一个**基于 WebSocket 的无限轮 ReAct 循环**：LLM 通过 function calling 决定调用哪个工具，工具结果回灌后再进入下一轮推理，直到 LLM 主动给出最终回复或用户停止。

---

### Q2. ReAct 和 Function Calling 有什么区别？本项目用的是哪种？

**要点**：ReAct 是"文本解析"模式，Function Calling 是"结构化协议"模式。本项目用 Function Calling，但 ReAct 作为**提示策略**存在。

**参考答案**：

- **ReAct（Reasoning + Acting）**：让 LLM 输出 `Thought / Action / Observation` 文本，再**用正则/JSON 解析**提取要调用的工具。缺点是解析脆弱、格式漂移会导致崩溃。
- **Function Calling**：LLM Provider（OpenAI Responses API）原生支持结构化工具调用，模型直接返回 `tool_calls` 列表（含工具名 + 参数 JSON），无需文本解析。

本项目（`backend/model/llm_client.py:89` `create_responses_llm_with_tools`）用 **OpenAI Responses API + LangChain `bind_tools(tool_choice="auto")`**，是标准 function calling。但 system prompt（`backend/agent/prompt/static_template.py:69-129`）里仍写了 ReAct 的 Thought/Action/Observation 循环策略——**这是作为"思考策略指令"注入提示，指导模型怎么推理，而不是作为解析协议**。

> 这是一个常见面试陷阱：ReAct 是策略，Function Calling 是协议，二者不互斥。

---

### Q3. 简述本项目 Agent 主循环的执行步骤和安全出口。

**要点**：能说清"一轮里发生了什么"以及"循环什么时候结束"。

**参考答案**（依据 `chat_service.py:993-1605`）：

1. 设置运行时上下文（`ContextVar` 隔离 conv_id/user_id）。
2. 加载三层记忆 + 构建系统提示 + 裁剪上下文（`build_context_messages`）。
3. 提前持久化 HumanMessage（防停止丢失），记录 checkpoint。
4. 进入 `while True` 主循环：
   - 流式调用 LLM（`_stream_llm_thinking_ws`），thinking chunk 实时推前端。
   - LLM 调用有**分类重试子循环**（token 超限→强制压缩；rate limit→退避；其他→立即重试）。
   - 若 LLM 无 `tool_calls` → 走**完成门**（`can_finalize_plan`）：计划必需步骤未全完成则注入 SystemMessage 强制继续；否则发 done，后台异步做记忆摘要。
   - 若有 `tool_calls` → 逐个执行工具（同步工具走 `asyncio.to_thread`，异步工具 `await ainvoke`），结果经 verification 验证 + repair_plan 注入回灌，追加 ToolMessage 进入下一轮。

**三个安全出口**：

1. LLM 给最终回复且完成门通过；
2. LLM 连续失败超 `llm_retry_max`；
3. 用户停止（`CancelledError`）触发回滚（`delete_messages_after(checkpoint_id)`，保留 HumanMessage 删 AI/Tool）。

---

## 模块二：记忆与上下文管理

### Q4. 一个工业级 Agent 的记忆系统应该分几层？本项目是怎么做的？

**要点**：三层（工作/短期/长期）+ 任务状态层。

**参考答案**（依据 `backend/agent/memory/memory_context.py:6-16`）：

- **工作记忆（buffer）**：`message` 表，存近期对话消息（含 tool_calls 的 JSONB）。检索方式：`load_messages_since`（`agent_db.py:1269`）按 id 取摘要边界之后的活跃消息。
- **短期记忆（summary）**：`conversation_summary` 表，本会话旧消息的 LLM 摘要 + `summarized_upto` 水位线。
- **长期记忆（long-term）**：`user_memory` 表，跨会话的偏好/事实/教训/流程（`category` 字段区分），UPSERT 去重。
- **任务/计划记忆**：`ai_task` 表 + 内存状态机（`task_state.py`），存显式执行计划的步骤状态。

**注入顺序**：长期→短期→任务状态→工作记忆，全部组装进 system prompt 或消息列表，再经 token 裁剪。

**存储选型**：PostgreSQL（非 SQLite/JSON 文件），`psycopg2.pool.ThreadedConnectionPool`，Agent 记忆库（`Agent_study`）与业务库（`cd`）物理隔离。

---

### Q5. 【关键题】本项目用向量检索还是规则检索？为什么？长期记忆全量注入会不会有问题？

**要点**：本项目**完全没有** embedding/向量检索/相似度排序。这是面试官常挖的坑——不要想当然说"肯定用了 RAG"。

**参考答案**（依据探索结论 + `agent_db.py:1356-1380`）：

本项目长期记忆用**全量扫描注入**（`load_user_memory` 取该用户全部 + `user_id='global'` 全局教训），靠 `UNIQUE(user_id, key)` 的 UPSERT 去重控制规模。代码注释明确"学习项目记忆量小，全量注入"。

**为什么不用向量检索**：

1. 记忆量小，向量检索的建索引/查询开销 > 全量注入。
2. 确定性优先：规则检索可复现，向量检索有随机性（近似最近邻），与本项目"确定性 verification/repair"的哲学冲突。
3. 教训类记忆价值密度高、条数少，全量注入反而召回更全。

**全量注入的风险与缓解**：当记忆膨胀到数百条时，会挤占 token 预算。本项目的缓解策略：

1. 偏好提取默认收敛（实测 86% 是低价值污染，已弱化）；
2. lesson 入库有严格门控（必须修复成功 + verification passed 才入库，`self_correction.py:31-35`）；
3. UPSERT 同 key 覆盖天然防增长。

这是"用质量门控替代检索"的取舍。

---

### Q6. 上下文窗口超了怎么办？讲讲多级压缩策略。

**要点**：可逆 vs 不可逆压缩的区分是亮点。

**参考答案**（依据 `memory_context.py:160-343, 513-634`）：

本项目有三级压缩，核心设计是**按可逆性分层**：

- **Level 0.5（可逆）`fold_tool_messages`**：把已完成的旧工具消息折叠成 `[已完成·工具名·type] 摘要前80字`，保留 tool_call_id 不破坏配对，每轮实时做。
- **Level 1（可逆）drop**：`trim_messages(strategy="last", max_tokens=16000)`，保留最近消息，强制保留当前 HumanMessage 和 SystemMessage。
- **Level 2（不可逆）LLM 摘要**：当 token 超过 `threshold × 1.5`，取旧消息调轻量模型生成结构化摘要，删旧消息，更新 `summarized_upto`。

**强制兜底**（`_force_compress_and_retrim`, `chat_service.py:926`）：当 LLM 报 token 超限，强制做一次 Level 2，并校验压缩后 token 必须降到原值一定比例以下，`compressed_once` 限制只压一次防反复。

**裁剪关键参数**：

- `start_on="human"`（从 human 轮切，不切断 human→ai→tool 配对）；
- 自定义 tiktoken 计数（ChatOpenAI 对 qwen 代理不支持原生计数）；
- `include_system=True`（system prompt 永不裁剪）。

---

## 模块三：算法核心（工具系统与 SAM 分割）

### Q7. 一个 Agent 的工具系统怎么设计才能让 LLM 用对工具？本项目的白名单机制是什么？

**要点**：注册表 + 白名单 + 决策表，三重收窄决策空间。

**参考答案**（依据 `backend/model/tools/tool_registry.py` + `backend/agent/prompt/tool_catalog.py`）：

- **统一注册表**：`@register_tool("category")` + `@tool` 装饰器（注意顺序，`tool_registry.py:8-11`），全局 `_tool_registry` 按 category→name→func 组织，全仓 47 个工具 10 大类。
- **白名单收窄**：`_LLM_EXPOSED_TOOL_NAMES`（`tool_registry.py:27`）只有 33 个工具暴露给 LLM，`get_tools_for_llm()` 和 `get_tools_catalog_for_llm()` 用同一白名单，保证 prompt 目录和 bind_tools 一致。非白名单工具仅供内部调用。
- **决策表**（`tool_catalog.py:69` `build_decision_table`）：把"任务类型→必用工具/禁用工具/关键约束"做成 Markdown 表注入 prompt，把工具选择从"自由组合"收窄为"按任务查表"。
- **工具目录**（`tool_catalog.py:30`）：按 13 个分类分组，每工具只输出 `name + docstring 首行`，参数 schema 由 bind_tools 注入，不重复占 prompt token。

**设计哲学**：LLM 不擅长在海量工具里做正确组合决策，要用规则把决策空间预先收窄。

---

### Q8. 讲讲 SAM（Segment Anything）在本项目里的推理流程。什么是多轮迭代推理（Remote SAMsing）？

**要点**：开放词汇 + 双头融合 + 多轮场景简化。这是算法核心题。

**参考答案**（依据 `backend/model/SamSeg/runner.py` + `SamSeg/infer.py`）：

本项目用的是 **SegEarth-OV3 / SAM3 开放词汇分割模型**（非原版 SAM 的 points/box 提示，而是**文本提示 text prompt**）。

**单视图推理流程**（`infer.py:200` `inference_single_view`）：

1. 图像编码一次（`processor.set_image`）。
2. 对每个类别词（含同义词展开）逐项推理，得到实例头 logits + 语义头 logits + 存在性分数。
3. **双头融合**（`_merge_prompt_result`）：实例 logits × object_score 取 max、语义 logits 取 max、再乘 presence_score 过滤。
4. 输出 `seg_logits`（num_queries, H, W）。

**多轮迭代推理 Remote SAMsing**（`infer.py:330`）——解决单轮覆盖率不足的核心算法：

1. **场景简化**：每轮把已分割像素涂黑，让残差区域突出。
2. 单轮推理 → 同义词同类聚合（`aggregate_logits`）。
3. argmax + **自适应阈值衰减**：Pass1 用基础 prob，Pass2/3 按 0.7 倍递减（前期严格后期放宽，逐步兜底）。
4. 只填充未覆盖区域。
5. 收敛条件：覆盖率 ≥ 0.95 或新增 < 0.1%。

**后处理**：形态学平滑 → 连通域过滤去碎片 → 填洞 → 实例内部填充。

**变化检测**（`compute_instance_change_map`）：两期各自分割 → 提取连通域实例 → 算实例对 IoU → 贪心匹配（阈值 0.3）→ 未匹配判消失/新增，匹配但类别不同判类别变化。

---

### Q9. SAM 结果怎么知道可不可信？怎么避免"伪装成功"？

**要点**：verification 是独立客观证据，不信任工具的 summary。

**参考答案**（依据 `backend/model/tools/_verification.py`）：

**核心问题**：工具 try/except 吞异常后仍返回 success + 友好 summary，LLM 会误判完成（典型 0KB ZIP bug）。

**本项目的反伪装三层防御**：

1. **工具层 verification**：每个产文件工具自带校验（`verify_file_exists` 文件 ≥100B、`verify_zip_integrity` 解压验核心组件、`verify_vector_features` geopandas 读回验要素）。
2. **统一入口 `verify_by_path`**（`:309`）：按扩展名自动路由校验器。
3. **传输层兜底**（`observation_builder.py:18`）：工具没自带 verification 但产了文件时，主循环自动补 `verify_by_path`。

**降级策略**：基础校验（文件/ZIP）只依赖标准库永远可用；矢量/PDF 校验依赖 geopandas/PyPDF2，缺失时返回 `ok=True + deep_checked=False`（"未深度校验"而非报错），保持系统鲁棒。

**设计原则**："summary 是自述可能撒谎，verification 是客观证据"，prompt 硬规则要求 LLM 看 verification 不准只看 summary。

---

## 模块四：鲁棒性与自修复（本项目最大亮点）

### Q10. 【核心题】完整描述工具调用失败后的"验证→诊断→修复→重试"闭环。

**要点**：这是本项目最值钱的设计，确定性规则驱动，不是 LLM 自修复。

**参考答案**（横跨 `observation_builder.py` / `verification_worker.py` / `repair_policy.py` / `validation_stage.py` / `chat_service.py`）：

**① 触发**：每个工具结果回灌 LLM 前，强制经 `build_observation_tool_message`（`observation_builder.py:52`）：

- `_attach_path_verification`：工具没自带 verification 但产了文件 → 自动补客观校验。
- `apply_validation_to_tool_result`：跑 verification_agent 验证。

**② 诊断**（确定性，不调 LLM）：`verification_worker.run_verification`（`verification_worker.py:102`）三段校验链：

- `tool_result.type=="error"` → 直接 FAILED
- 读工具自带 verification，`ok==False` 收集为 issue
- 对产物路径调 verify_by_path
- 输出三态：`passed`（有证据全过）/ `failed`（有 error 或校验失败）/ `warning`（无证据不能判完成）

**③ 修复策略**（13 类查表，`repair_policy.py:115`）：按 `failure_type` 分派，例如：

- `missing_input_file` → 不重试同路径，请求用户提供（REPORT_ERROR）
- `empty_vector` → 回 segment_image，保持原类别加同义词（SWITCH_TOOL）
- `incomplete_shapefile_zip` → 重导或降级 GeoJSON
- `unknown_failure` → 至少改一个参数/工具/策略

**④ 强化注入**：`build_repair_execution_card` 把修复计划转"硬执行卡片"，标记 `latest_evidence_overrides`（新证据覆盖旧 summary）。`should_disable_tools_for_next_repair_turn` 在 `must_not_call_tool` 场景下**下一轮直接移除工具 schema 绑定**（用 `llm_without_tools`），物理阻止模型继续犯错。

**⑤ 防机械重复**（`retry_guard`）：首次失败只记录签名不拦截（允许一次修复），同签名再次出现才追加 SystemMessage 强制 replan。

**⑥ 完成门兜底**：即使 LLM 想收尾，`can_finalize_plan` 检查必需步骤未全 completed → 强制继续。

**设计哲学**：**确定性优先于 LLM**——验证、修复策略、failure 分类全是规则查表，避免修复本身成为新的不确定性源。只有"自纠教训沉淀"和"长期记忆提炼"用 LLM。

---

### Q11. 为什么本项目坚持 verification_agent 用确定性规则，而不是让 LLM 自己判断结果对不对？

**要点**：避免修复本身成为新的不确定性源 + 成本 + 可复现。

**参考答案**：

1. **不确定性传染**：如果用 LLM 判断工具结果，LLM 本身是概率生成器，可能误判成功（回到"伪装成功"问题），相当于用一个不可靠的裁判去纠正另一个不可靠的执行者。
2. **成本与延迟**：每次验证调一次 LLM，长任务几十次工具调用会放大成本和延迟。
3. **可复现性**：规则验证结果确定可复现，利于评测和回归；LLM 验证结果随温度/版本变化。
4. **可审计**：规则验证的判定逻辑可追溯（哪条规则、哪个文件没过），便于 debug。

**代价**：规则覆盖不全（只能验已知失败模式），无法处理语义层面的"结果对不对"。本项目用 `warning`（证据不足）状态做折中——宁可标记"不能确认完成"也不强行判通过。

---

## 模块五：工程化与系统设计

### Q12. 本项目的并发模型是怎样的？为什么用 WebSocket 而不是 SSE？

**要点**：asyncio + 多对话并行 + 双向通道需求。

**参考答案**（依据 `chat_service.py` + `ws_manager.py`）：

- **多对话并行**：同一 WebSocket 连接可同时跑多个对话，每对话一个 `asyncio.Task`，`ContextVar` 隔离 conv_id/user_id，下行消息带 `conversation_id` 路由。
- **同步/异步工具分流**：同步工具（DB/GeoServer/torch）走 `asyncio.to_thread` 不阻塞 event loop，异步工具（沙盒 docker）走 `await ainvoke`。
- **CancelledError 不上抛**：一个对话被停止不影响同 session 其他对话。
- **前端阻塞交互**：`frontend_action` + `wait_for_frontend_result` 用 `asyncio.Event` + `wait_for(120s)` 实现"后端发指令→前端执行→回传结果"。

**为什么 WebSocket 而非 SSE**（`architecture-agent.md:800`）：`frontend_action` 需要**双向闭环**——后端发渲染指令，前端执行后回传 tool_result。SSE 是单向通道做不到。`request_id` 配对精确唤醒并发的多个 await。

---

### Q13. 本项目有哪些可观测性能力？如何审计 LLM 是否真的遵循了修复计划？

**要点**：JSONL 审计 + DB 任务卡 + 决策审计，没有 OpenTelemetry。

**参考答案**（依据 `backend/agent/observability/`）：

本项目没有分布式追踪（OpenTelemetry/span/metric），而是自建三类可观测性：

**① 结构化审计追踪（JSONL 落盘）**：

- `decision_audit.py`：`audit_repair_plan_following` 把 repair_plan 与 LLM 真实下一步对比，判断 `followed` + `violation_type`（如 `repeated_forbidden_input` / `ignored_recommended_tool`），写 `logs/agent-runtime-audit.jsonl`。
- `parameter_repair.py`：参数级修正验证（必填参数是否补齐、expected_terms 是否体现）。
- `repair_success.py`：下一次工具结果看 `agent_validation.status==passed` 判定是否真修好，算修正成功率，写 `logs/agent-repair-success.jsonl`。

**② 任务执行追踪（DB）**：重工具自动开 `ai_task` 卡，记录脱敏输入、输出摘要、资源采集（`wall_clock_ms` / `inference_seconds`）、产物血缘（artifact→task→conversation）。

**③ 运行时日志**：缓存命中率（从 `usage_metadata.cache_read` 算）、`[RetryGuard]`/`[Memory]`/`[SelfCorrection]` 等前缀日志。

**审计 LLM 是否遵循修复计划**：`pending_repair_audit` 机制——本轮注入 repair_plan 后设审计标志，下一轮 LLM 实际调用对比 repair_plan 的 `recommended_tool`/`parameter_changes`，不符则记 violation_type。

---

### Q14. 【系统设计题】如何评测一个 Agent 的好坏？本项目用什么指标？

**要点**：不只是功能指标，还要有"行为决策指标"和"反幻觉指标"。

**参考答案**（依据 `docs/agent/evaluation/metrics.md`）：

本项目定义了 7 大类指标，核心亮点是**不只测"功能对不对"，更测"决策行为对不对"**：

**① 功能/验证类**（确定性测试集，`tests/agent_evaluation_dataset.py`）：

- `validation_rule_pass_rate`（验证规则覆盖率）
- `covered_failure_types`（覆盖多少种失败模式）
- `repair_success_rate`（修复成功率，带 score）

**② 决策行为类**（真实 LLM 跑，`agent_behavior_harness_eval.py`）：

- `agent_behavior_score`（行为得分，门槛 80）
- `llm_follow_rate`（LLM 遵循 repair_plan 的比例）
- **`false_success_rate`（伪装成功率，目标 0/10）** ← 核心：防止"假装完成"
- `same_failure_repeat_rate`（机械重复失败率）

**③ 四层能力指标**：

1. 任务规划能力
2. **抑制幻觉能力**（false_success）
3. 失败探索能力（方向性探索 vs 盲目重试比例）
4. 思维链质量（LLM-as-judge，用裁判模型 `gpt-5.6-terra` temp=0 评四维：务实/问题导向/简洁/第一性原理）

**核心理念**：Agent 的评测难点不在"功能"，而在"决策质量"——它会不会在失败时假装成功、会不会盲目重试、会不会遵循修复指令。这些必须用行为样本 + 真实 LLM 跑，不能只靠单元测试。

---

### Q15. 说说本项目你看到的工程取舍（trade-off），有哪些设计哲学值得借鉴？

**要点**：开放性题，考察对系统的整体理解。

**参考答案**（精选几条）：

1. **确定性优先于 LLM**：验证、修复策略、failure 分类全是规则查表。只有教训沉淀和记忆提炼用 LLM（且用免费额度模型）。哲学：**用 LLM 做决策，用规则做兜底**。

2. **无轮次上限 + 用户停止兜底**：复杂任务不被强制截断，安全出口靠用户停止。哲学：**信任用户判断而非硬编码截断**。代价是可能死循环（靠 retry_guard 拦截机械重复缓解）。

3. **降级而非报错**：`_verification` 依赖缺失返回"未深度校验"而非报错；`agent_db` 不可用时记忆降级到内存 dict。哲学：**可用性优先于一致性**，无 DB 时仍能运行只丢跨会话记忆。

4. **宁缺毋滥的教训学习**：自纠教训提炼失败/超时/缺字段一律跳过不入库，不降级到规则拼接。哲学：**保证记忆库纯净**，宁可没有也不要错的。

5. **不新增 DB 表的 plan 持久化**：plan 复用一条 `task_type='agent_plan'` 的 ai_task（首次写 input，后续写 output）。哲学：**降低 schema 复杂度**，不为每个新概念建新表。

6. **fire-and-forget 后台收尾**：done 事件先发降低首字节延迟，记忆摘要/自纠扫描后台跑。哲学：**用户感知优先**，不阻塞已完成对话做后台杂活。

7. **水线理论 / 最小混沌单元**（harness 方法）：在"边界"和"checkpoint"控制 Agent，允许边界内探索，越界则回炉。任务包"小到可检查，大到可自治"。哲学：**用约束收住 Agent 的混沌，而非用脚本固化流程**。

---

## 加分题（代码审阅）

### Q16. 探索发现 `chat_service.py` 引用了 `settings.llm_retry_max`、`settings.llm_retry_rate_limit_sleep`、`settings.llm_force_compress_target_ratio`，但在 `backend/config.py` 的 `Settings` dataclass 中未找到定义。这是什么问题？会怎样？

**参考答案**：这是一个**潜在的运行时缺陷**。`Settings` 是 `@dataclass`，这三个字段若无 `field(default=...)`，访问 `settings.llm_retry_max` 会抛 `AttributeError`。触发路径：LLM 首次调用失败进入 `except Exception` 分支读取 `settings.llm_retry_max` 时崩溃，导致重试子循环失效——本应分类重试的逻辑变成直接抛异常。

**修复**：在 `config.py` 的 `Settings` 中补定义：

```python
llm_retry_max: int = 3
llm_retry_rate_limit_sleep: float = 5.0
llm_force_compress_target_ratio: float = 0.85
```

> 这道题考察候选人的**代码审阅能力**和**对 dataclass 配置模式的理解**，也是真实从代码里发现的隐患，能区分"只会背概念"和"能落地 debug"的候选人。

---

## 附录：能力维度速查表

| 层次 | 题号 | 考察能力 |
|------|------|----------|
| 基础概念 | Q1–Q3 | 理解 Agent 本质、ReAct vs Function Calling、主循环 |
| 记忆上下文 | Q4–Q6 | 三层记忆、检索策略取舍、上下文压缩 |
| 算法核心 | Q7–Q9 | 工具系统设计、SAM 推理、结果验证 |
| 鲁棒性自修复 | Q10–Q11 | 验证-诊断-修复闭环、确定性优先哲学 |
| 工程化 | Q12–Q15 | 并发模型、可观测性、评测体系、系统 trade-off |
| 代码审阅 | Q16 | 缺陷定位与修复 |
