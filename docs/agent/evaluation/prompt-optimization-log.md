# Agent Prompt 优化实验记录

本文记录为了提升真实行为评估 `llm_follow_rate` 所做的 prompt / context 实验。每次实验记录改动 diff 摘要、固定命令、量化结果和失败模式。

目标:

```text
在有限且会腐烂的上下文里, 让 Agent 始终基于最新证据做出可验证决策。
```

当前目标门槛:

```text
llm_follow_rate >= 8 / 10
false_success_rate <= 2 / 10
same_failure_repeat_rate <= 2 / 10
```

固定评估命令:

```powershell
python tests\agent_behavior_harness_eval.py
```

## Baseline

时间: 2026-07-07

改动: 无, 使用 10-case 行为评估基线。

结果:

| 指标 | 结果 |
|---|---:|
| behavior_case_count | 10 |
| llm_follow_rate | 2 / 10 |
| false_success_rate | 0 / 10 |
| same_failure_repeat_rate | 0 / 10 |

主要失败:

| 类型 | 现象 |
|---|---|
| `missing_expected_parameter_change` | 模型调用了推荐工具, 但没有体现参数修正 |
| `unexpected_tool_after_missing_input` | 输入文件不存在时, 模型查目录而不是请求有效文件 |
| `ignored_recommended_tool` | 模型改用摘要、导入或 shell, 没有按 recommended_tool / fallback_tools 执行 |

## Experiment 1: 强化 Repair Plan 执行协议

时间: 2026-07-07

改动摘要:

```text
backend\agent\prompt\static_template.py
- 提升最新 agent_validation / repair_plan 的优先级。
- 禁止 failed/warning 后调用 view_conversation_summary / view_user_memory 来确认完成。
- 增加 Repair Plan 执行协议, 按 failure_type 明确下一步动作。
- 强化 parameter_changes: 不允许只重复工具名而不修改参数。

backend\agent\prompt\dynamic_context.py
- 将“当前任务状态”移动到“会话摘要”之前。
- 目的: 让最新工作状态优先于可能腐烂的旧摘要。
```

结果:

| 指标 | 结果 |
|---|---:|
| behavior_case_count | 10 |
| llm_follow_rate | 6 / 10 |
| false_success_rate | 0 / 10 |
| same_failure_repeat_rate | 0 / 10 |

结论:

```text
有效提升。A1/A3/A5 等 recommended_tool / parameter_changes 场景明显改善。
剩余失败集中在 missing_input_file 场景继续调用 shell, 以及部分 fallback 选择不稳定。
```

## Experiment 2: 禁止缺失文件场景继续 Shell 探测

时间: 2026-07-07

改动摘要:

```text
backend\agent\prompt\static_template.py
- 强化 missing_input_file: 没有新的有效路径或上传文件时必须停止工具调用并请求用户输入。
- 明确目录查询、ls、find、shell 检查不能修复“文件不存在”。
- 明确 run_shell_command 只有出现在 recommended_tool 或 fallback_tools 时才能调用。
```

结果:

| 指标 | 结果 |
|---|---:|
| behavior_case_count | 10 |
| llm_follow_rate | 3 / 10 |
| false_success_rate | 0 / 10 |
| same_failure_repeat_rate | 0 / 10 |

结论:

```text
该改动退化。全局禁止 run_shell_command 干扰了原本能通过的补证据和诊断场景。
后续回退该全局禁令, 改为只在当前失败 ToolMessage 中给出局部约束。
```

## Experiment 3: ToolMessage 局部执行卡片

时间: 2026-07-07

改动摘要:

```text
backend\agent\prompt\static_template.py
- 回退 Experiment 2 过强的全局 run_shell_command 禁令。

backend\agent\team\validation_stage.py
- 新增 build_repair_execution_card。
- 在 ToolMessage 中写入 repair_execution_card。
- 将 repair_plan 压缩成局部最高优先级执行卡片, 包含 allowed_tools、argument_requirements、must_not_call_tool 等字段。
```

结果:

| 指标 | 结果 |
|---|---:|
| behavior_case_count | 10 |
| llm_follow_rate | 6 / 10 |
| false_success_rate | 0 / 10 |
| same_failure_repeat_rate | 0 / 10 |

结论:

```text
恢复到 6 / 10, 但没有超过 Experiment 1。
ToolMessage 内部字段仍可能被模型漏读。
```

## Experiment 4: Failed/Warning 后追加即时 SystemMessage

时间: 2026-07-07

改动摘要:

```text
backend\agent\runtime\observation_builder.py
- 新增 build_repair_followup_system_message。
- 当 agent_validation 为 failed/warning 时, 生成紧跟 ToolMessage 的短 SystemMessage。
- missing_input_file 场景明确禁止工具调用, 要求请求有效路径或上传文件。
- 其他场景明确 allowed_tools 和 argument_requirements。

backend\agent\chat_service.py
- 主循环在 ToolMessage 后追加 repair follow-up SystemMessage。

tests\agent_behavior_harness_eval.py
- 行为评估使用同一 follow-up SystemMessage, 与真实主循环保持一致。
```

结果:

| 指标 | 结果 |
|---|---:|
| behavior_case_count | 10 |
| llm_follow_rate | 6 / 10 |
| false_success_rate | 0 / 10 |
| same_failure_repeat_rate | 0 / 10 |

结论:

```text
即时 SystemMessage 没有超过 6 / 10。
模型仍会在 must_not_call_tool 场景调用工具, 说明只靠文字禁止不足以控制 function calling。
```

## Experiment 5: Must-Not-Call 场景移除下一轮工具 Schema

时间: 2026-07-07

改动摘要:

```text
backend\agent\runtime\observation_builder.py
- 新增 should_disable_tools_for_next_repair_turn。
- 当 repair_execution_card.must_not_call_tool=true 时, 标记下一轮应移除工具 schema。

backend\agent\chat_service.py
- 主循环同时持有带工具 LLM 和无工具 LLM。
- must_not_call_tool 场景下一轮使用无工具 LLM, 防止继续调用目录查询或缺失路径工具。

tests\agent_behavior_harness_eval.py
- 行为评估按同一规则在 must_not_call_tool 样本中使用无工具 LLM。
```

结果:

| 指标 | 结果 |
|---|---:|
| behavior_case_count | 10 |
| agent_behavior_score | 96.67 / 100 |
| llm_follow_rate | 9 / 10 |
| false_success_rate | 0 / 10 |
| no_false_success_rate | 10 / 10 |
| same_failure_repeat_rate | 0 / 10 |
| no_same_failure_repeat_rate | 10 / 10 |

通过样本:

```text
A2, A3, A4, A5, A6, A7, A8, A9, A10
```

剩余失败:

| 编号 | 失败类型 | 现象 |
|---|---|---|
| A1 | `missing_expected_parameter_change` | 参数非法时调用了 `segment_image`, 但参数表达未被审计规则认定为体现修正 |

结论:

```text
达到当前 9 / 10 行为评估目标。must_not_call_tool 场景移除工具 schema 是关键收益点。
这说明在 function calling 架构里, “从动作空间移除错误动作”比单纯在 prompt 中禁止更稳定。
综合行为分为 96.67 / 100, 高于 80 分通过线。
```

## Experiment 6: Failure-Type Driven Decision Audit

时间: 2026-07-08

改动摘要:

```text
backend\agent\team\repair_policy.py
- 新增 missing_sandbox_file, 将沙盒源文件缺失从普通 missing_input_file 拆出。
- 新增 missing_vector_artifact, 将 GeoJSON 发布 / 可视化产物缺失从普通本地输入缺失拆出。
- unreadable_artifact 允许 import_file_to_sandbox 作为格式检查前置动作。

backend\agent\observability\parameter_repair.py
- 参数级修正验证改为按 failure_type 判断。
- 建筑物目标支持“建筑 / 建筑物 / building / 房屋”等同类语义。
- Shapefile 缺组件允许重导工具隐式生成核心组件。
- insufficient_evidence 允许 shell / python 检查报告、路径、PDF、产物证据。

tests\agent_evaluation_dataset.py
- 固定规则样本扩展到 11 条。
- Observation 回灌样本扩展到 5 条。
- covered_failure_types 扩展到 9 类。

tests\agent_behavior_harness_eval.py
- A3 补足原始影像路径, 使空矢量样本真正评估“回到分割阶段”。
```

结果:

| 指标 | 结果 |
|---|---:|
| validation_rule_pass_rate | 11 / 11 |
| observation_injection_pass_rate | 5 / 5 |
| prompt_contract_pass_rate | 6 / 6 |
| covered_failure_types | 9 |
| guardrail_pass_rate | 10 / 10 |
| agent_behavior_score | 100.0 / 100 |
| llm_follow_rate | 10 / 10 |
| false_success_rate | 0 / 10 |
| same_failure_repeat_rate | 0 / 10 |

结论:

```text
继续堆 prompt 的收益已经下降。
本轮主要收益来自把“下一步怎么改”变成 failure_type 驱动的 repair_plan 和参数级 decision_audit。
当前 10 条真实 LLM 行为评估全部通过, 达到阶段性最优基线。
```
