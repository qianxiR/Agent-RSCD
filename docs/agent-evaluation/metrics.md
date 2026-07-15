# Agent 评估指标字典

本文定义 Agent 架构评估指标。所有指标都必须说明计算方式、证据来源和适用阶段。没有证据来源的指标只能标记为“未建立”。

## 1. 第一阶段已建立指标

| 指标 | 含义 | 计算方式 | 证据来源 | 当前值 |
|---|---|---|---|---|
| `validation_rule_pass_rate` | 验证规则是否按预期通过 | 通过验证规则用例数 / 验证规则用例总数 | `tests\agent_evaluation_dataset.py` | 11 / 11 |
| `observation_injection_pass_rate` | ToolMessage 是否包含验证和修正策略 | 通过观察回灌用例数 / 观察回灌用例总数 | `tests\agent_evaluation_dataset.py` | 5 / 5 |
| `prompt_contract_pass_rate` | 系统提示词是否保留验证修正闭环硬规则 | 通过 prompt 契约检查数 / prompt 契约检查总数 | `tests\agent_evaluation_dataset.py` | 6 / 6 |
| `covered_failure_types` | repair_policy 当前固定评估覆盖的失败类型数量 | distinct `failure_type` 数量 | `tests\agent_evaluation_dataset.py` | 9 |
| `repair_success_rate` | 修正策略是否最终修复问题 | 修正后 `agent_validation.status=passed` 次数 / 已验证修正次数 | `tests\agent_repair_success_eval.py`、`logs\agent-repair-success.jsonl` | 1 / 2 |
| `repair_success_score` | 修正成功率百分制分数 | `repair_success_rate * 100` | `tests\agent_repair_success_eval.py` | 50.0 / 100 |
| `geoserver_layers_read_rate` | GeoServer 指定图层是否可读取 | 成功读取图层数 / 请求图层数 | `tests\e2e_geoserver_report_eval.py` | 2 / 2 |
| `overlay_image_generated` | 报告是否包含原图叠加矢量 PNG 证据 | 叠加 PNG 已生成且传入报告结果 | `tests\e2e_geoserver_report_eval.py` | true |
| `e2e_report_verification_ok` | 端到端报告产物是否通过存在性和大小校验 | 报告工具 verification.ok | `tests\e2e_geoserver_report_eval.py` | true |
| `segment_auto_report_status` | 真实分割后是否自动触发 report_agent 并生成报告 | `auto_report.status == success` | `tests\e2e_segment_auto_report_eval.py` | 未建立固定基线 |

## 2. 决策行为指标

| 指标 | 含义 | 计算方式 | 必要证据 | 建立条件 |
|---|---|---|---|---|
| `llm_follow_rate` | 主控 Agent 是否按 `repair_plan` 采取下一步动作 | 遵循 repair_plan 的轮次 / 存在 failed 或 warning validation 的轮次 | `agent-trace.jsonl` 中 expected / actual 对比 | `decision_audit` 落地 |
| `false_success_rate` | 工具失败或证据不足后, Agent 是否错误收尾 | failed/warning 后直接最终成功回复次数 / failed/warning 总次数 | ToolMessage、final response、validation status | `decision_audit` 落地 |
| `same_failure_repeat_rate` | 同一失败是否机械重复 | 重复失败签名次数 / failed tool_call 总次数 | tool_name、tool_args、failure_type、error message | `retry_guard` 或 trace 落地 |
当前行为评估固定命令:

```powershell
python tests\agent_behavior_harness_eval.py
```

当前命令建立 `llm_follow_rate`、`false_success_rate`、`same_failure_repeat_rate` 的真实 LLM 观测值。
`repair_success_rate` 已由独立确定性命令建立第一版, 运行态 trace 写入 `logs\agent-repair-success.jsonl`。

当前行为基线:

| 指标 | 当前值 | 说明 |
|---|---:|---|
| `behavior_case_count` | 10 | 覆盖遥感、沙盒、发布、报告、下载、矢量和 GeoServer 工具族 |
| `agent_behavior_score` | 100.0 / 100 | `llm_follow_rate`、`no_false_success_rate`、`no_same_failure_repeat_rate` 三项平均分 |
| `agent_behavior_score_threshold` | 80 / 100 | 当前行为评估通过门槛 |
| `llm_follow_rate` | 10 / 10 | 10 个真实行为样本遵循 repair_plan |
| `false_success_rate` | 0 / 10 | 没有 failed 后直接宣告成功 |
| `no_false_success_rate` | 10 / 10 | failed 后均未直接宣告成功 |
| `same_failure_repeat_rate` | 0 / 10 | 没有完全重复同一失败参数 |
| `no_same_failure_repeat_rate` | 10 / 10 | 均未完全重复同一失败参数 |

## 3. 上下文指标

| 指标 | 含义 | 计算方式 | 必要证据 | 建立条件 |
|---|---|---|---|---|
| `context_conflict_resolution_accuracy` | 上下文冲突时是否选择正确信息源 | 正确裁决冲突数 / 冲突场景总数 | conflict list、winner、reason | `conflict_resolver` 落地 |
| `fresh_evidence_priority_rate` | 最新工具证据是否压过旧摘要和记忆 | 最新证据获胜次数 / 有冲突的最新证据场景数 | context snapshot、裁决结果 | `context_priority` 落地 |
| `memory_override_error_rate` | 长期记忆是否错误覆盖当前事实 | 记忆错误获胜次数 / 记忆与工具结果冲突次数 | context snapshot、final decision | `conflict_resolver` 和 audit 落地 |

## 4. 反思与记忆指标

| 指标 | 含义 | 计算方式 | 必要证据 | 建立条件 |
|---|---|---|---|---|
| `lesson_precision` | 写入长期记忆的 lesson 是否有泛化价值 | 有效 lesson 数 / 写入 lesson 总数 | lesson payload、触发上下文、修复证据 | `lesson_policy` 落地 |
| `lesson_dedup_rate` | 同类 lesson 是否去重 | 去重命中数 / 同类 lesson 候选数 | failure_signature、existing lesson | `lesson_policy` 落地 |
| `learned_error_avoidance_rate` | 已学习错误是否减少复发 | 被 lesson 阻止的重复错误数 / lesson 相关错误候选数 | lesson 命中、实际动作、trace | `decision_audit` + `lesson_policy` 落地 |

## 5. 技能指标

| 指标 | 含义 | 计算方式 | 必要证据 | 建立条件 |
|---|---|---|---|---|
| `skill_selection_accuracy` | 主控 Agent 是否选择了正确 skill | 正确 skill 选择次数 / skill 候选任务数 | 用户目标、候选 skill、选择结果 | `skill_selector` 落地 |
| `skill_checkpoint_pass_rate` | skill 步骤是否按 checkpoint 执行 | 通过 checkpoint 数 / 应执行 checkpoint 数 | skill trace、工具结果、验证结果 | skill 执行 trace 落地 |
| `skill_fallback_success_rate` | skill 失败后是否回到 repair_plan | 成功回退次数 / skill 失败次数 | skill failure、repair_plan、后续动作 | skill 执行 trace 落地 |

## 6. worker 指标

| 指标 | 含义 | 计算方式 | 必要证据 | 建立条件 |
|---|---|---|---|---|
| `worker_report_completeness` | worker 汇报是否包含必要字段 | 完整汇报数 / worker 汇报总数 | worker result contract | worker 汇报协议完善 |
| `worker_tool_scope_violation_rate` | worker 是否越权调用工具 | 越权调用次数 / worker tool_call 总次数 | worker role、tool whitelist、tool_call | worker 工具白名单落地 |
| `plan_step_completion_rate` | 主控 plan 中子任务是否完成 | 完成 plan step 数 / plan step 总数 | plan state、worker reports、validation | 显式 plan state 落地 |

当前 report_agent 快速验证命令:

```powershell
python tests\segment_auto_report_test.py
```

当前真实分割 E2E 命令:

```powershell
conda run -n sam3 python tests\e2e_segment_auto_report_eval.py
```
