# Agent 目标与边界

本文定义后端 Agent 架构的当前目标、固定边界和验收标准。实施进度见 `docs\agent-progress.md`，实现细节见 `docs\agent-implementation-plan.md`。

## 1. 总目标

构建面向自然资源遥感监测的可验证执行系统，使主控 Agent 能在长任务中持续掌握目标、计划、证据和下一步动作，并在失败后修正而不是伪装成功。

```text
用户目标
→ 主控 Agent 建立显式计划
→ 选择技能或直接调用工具
→ verification_agent 验证工具结果
→ 通过则推进计划，失败则执行 repair_plan
→ 完成门通过后回复用户
→ 成功修复经验按 lesson_policy 沉淀
```

系统的核心要求只有三项：

| 要求 | 判断标准 |
|---|---|
| 状态连续 | 当前目标、步骤、证据和阻塞点可恢复 |
| 结果可信 | 完成结论由工具结果与 verification 支撑 |
| 行为受控 | 计划、教训和技能均有确定性契约与边界 |

## 2. 当前架构边界

| 决策 | 当前结论 |
|---|---|
| 决策中心 | 主控 Agent 是唯一决策中心和唯一用户可见回复者 |
| 多智能体范围 | 保留 `verification_agent` 和分割链路内同步调用的 `report_agent` |
| worker 扩展 | 不再拆分 `rs_agent`、`publish_agent` 或其他专业 worker |
| 报告执行方式 | 不将同步报告生成改造成主控显式派发、查询的任务卡 |
| 计划状态 | 复用 `ai_task.input/output` 持久化，不新增任务状态表 |
| 验证方式 | 第一层使用确定性规则，复杂场景可在现有边界内扩展 |
| 反思记忆 | 只有成功修复且验证通过的 lesson 可以进入长期记忆上下文 |
| 技能执行 | 技能只提供受控步骤，不能绕过主控计划或直接执行隐藏动作 |

## 3. 显式计划契约

每个工具型任务维护一个 plan state：

| 字段 | 含义 |
|---|---|
| `plan_id` / `goal` | 计划身份与当前用户目标 |
| `steps` | 步骤目标、执行者、输入、预期产物、状态和证据 |
| `current_step_id` | 当前执行位置 |
| `completion_gate` | 是否允许向用户宣告完成及其原因 |
| `selected_skill` | 当前选中的技能名称与版本 |

必需步骤只有获得 `agent_validation.status=passed` 才能进入 `completed`。`failed` 或 `warning` 必须记录 `repair_plan` 并关闭完成门。普通问答没有工具计划时可直接回复。

## 4. Lesson 准入契约

结构化 lesson 至少包含失败签名、根因、成功修复动作、未来规则、验证信号、证据和状态。

| 状态 | 准入条件 | 使用方式 |
|---|---|---|
| `accepted` | `repair_success=true` 且下一次 verification 为 `passed` | 可进入长期记忆和技能选择上下文 |
| `review` | 高严重度问题但尚无成功修复证据 | 仅保留审计 |
| `rejected` | 修复失败、证据不足或字段缺失 | 仅保留审计 |

长期记忆不能覆盖当前工具事实；相同失败模式按稳定去重键和证据强度处理。

## 5. 技能契约

稳定技能必须声明：

- `version`
- `trigger_conditions`
- `required_inputs`
- `steps`
- `tool_allowlist`
- `expected_outputs`
- `verification_rules`
- `fallback_rule`
- `promotion_evidence`

选择器执行契约质量门、必填输入门和确定性排序，每次最多选择一个主技能。技能步骤写入显式计划后仍由主控逐步调用真实工具；任何步骤失败都返回 verification 与 `repair_plan` 闭环。

当前 v1.0 技能：

| 技能 | 用途 |
|---|---|
| `wf1-segment-visualize-report` | 语义分割、可视化与报告 |
| `wf2-change-detection-visualize-report` | 变化检测、可视化与报告 |
| `wf4-upload-geoserver-display` | GeoServer 上传、发布与展示 |

## 6. 信息优先级

```text
当前用户要求
> 最新工具结果与 verification
> 当前 repair_plan
> 显式 plan state
> 近期消息与会话摘要
> accepted lesson 与 skill
```

发生冲突时始终以较新的客观证据为准。

## 7. 非目标

- 不做开放式 Agent 群聊、投票或辩论。
- 不允许 worker 直接回复用户或无限递归分配任务。
- 不允许失败结果、缺失产物或未验证结果通过完成门。
- 不允许同一工具、同一参数、同一失败原因机械重试。
- 不允许 lesson 或 skill 成为绕过当前事实和主控决策的隐藏执行器。

## 8. 完成标准

- 计划可持久化、恢复和审计。
- 必需步骤由验证证据驱动状态迁移。
- 修复失败不会被沉淀为可复用教训。
- 技能缺少必填输入或契约不合格时不会执行。
- 报告链路保持同步、产物可验证、结果结构化回传。
- 后端专项测试与阶段 6–8 的 Playwright 页面验证全部通过。
