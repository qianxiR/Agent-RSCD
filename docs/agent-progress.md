# Agent 架构进度账本

本文是后端 Agent 架构专项 checkpoint ledger。项目总进度仍以 `docs\PROGRESS.md` 为准, 本文只记录 Agent 记忆、反思、验证、多 Agent 编排和运行时拆分。

## 1. 当前阶段结论

当前后端已经从单一 ReAct Agent 进入第一阶段主控式多 Agent 架构, 并已落地第一个专业 worker `report_agent` 的第一版:

```text
主控 Agent
→ 工具执行
→ Observation 构建
→ verification_agent 验证
→ repair_policy 生成 repair_plan
→ ToolMessage 回灌
→ 主控 Agent 继续决策
```

当前实际状态:

```text
已完成: 主控 Agent + verification_agent + repair_plan + decision_audit + retry_guard + context_priority + 参数级修正验证 + repair_success_rate 第一版。
已具备: assign_agent_task / query_agent_task / cancel_agent_task worker 调度外壳。
已落地: report_agent 第一版, 可在 segment_image 完成后回读 GeoServer 图层并生成叠加 PNG + PDF 报告。
未完成: rs_agent / publish_agent 等专业 worker, 显式 plan state, 通用 worker 汇报协议强化, lesson_policy。
```

这意味着当前多 Agent 不是“多个 LLM Agent 已经并行协作”, 而是“主控 Agent 已能调用受控 verification worker, 并且分割工具已同步委托 report_agent worker 生成报告证据”。

## 2. Checkpoint 账本

| Checkpoint | 状态 | 证据 | 剩余风险 |
|---|---|---|---|
| 单 Agent ReAct 基线 | 已完成 | `backend\agent\chat_service.py` 保留主控 ReAct 循环 | 主循环仍承担 WebSocket、LLM streaming、持久化等职责, 还可继续瘦身 |
| 三层记忆 | 已完成 | `backend\agent\memory\memory_context.py` 构建长期记忆、会话摘要、工作记忆 | 动态上下文优先级和冲突裁决尚未工程化 |
| verification_agent | 已完成第一版 | `backend\agent\team\verification_worker.py` + `tests\verification_agent_stage_test.py` | 当前是确定性规则, 不处理复杂语义判断 |
| repair_policy | 已完成第二版 | `backend\agent\team\repair_policy.py` 输出 `repair_plan`, 覆盖 9 类 failure_type | failure_type 仍需覆盖更多遥感和发布失败模式 |
| agent_validation 回灌 | 已完成 | `backend\agent\team\validation_stage.py` 写入 `tool_result.agent_validation` | 仍需扩大真实多轮修正样本 |
| repair_plan 回灌 | 已完成 | `tests\chat_service_validation_integration_test.py` 验证 ToolMessage content 和 summary | 仍需观察真实工具修正后的通过率 |
| missing_input_file | 已完成 | `tests\verification_agent_stage_test.py` 覆盖本地文件不存在分类 | 还未把该规则沉淀为 skill 前置检查 |
| prompt 拆分 | 已完成 | `backend\agent\prompt\static_template.py` / `dynamic_context.py` / `tool_catalog.py` / `system_prompt.py` | 动态上下文裁决逻辑还未接入 |
| runtime observation 层 | 已完成 | `backend\agent\runtime\observation_builder.py` 统一 ToolMessage 构造 | 仍需继续扩展运行期 trace |
| context_priority | 已完成第一版 | `backend\agent\context\context_priority.py` + `tests\agent_guardrail_eval.py` | 当前是确定性裁决, 尚未覆盖全部动态上下文来源 |
| retry_guard | 已完成第一版 | `backend\agent\runtime\retry_guard.py` + `tests\agent_guardrail_eval.py` | 仍需和多轮真实工具执行深度集成 |
| decision audit | 已完成第一版 | `backend\agent\observability\decision_audit.py`、`parameter_repair.py`、`logs\agent-runtime-audit.jsonl` | 仍需把更多真实会话纳入 trace |
| repair_success_rate | 已完成第一版 | `backend\agent\observability\repair_success.py`、`tests\agent_repair_success_eval.py`、`logs\agent-repair-success.jsonl` | 当前固定评估为小样本, 需扩展真实工具链多轮任务 |
| GeoServer E2E 报告评估 | 已完成第一版 | `tests\e2e_geoserver_report_eval.py`、`tests\artifacts\e2e_geoserver_report_metrics.json` | 依赖本机 GeoServer 和 `sam3` conda 环境 |
| worker 调度工具 | 已完成基础版 | `assign_agent_task` / `query_agent_task` / `cancel_agent_task` 已暴露 | 通用汇报协议仍需继续强化 |
| report_agent | 已完成第一版 | `backend\agent\team\report_worker.py` + `tests\segment_auto_report_test.py` | 当前分割链路同步调用, 还不是独立可视化子任务卡 |
| 专业 worker 白名单 | 已完成基础版 | `backend\agent\team\agent_roles.py` 当前开放 `verification_agent` 和 `report_agent` | `rs_agent` / `publish_agent` 仍未开放 |
| 工具暴露边界 | 已完成第一版 | `backend\model\tools\tool_registry.py` 收窄 LLM 可见工具和 worker 工具白名单 | worker 专属工具白名单还需随 rs / publish worker 继续拆分 |

## 3. 最近提交节点

| 提交 | 主题 | 验收证据 |
|---|---|---|
| `4fbf328` | `feat: add verification agent repair stage` | verification_agent、repair_policy、worker 基础设施和验证测试 |
| `9bf965d` | `fix: enforce validation repair loop` | ToolMessage 集成测试验证 `agent_validation` 和 `repair_plan` |
| `c8cce40` | `refactor: split prompt and observation runtime` | prompt 拆分、runtime observation 拆分、`missing_input_file` 策略 |

## 4. 当前架构形态

当前关键目录:

```text
backend\agent\
├── chat_service.py
├── prompt\
│   ├── system_prompt.py
│   ├── static_template.py
│   ├── dynamic_context.py
│   └── tool_catalog.py
├── runtime\
│   ├── context_vars.py
│   ├── observation_builder.py
│   └── retry_guard.py
├── context\
│   └── context_priority.py
├── observability\
│   ├── decision_audit.py
│   ├── parameter_repair.py
│   └── repair_success.py
├── team\
│   ├── agent_roles.py
│   ├── report_worker.py
│   ├── result_contract.py
│   ├── task_manager.py
│   ├── validation_stage.py
│   ├── verification_worker.py
│   └── repair_policy.py
└── memory\
```

当前数据流:

```text
用户输入
→ memory_context.build_context_messages
→ prompt 构建静态规则 + 动态上下文
→ LLM 生成 tool_calls
→ chat_service 执行工具
→ runtime.observation_builder 构造 Observation
→ validation_stage / verification_worker 验证
→ repair_policy 生成 repair_plan
→ ToolMessage 回灌
→ LLM 继续 ReAct 或最终回复
```

## 5. 已验证测试

当前 Agent 专项测试:

```powershell
python tests\verification_agent_stage_test.py
python tests\chat_service_validation_integration_test.py
python tests\segment_auto_report_test.py
```

验证覆盖:

- 工具 error 能生成 `agent_validation`。
- 产物路径缺失能生成 `missing_artifact`。
- 空矢量能保持原目标类别并扩展同义词。
- 本地输入文件不存在能生成 `missing_input_file`。
- ToolMessage content 中包含 `agent_validation` 和 `repair_plan`。
- summary 中包含 `next_action`、`recommended_tool`、`parameter_changes`、`fallback_tools`。
- `report_agent` 可从 GeoServer 图层输入生成结构化 worker report、叠加 PNG 和 PDF 报告。

Prompt 构建验证:

```powershell
python -c "from backend.agent.prompt import build_system_prompt, build_system_prompt_blocks; p=build_system_prompt('摘要','画像','状态'); b=build_system_prompt_blocks('摘要','画像','状态'); print('PROMPT_OK', '会话摘要' in p, len(b), isinstance(b[0], dict))"
```

期望输出:

```text
PROMPT_OK True 2 True
```

## 6. 现阶段评估体系

Agent 评估已从本进度账本拆出到独立目录:

```text
docs\agent-evaluation\
├── README.md
├── baseline-2026-07-07.md
├── baseline-2026-07-08.md
├── commands.md
├── e2e-geoserver-report.md
├── evaluation-set.md
├── metrics.md
├── prompt-optimization-log.md
└── scenarios.md
```

当前基线同时评估规则层、工程护栏层和真实 LLM 行为层。真实行为评估使用 10 条腐烂上下文样本, 判断主控 Agent 是否基于最新 ToolMessage 和 repair_plan 做出可验证决策。

当前已建立指标:

| 指标 | 当前基线 |
|---|---:|
| validation_rule_pass_rate | 11 / 11 |
| observation_injection_pass_rate | 5 / 5 |
| prompt_contract_pass_rate | 6 / 6 |
| covered_failure_types | 9 |
| guardrail_pass_rate | 10 / 10 |
| guardrail_score | 100.0 / 100 |
| behavior_case_count | 10 |
| agent_behavior_score | 100.0 / 100 |
| llm_follow_rate | 10 / 10 |
| false_success_rate | 0 / 10 |
| no_false_success_rate | 10 / 10 |
| same_failure_repeat_rate | 0 / 10 |
| no_same_failure_repeat_rate | 10 / 10 |
| repair_success_rate | 1 / 2 |
| repair_success_score | 50.0 / 100 |
| geoserver_layers_read_rate | 2 / 2 |
| e2e_report_generated | true |
| e2e_report_verification_ok | true |

当前未建立指标:

| 指标 | 阻塞原因 |
|---|---|
| context_conflict_resolution_accuracy | 已有基础 `context_priority`, 缺少真实动态上下文冲突样本 |
| lesson_precision | 缺少 `lesson_policy` |

详细基线、固定命令、指标定义和场景集见 `docs\agent-evaluation\baseline-2026-07-08.md`、`docs\agent-evaluation\commands.md`、`docs\agent-evaluation\metrics.md`、`docs\agent-evaluation\evaluation-set.md`、`docs\agent-evaluation\scenarios.md`。

## 7. 下一阶段执行重点

下一阶段不再优先扩写 prompt。当前 `repair_success_rate` 已有第一版, 最该补的是“主控分配子任务后, worker 如何结构化汇报, 以及专业 worker 是否能把工具结果和验证证据稳定带回主控”。

优先级如下:

| 优先级 | 任务 | 原因 | 完成信号 |
|---|---|---|---|
| P0 | worker 汇报协议泛化 | `report_agent` 已有第一版字段, 但所有 worker 仍需统一强 schema | `result_contract` 支持完整 worker report schema 和测试 |
| P0 | 扩展真实修正成功样本 | 当前 `repair_success_rate` 是第一版小样本, 还不能代表长任务稳定性 | 真实工具链样本覆盖遥感、发布、报告、下载 |
| P1 | `report_agent` 任务卡化 | 第一版已在分割工具内同步调用, 下一步让主控可显式派发和查询 | `assign_agent_task(agent_role="report_agent")` 有稳定前端/日志呈现 |
| P1 | worker 工具白名单 | 专业 worker 需要最小权限, 避免抢主控决策权 | `agent_roles.py` 为每个 worker 定义工具范围 |
| P2 | 显式 plan state | 当前主控仍靠隐式 ReAct, 长任务缺少可审计计划状态 | plan step 有状态、owner、evidence、next_action |
| P2 | lesson_policy | self_correction 已有, 但 lesson 写入质量还不够可控 | 只把有证据的修复沉淀为 lesson |

## 8. 未完成缺口

| 缺口 | 当前问题 | 下一 checkpoint |
|---|---|---|
| 显式 plan 结构 | 主控 Agent 还没有稳定输出和维护结构化计划 | 定义 plan state 和任务计划卡 |
| worker 汇报协议 | `report_agent` 已返回 artifacts / metrics / next_action / handoff_notes, 但还未形成所有 worker 强制 schema | 完善 `result_contract` |
| 上下文优先级 | 已有基础裁决, 但还没覆盖全部动态上下文来源 | 扩展真实上下文冲突样本 |
| decision audit / trace | 已有结构化行为观测和修正成功率第一版, 但真实多轮样本仍少 | 扩展真实修正成功样本 |
| retry guard | 已有重复失败签名, 但还需接入更多运行态场景 | 扩展多轮 retry 样本 |
| 结构化反思记忆 | self_correction 已有, 但 lesson 质量门槛还需细化 | 增加 `lesson_policy` |
| 技能选择 | `lookup_skill` 已存在, 但还不是主控计划的稳定步骤 | 增加 `skill_selector` |
| 专业 worker | 已有 verification_agent 和 report_agent 第一版 | 继续拆 `rs_agent`、`publish_agent` |

## 9. 当前风险

- 真实行为评估已达到 10 / 10, 但样本仍是固定集, 需要继续扩大真实多轮任务集。
- 长期记忆、会话摘要和最新工具结果冲突已有基础裁决, 但还没覆盖全部动态上下文来源。
- worker 数量过早增加会放大调试难度, 应先补 repair_success_rate 和 worker 汇报协议。
- skill 如果缺少 checkpoint, 会变成隐藏执行器, 破坏主控 Agent 决策中心定位。
