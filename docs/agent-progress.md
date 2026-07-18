# Agent 架构进度

本文记录 Agent 架构阶段状态和验收证据。项目总体进度仍以 `docs\PROGRESS.md` 为准。

## 1. 当前结论

阶段 1–8 已完成。系统保持单一主控决策中心，不再继续拆分专业 worker，也不把同步报告生成改造成任务卡。

当前运行闭环：

```text
主控 Agent
→ plan state / skill checkpoint
→ 工具执行
→ observation_builder
→ verification_agent
→ passed: 推进计划
→ failed 或 warning: repair_plan 修正
→ completion gate
→ 用户回复与 accepted lesson 沉淀
```

## 2. 阶段状态

| 阶段 | 目标 | 状态 | 完成结果 |
|---|---|---|---|
| 1 | 验证与修正闭环 | 已完成 | `agent_validation`、`repair_plan`、ToolMessage 回灌 |
| 2 | 上下文优先级与重复失败防护 | 已完成 | `context_priority`、`retry_guard` |
| 3 | 行为观测 | 已完成 | decision audit、参数修正追踪 |
| 4 | 修复成功率评估 | 已完成第一版 | repair success 日志与固定评估集 |
| 5 | 同步报告链路收口 | 已完成 | `report_agent` 同步生成、验证并回传 PNG/PDF 证据 |
| 6 | 显式 plan state | 已完成 | 计划持久化、状态迁移、恢复与完成门 |
| 7 | 结构化反思记忆 | 已完成 | 基于修复证据的 `lesson_policy` |
| 8 | 技能选择与执行规范 | 已完成 | 技能契约、单技能选择、计划检查点和失败回退 |

## 3. 阶段 6–8 证据

| 阶段 | 核心实现 | 测试 | Playwright 证据 |
|---|---|---|---|
| 6 | `backend\agent\memory\task_state.py` | `tests\plan_state_test.py` | `output\playwright\phase6-plan-state.png` |
| 7 | `backend\agent\memory\lesson_policy.py` | `tests\lesson_policy_test.py`、`tests\self_correction_test.py` | `output\playwright\phase7-lesson-policy.png` |
| 8 | `backend\model\skills\selector.py`、`backend\agent\runtime\skill_execution.py` | `tests\skill_selector_test.py` | `output\playwright\phase8-skill-contracts.png` |

阶段 6：

- 计划复用 `ai_task.input/output` 保存初始值和最新快照。
- 工具执行前建立或激活步骤，执行后按验证结果更新状态和证据。
- 任一必需步骤未通过验证时，`completion_gate.allowed=false`。

阶段 7：

- `repair_success=true` 且 verification 为 `passed` 才能标记 `accepted`。
- `review` 和 `rejected` 只写审计日志，不注入主控上下文。
- lesson 内容清洗路径信息，并按失败签名与证据强度去重。

阶段 8：

- 三个技能均具备 v1.0 contract。
- 选择器校验字段、首步输入、末步验证和工具白名单。
- 缺少必填输入时不选择；满足条件时最多选择一个主技能。
- 技能步骤进入 plan state，失败后继续走 verification / `repair_plan`。

## 4. 验证结果

专项测试均已通过：

```powershell
python tests\plan_state_test.py
python tests\lesson_policy_test.py
python tests\skill_selector_test.py
python tests\self_correction_test.py
python tests\verification_agent_stage_test.py
python tests\chat_service_validation_integration_test.py
```

固定评估结果：

| 评估 | 结果 |
|---|---:|
| `agent_guardrail_eval.py` | 10 / 10 |
| `agent_behavior_harness_eval.py` | 10 / 10 |
| false success | 0 / 10 |
| `agent_repair_success_eval.py` | 10 / 13（76.92%） |

阶段 5 报告验证：

```powershell
python tests\segment_auto_report_test.py
python tests\monitor_report_pdf_test.py
```

## 5. 固定边界

- 保留 `verification_agent` 与同步 `report_agent`，不再新增专业 worker。
- 保留分割工具内的同步报告调用，不实现派发、查询式报告任务卡。
- 主控 Agent 仍是唯一计划维护者、技能执行决策者和最终回复者。
- 新增 lesson 或 skill 时必须复用现有准入门和契约质量门。

## 6. 剩余风险

- 当前行为和修复成功率主要来自固定样本，真实长任务样本仍有限。
- 上下文冲突裁决尚未覆盖所有动态来源。
- verification 以确定性规则为主，复杂语义质量仍需通过新增样本扩展现有规则。
- 新技能增加后需持续校验工具白名单、必填输入和末步验证规则。

以上属于后续质量扩展，不影响阶段 1–8 的完成结论。
