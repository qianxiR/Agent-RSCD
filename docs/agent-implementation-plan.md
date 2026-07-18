# Agent 架构实施记录

本文保留阶段 1–8 的实施结论、阶段 6–8 的关键设计和统一验收方式。已取消方案和历史任务拆解不再作为执行依据。

## 1. 实施原则

- 主控 Agent 是唯一决策中心，worker 只返回受控结果。
- 当前工具事实高于历史消息、长期记忆和技能建议。
- 所有完成结论必须具备工具产物或 verification 证据。
- 失败先进入 `repair_plan`，禁止机械重试和伪装成功。
- 复用现有 `ai_task`、ToolMessage、长期记忆和技能目录，不建立平行状态系统。
- 报告链路保持同步，不再扩展 worker 数量或任务卡协议。

## 2. 阶段总览

| 阶段 | 实施内容 | 状态 |
|---|---|---|
| 1 | verification、repair_policy、结果回灌 | 已完成 |
| 2 | 上下文优先级、冲突裁决、重复失败防护 | 已完成 |
| 3 | decision audit 与参数修正追踪 | 已完成 |
| 4 | repair success 评估 | 已完成第一版 |
| 5 | 同步 `report_agent` 报告生成和 PDF 质量收口 | 已完成 |
| 6 | 显式 plan state | 已完成 |
| 7 | 结构化 `lesson_policy` | 已完成 |
| 8 | 技能选择与执行规范 | 已完成 |

## 3. 阶段 5 边界

阶段 5 只保留以下结果：

- `segment_image` 完成后同步调用 `report_agent`。
- `report_agent` 回读 GeoServer 图层并生成叠加 PNG 与自然资源监测 PDF。
- 报告产物经验证后以结构化结果回传主控。

明确不实施：

- 不新增 `rs_agent`、`publish_agent` 或其他专业 worker。
- 不把报告生成改为 `assign_agent_task` / `query_agent_task` 任务卡流程。
- 不扩展 worker 递归调度或多 Agent 辩论机制。

## 4. 阶段 6：显式 Plan State

### 目标

让工具型任务的计划可持久化、可恢复、可审计，并实际限制主控收尾。

### 实现

`backend\agent\memory\task_state.py` 提供统一状态操作：

| 操作 | 作用 |
|---|---|
| `create_plan_state` | 建立计划身份、目标和默认关闭的完成门 |
| `append_plan_step` | 声明步骤目标、执行者、输入和预期产物 |
| `start_tool_step` | 在真实工具执行前激活或补建步骤 |
| `apply_step_observation` | 根据验证结果更新状态、证据和修复计划 |
| `can_finalize_plan` | 确定性判断主控是否可以收尾 |
| `persist_plan_state` / `load_plan_state` | 使用 `ai_task.input/output` 保存与恢复快照 |

状态规则：

```text
pending → running → completed
                  ↘ blocked → 修复后重新执行
                  ↘ failed  → 补充验证或修复
```

- `passed`：步骤进入 `completed`。
- `failed` 或 `warning`：步骤进入 `blocked` 并保存 `repair_plan`。
- 缺少验证：步骤进入 `failed`。
- 所有必需步骤通过后，完成门才允许最终回复。

### 验收

```powershell
python tests\plan_state_test.py
```

Playwright 证据：`output\playwright\phase6-plan-state.png`。

## 5. 阶段 7：Lesson Policy

### 目标

把“失败后的文字总结”改为有证据门槛、可去重、可审计的结构化教训。

### 实现

`backend\agent\memory\lesson_policy.py` 负责：

1. 根据 failure type、工具和参数生成失败签名。
2. 清洗路径与冗余文本，构造 lesson contract。
3. 根据修复成功和验证证据判定状态。
4. 将所有判定写入 `logs\lesson-policy.jsonl`。
5. 只允许 `accepted` lesson 进入长期记忆和 prompt。

准入规则：

| 条件 | 状态 |
|---|---|
| `repair_success=true` 且 verification=`passed` | `accepted` |
| 高严重度但无成功修复证据 | `review` |
| 字段缺失、修复失败或未验证 | `rejected` |

同一 `dedup_key` 只保留证据更强的 accepted lesson。`review` 和 `rejected` 保留审计，但不参与后续决策。

### 验收

```powershell
python tests\lesson_policy_test.py
python tests\self_correction_test.py
```

Playwright 证据：`output\playwright\phase7-lesson-policy.png`。

## 6. 阶段 8：技能选择与执行规范

### 目标

使技能具备稳定契约、确定性选择、计划检查点和验证失败回退。

### 实现

每个技能必须声明九项 contract 字段：

```text
version
trigger_conditions
required_inputs
steps
tool_allowlist
expected_outputs
verification_rules
fallback_rule
promotion_evidence
```

`backend\model\skills\selector.py` 的选择流程：

```text
检索候选技能
→ 校验契约字段、首步输入、末步验证和工具白名单
→ 过滤缺少 required_inputs 的候选
→ 按关键词、accepted lesson 和当前计划稳定排序
→ 最多选择一个主技能
```

`backend\agent\runtime\skill_execution.py` 将技能步骤写入阶段 6 的 plan state，不直接执行工具。主控逐步执行真实工具，每一步继续经过 verification；失败时按技能 `fallback_rule` 和实际 `repair_plan` 修正。

当前技能：

| 文件 | 版本 | 主流程 |
|---|---|---|
| `backend\model\skills\wf1-segment-visualize-report.md` | v1.0 | 分割、展示、报告 |
| `backend\model\skills\wf2-change-detection-visualize-report.md` | v1.0 | 变化检测、展示、报告 |
| `backend\model\skills\wf4-upload-geoserver-display.md` | v1.0 | 上传、发布、展示 |

### 验收

```powershell
python tests\skill_selector_test.py
```

Playwright 证据：`output\playwright\phase8-skill-contracts.png`。

## 7. 统一后端验证

核心闭环测试：

```powershell
python tests\verification_agent_stage_test.py
python tests\chat_service_validation_integration_test.py
python tests\plan_state_test.py
python tests\lesson_policy_test.py
python tests\skill_selector_test.py
python tests\self_correction_test.py
```

报告链路测试：

```powershell
python tests\segment_auto_report_test.py
python tests\monitor_report_pdf_test.py
```

评估脚本：

```powershell
python tests\agent_guardrail_eval.py
python tests\agent_behavior_harness_eval.py
python tests\agent_repair_success_eval.py
```

当前结果：guardrail 10/10，behavior 10/10，false success 0/10，repair success 10/13（76.92%）。

## 8. 后续变更规则

- 扩展真实长任务和失败样本时，只补充测试与现有规则，不建立第二套运行时。
- 新 lesson 必须复用 `lesson_policy` 的证据准入和审计格式。
- 新技能必须通过 contract 质量门、必填输入门和工具白名单校验。
- 新步骤必须进入 plan state，并由 completion gate 约束最终回复。
- 报告格式优化继续在现有同步报告链路内完成。

阶段 1–8 至此收口。
