# Agent 架构实施规划

本文把后端 Agent 改造拆成最小混沌任务包。每个任务包都必须小到可检查、大到可自治, 并且包含目标、边界、输出、checkpoint、验收命令和停止条件。

## 1. 实施原则

- 主控 Agent 是唯一决策中心。
- worker 只执行受控子任务并结构化汇报。
- 不采用群体辩论式多 Agent。
- 每个任务包都必须有可验证输出。
- 优先稳定执行闭环, 再增加 worker 数量。
- 工具结果和验证结果优先于历史记忆。
- 记忆只沉淀有泛化价值的错误和成功修复路径。
- skill 只承接成熟、高频、稳定的流程。

## 2. 当前阶段判定

截至当前代码状态:

| 能力 | 状态 | 证据 |
|---|---|---|
| 主控 ReAct Agent | 已完成 | `backend\agent\chat_service.py` |
| verification_agent | 已完成第一版 | `backend\agent\team\verification_worker.py` |
| repair_policy | 已完成第二版 | `backend\agent\team\repair_policy.py` 覆盖 9 类 failure_type |
| ToolMessage Observation 回灌 | 已完成 | `backend\agent\runtime\observation_builder.py` |
| decision audit | 已完成第一版 | `backend\agent\observability\decision_audit.py` |
| retry_guard | 已完成第一版 | `backend\agent\runtime\retry_guard.py` |
| context_priority | 已完成第一版 | `backend\agent\context\context_priority.py` |
| repair_success_rate | 已完成第一版 | `backend\agent\observability\repair_success.py`、`tests\agent_repair_success_eval.py` |
| GeoServer E2E 报告评估 | 已完成第一版 | `tests\e2e_geoserver_report_eval.py` |
| worker 调度工具 | 已完成基础版 | `assign_agent_task` / `query_agent_task` / `cancel_agent_task` |
| report_agent | 已完成第一版 | `backend\agent\team\report_worker.py` + `tests\segment_auto_report_test.py` |
| 专业 worker | 部分完成 | 当前开放 `verification_agent` 和 `report_agent`, 尚未开放 `rs_agent` / `publish_agent` |
| 显式 plan state | 未完成 | 尚无 plan 状态模型 |

当前执行判断:

```text
阶段 1、阶段 2、阶段 3 已完成第一版。
阶段 4.1 repair_success_rate 已完成第一版。
阶段 5.1 report_agent 已完成第一版。
下一步不应继续主要优化 prompt。
下一步应进入 worker 汇报协议泛化、report_agent 任务卡化和真实修正成功样本扩展。
```

## 3. 总阶段路线

| 阶段 | 主题 | 目标状态 |
|---|---|---|
| 阶段 1 | 稳定主控 Agent + 验证 Agent 闭环 | 已完成第一版 |
| 阶段 2 | 上下文优先级、去重、冲突裁决 | 已完成基础护栏 |
| 阶段 3 | 行为观测与 decision audit | 已完成第一版 |
| 阶段 4 | repair_success_rate + worker 汇报协议 | repair_success_rate 已完成第一版, worker 汇报协议需要继续泛化 |
| 阶段 5 | 拆第一个专业 worker | `report_agent` 已完成第一版 |
| 阶段 6 | 显式 plan state | 主控计划、worker owner、证据、状态可审计 |
| 阶段 7 | 反思记忆结构化沉淀 | 错误模式、根因、修复动作进入长期 lesson |
| 阶段 8 | 技能选择与技能执行规范 | 高频遥感流程可由 skill 规范驱动 |
| 阶段 9 | 拆遥感、发布 worker | 主控 Agent 可分配多个专业 worker 完成子任务 |

## 4. 下一阶段执行计划

### 执行包 4.1: repair_success_rate

Status:

```text
已完成第一版。
```

Task:

```text
统计修正动作执行后的真实工具验收结果
```

Goal:

- 当前 `decision_audit` 已能判断“主控有没有遵循 repair_plan”。
- 本任务补上“遵循之后是否真的修好”的指标。
- 指标名固定为 `repair_success_rate`。

Allowed files:

```text
backend\agent\observability\
backend\agent\runtime\
backend\agent\chat_service.py
tests\
docs\agent-evaluation\
```

Outputs:

```text
repair_attempt_id
source_failure_type
repair_plan
actual_repair_action
next_tool_result
next_agent_validation.status
repair_success
```

Checkpoint:

- failed / warning 后的下一次工具结果能关联到上一条 repair_plan。
- 下一次 `agent_validation.status=passed` 时计为成功。
- 下一次仍 failed / warning 时计为失败或继续中。

Validation command:

```powershell
python tests\agent_repair_success_eval.py
python tests\agent_guardrail_eval.py
python tests\agent_behavior_harness_eval.py
```

Current baseline:

```text
repair_attempt_count = 3
repair_completed_count = 2
repair_success_rate = 1 / 2
repair_failed_rate = 1 / 2
repair_success_score = 50.0 / 100
```

Runtime trace:

```text
logs\agent-repair-success.jsonl
```

当前边界:

```text
第一版能统计 repair_plan 后下一次工具结果是否 passed, 但固定样本仍小。
后续应把遥感、发布、报告、下载等真实工具链样本接入该指标。
```

Stop condition:

```text
如果无法稳定关联 repair_plan 和下一次工具结果, 先只写 trace, 不写通过率。
```

### 执行包 4.2: worker 汇报协议泛化

Status:

```text
下一步优先执行。report_agent 已有第一版字段, 但还不是所有 worker 强制 schema。
```

Task:

```text
扩展 worker result contract, 固定专业 worker 汇报字段
```

Goal:

- 让所有 worker 都用统一结构向主控 Agent 汇报。
- 主控 Agent 不需要从自然语言中猜 worker 是否完成。
- 为 `report_agent`、`publish_agent`、`rs_agent` 拆分铺路。

Allowed files:

```text
backend\agent\team\result_contract.py
backend\agent\team\task_manager.py
backend\agent\team\agent_roles.py
tests\
```

Worker report schema:

```text
agent_role
task_id
status
summary
evidence
artifacts
metrics
issues
recommendation
repair_plan
next_action
handoff_notes
```

Checkpoint:

- `verification_agent` 输出兼容新 schema。
- `query_agent_task` 能返回完整 worker report。
- 缺字段时测试失败。

Validation command:

```powershell
python tests\worker_report_contract_test.py
python tests\agent_evaluation_dataset.py
```

Stop condition:

```text
如果 schema 变更破坏现有 verification_agent, 先保持向后兼容, 不继续拆专业 worker。
```

### 执行包 5.1: report_agent

Task:

```text
拆分第一个专业 worker: report_agent
```

Status:

```text
已完成第一版。
```

Goal:

- 让报告生成、报告产物检查、报告证据汇报从主控 Agent 中拆出。
- report_agent 只负责报告子任务, 不抢最终决策权。

为什么先拆 report_agent:

```text
报告任务边界清楚。
工具风险低于遥感推理和 GeoServer 发布。
当前评估集中已有 A6 证据不足报告场景。
适合验证 worker 协议是否可用。
```

Allowed files:

```text
backend\agent\team\
backend\agent\tools\team_tools.py
backend\model\tools\tool_registry.py
tests\
docs\agent-evaluation\
```

Role:

```text
REPORT_AGENT = "report_agent"
```

Tool whitelist:

```text
generate_monitor_report
visualize_vector
```

Inputs:

```text
task_type=segmentation_auto_report
image_path
classes
polygon_layer
base_layer
vector_stats
```

Outputs:

```text
worker_report
report_path
report_url
overlay_image_path
geojson_path
source_image_path
verification
evidence
issues
recommendation
```

Checkpoint:

- `assign_agent_task(agent_role="report_agent")` 可以创建任务。
- `report_agent` 只调用白名单工具或内部确定性流程。
- 产物必须经过 verification。
- worker 只汇报结果, 不直接对用户最终收尾。

Validation command:

```powershell
python tests\segment_auto_report_test.py
python tests\agent_guardrail_eval.py
```

真实分割端到端命令需要 `sam3` 环境和 GeoServer, 不作为默认快速验证:

```powershell
conda run -n sam3 python tests\e2e_segment_auto_report_eval.py
```

Stop condition:

```text
如果 report_agent 输出不能被主控 Agent 稳定读取, 先回到 worker 汇报协议泛化和任务卡化, 不继续拆 publish_agent。
```

### 执行包 6.1: 显式 plan state

Task:

```text
建立主控 Agent 的结构化计划状态
```

Goal:

- 让主控 Agent 的“任务 → 子任务 → worker → 汇报 → 决策”过程可审计。
- plan 不是群体辩论, 而是主控 Agent 的任务分解和执行账本。

Plan step schema:

```text
step_id
goal
owner
status
inputs
expected_outputs
evidence
repair_plan
worker_task_id
next_action
```

Allowed files:

```text
backend\agent\team\
backend\agent\memory\task_state.py
backend\agent\prompt\dynamic_context.py
tests\
```

Validation command:

```powershell
python tests\plan_state_test.py
```

Stop condition:

```text
如果 plan state 只被写入但不影响主控决策, 不继续扩展复杂计划字段。
```

## 5. 历史任务包

### 任务包 1.1: 稳定验证修正闭环

Task:

```text
稳定 verification_agent → repair_policy → ToolMessage 回灌链路
```

Goal:

- 确保 `agent_validation.failed/warning` 后主控 Agent 不直接收尾。
- 确保 ToolMessage 中包含 `repair_plan`。
- 确保通用失败能被映射成明确 failure_type。

Allowed files:

```text
backend\agent\team\
backend\agent\runtime\observation_builder.py
tests\verification_agent_stage_test.py
tests\chat_service_validation_integration_test.py
```

Forbidden files:

```text
frontend-vue\
backend\model\SamSeg\
backend\data\
```

Inputs:

- 工具原始返回。
- `verification` 字段。
- 产物路径。
- 当前用户目标。

Outputs:

- `agent_validation`。
- `repair_plan`。
- 增强后的 ToolMessage。

Checkpoint:

- failed / warning 不伪装成功。
- `repair_plan.next_action`、`recommended_tool`、`parameter_changes`、`fallback_tools` 进入 ToolMessage。
- `missing_input_file` 不再落到通用 `tool_error`。

Validation command:

```powershell
python tests\verification_agent_stage_test.py
python tests\chat_service_validation_integration_test.py
```

Stop condition:

```text
如果同一失败类型无法形成明确 next_action, 停止继续扩展 worker, 先补 repair_policy。
```

### 任务包 2.1: 上下文优先级表

Task:

```text
定义动态上下文优先级
```

Goal:

- 把当前用户输入、最新工具结果、agent_validation、任务状态、历史消息、摘要、长期记忆的优先级工程化。
- 让冲突裁决有统一依据。

Allowed files:

```text
backend\agent\context\
backend\agent\prompt\dynamic_context.py
tests\
```

Forbidden files:

```text
backend\model\tools\
frontend-vue\
```

Inputs:

- 当前用户输入。
- 最新 ToolMessage。
- `agent_validation`。
- 当前任务状态卡。
- 会话摘要。
- 长期记忆。

Outputs:

- `context_priority.py`。
- 明确优先级常量或规则函数。
- 对应单元测试。

Checkpoint:

- 最新 `agent_validation.failed` 优先于旧摘要中的成功描述。
- 当前用户明确要求优先于长期偏好。

Validation command:

```powershell
python tests\context_priority_test.py
```

Stop condition:

```text
如果无法从现有上下文中识别最新 ToolMessage, 先补上下文快照能力, 不继续做冲突裁决。
```

### 任务包 2.2: 上下文冲突裁决

Task:

```text
检测并输出上下文冲突裁决摘要
```

Goal:

- 检测摘要、长期记忆、任务状态和最新工具结果之间的冲突。
- 把裁决结果注入动态上下文, 供主控 Agent 明确读取。

Allowed files:

```text
backend\agent\context\
backend\agent\prompt\dynamic_context.py
backend\agent\memory\memory_context.py
tests\
```

Forbidden files:

```text
backend\model\SamSeg\
frontend-vue\
```

Inputs:

- context priority。
- 动态上下文来源。
- 最近工具观察结果。

Outputs:

- `conflict_resolver.py`。
- 冲突列表。
- `winner` / `loser` / `reason` 裁决摘要。

Checkpoint:

- 旧摘要说完成、最新 validation failed 时, 裁决结果必须选择最新 validation。
- 裁决结果进入 system prompt 动态段。

Validation command:

```powershell
python tests\context_conflict_resolver_test.py
```

Stop condition:

```text
如果裁决结果不能被稳定注入 dynamic_context, 不继续进入 decision audit。
```

### 任务包 3.1: Decision Audit Trace

Task:

```text
记录 repair_plan 与实际 tool_call 的符合情况
```

Goal:

- 观测主控 Agent 是否真的遵循 `repair_plan`。
- 让反思和记忆系统有行为证据。

Allowed files:

```text
backend\agent\observability\
backend\agent\runtime\
tests\
```

Forbidden files:

```text
frontend-vue\
backend\model\SamSeg\
```

Inputs:

- 上一条 `agent_validation`。
- 上一条 `repair_plan`。
- 本轮实际 `tool_call`。
- 本轮最终回复。

Outputs:

- `logs\agent-trace.jsonl`。
- `repair_plan_followed`。
- `violation_type`。
- `same_failure_repeat`。

Checkpoint:

- 能记录 expected next_action 和 actual tool。
- 能识别同一路径、同一工具、同一失败原因的重复调用。
- 能统计 failed/warning 后直接收尾次数。

Validation command:

```powershell
python tests\decision_audit_test.py
```

Stop condition:

```text
如果 audit 需要阻塞主流程才能实现, 停止并改为异步或 best-effort 记录。
```

### 任务包 3.2: Retry Guard

Task:

```text
增加工程层防重复失败签名
```

Goal:

- 防止同一工具、同一参数、同一 failure_type、同一错误信息机械重复。
- 与 `repair_plan.stop_condition` 配合。

Allowed files:

```text
backend\agent\runtime\
backend\agent\team\
tests\
```

Forbidden files:

```text
frontend-vue\
backend\data\
```

Inputs:

- tool_name。
- tool_args。
- failure_type。
- error message。
- repair_plan.stop_condition。

Outputs:

- `retry_guard.py`。
- retry signature。
- allow / block 判断结果。

Checkpoint:

- 同一缺失文件路径不得重复导入。
- 同一 error_type 连续出现两次且无参数变化时应触发停止条件。

Validation command:

```powershell
python tests\retry_guard_test.py
```

Stop condition:

```text
如果无法判断参数是否变化, 先只记录 audit, 不阻断执行。
```

### 任务包 4.1: Lesson Policy

Task:

```text
结构化反思记忆写入策略
```

Goal:

- 把有价值的错误修复经验沉淀为长期 lesson。
- 避免低价值聊天内容污染长期记忆。

Allowed files:

```text
backend\agent\memory\
tests\
```

Forbidden files:

```text
backend\model\tools\
frontend-vue\
```

Inputs:

- 失败工具名。
- failure_type。
- 错误消息。
- 错误动作。
- 成功修复动作。
- 最终验证结果。

Outputs:

```text
failure_signature
root_cause
wrong_action
successful_repair
future_rule
```

Checkpoint:

- 只有修复成功或高价值阻塞才写入 lesson。
- 同类 lesson 能去重。
- `build_user_profile_text` 注入的是规则性教训, 不是流水账。

Validation command:

```powershell
python tests\lesson_policy_test.py
```

Stop condition:

```text
如果无法证明修复动作有效, 不写长期记忆。
```

### 任务包 5.1: Skill Selector

Task:

```text
主控 Agent 的技能选择与技能执行规范
```

Goal:

- 让稳定高频流程升级为 skill。
- 主控 Agent 在任务开始或复杂任务中能主动检索并按 skill 执行。

Allowed files:

```text
backend\agent\skills\
backend\model\skills\
tests\
```

Forbidden files:

```text
frontend-vue\
backend\data\
```

Inputs:

- 用户目标。
- 当前任务类型。
- 历史 lesson。
- 可用 skill 列表。

Outputs:

- 选中的 skill。
- skill 执行步骤。
- 主控 Agent 逐步执行的 checkpoint。

Checkpoint:

- 遥感分割任务能匹配遥感分析 skill。
- skill 步骤不跳过输入检查和产物验证。
- skill 失败时仍回到 `repair_plan`。

Validation command:

```powershell
python tests\skill_selector_test.py
```

Stop condition:

```text
如果 skill 会绕过主控 Agent 或实时验证, 不接入执行链路。
```

### 任务包 6.1: rs_agent

Task:

```text
拆分遥感推理 worker
```

Goal:

- 让遥感分割、变化检测、类别词扩展、掩膜前置检查由 `rs_agent` 承担。

Allowed files:

```text
backend\agent\team\
backend\agent\tools\team_tools.py
backend\model\tools\tool_registry.py
tests\
```

Forbidden files:

```text
frontend-vue\
backend\data\
```

Inputs:

- 用户遥感目标。
- 影像路径或图层。
- 类别词。
- 当前任务状态。

Outputs:

- 遥感 worker 汇报。
- 产物路径。
- 掩膜或变化检测统计。
- 验证结果。

Checkpoint:

- worker 有明确输入输出契约。
- worker 有工具白名单。
- worker 结果必须经过验证。
- worker 完成后只向主控 Agent 汇报。

Validation command:

```powershell
python tests\rs_agent_test.py
```

Stop condition:

```text
如果 verification_agent 闭环和 decision audit 未稳定, 不继续拆 publish_agent / report_agent。
```

## 6. 统一验收

所有阶段必须保持以下测试通过:

```powershell
python tests\verification_agent_stage_test.py
python tests\chat_service_validation_integration_test.py
```

新增阶段必须补充对应测试。测试至少覆盖:

- 正常通过。
- 工具失败。
- 缺证据 warning。
- 修正策略写入。
- 主控 Agent 不应直接收尾的场景。

## 7. 风险与约束

- 动态上下文过多会导致模型忽略最新验证结果, 需要冲突裁决。
- 长期记忆过宽会污染 prompt, 只应注入高价值 lesson / workflow / envfact。
- worker 数量过早增加会让调试难度上升, 必须先有 observation 和 audit。
- skill 不应变成隐藏执行器, 必须仍由主控 Agent 按步骤执行。
- 工具结果必须保留客观验证字段, 不能只依赖 summary。

## 8. 默认决策

- 主控 Agent 保持唯一决策中心。
- 第一优先级继续稳定验证修正闭环。
- `verification_agent` 保持确定性规则实现, 不默认调用 LLM。
- `ai_task` 继续作为 worker 任务状态源。
- `ToolMessage` 继续作为主控 Agent 读取 worker / 工具结果的主要通道。
- 高价值错误先进入 lesson, 高频稳定流程再升级为 skill。
