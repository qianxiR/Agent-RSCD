# Agent 评估体系

本文是后端 Agent 评估体系的入口文件。评估体系用于回答一个核心问题:

```text
在有限且会腐烂的上下文里, Agent 是否始终基于最新证据做出可验证决策。
```

评估不是只跑单元测试, 而是把规则验证、观察回灌、主控决策、反思记忆、技能复用和 worker 汇报放到同一套证据标准里管理。

## 1. 目录结构

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

| 文件 | 作用 |
|---|---|
| `README.md` | 评估体系总纲, 说明评估目标、层级和执行方式 |
| `baseline-2026-07-07.md` | 历史基线, 记录第一版验证和行为评估状态 |
| `baseline-2026-07-08.md` | 当前基线, 记录 decision audit、guardrail 和真实行为评估结果 |
| `commands.md` | 固定评估命令集, 把数据集入口、输出产物和目标指标固化 |
| `e2e-geoserver-report.md` | GeoServer 图层读取到报告生成的端到端评估基线 |
| `evaluation-set.md` | 当前评估集目录, 梳理 V/O/P/G/A 全部样本和覆盖边界 |
| `metrics.md` | 指标字典, 定义每个指标的含义、计算方式、证据来源和适用阶段 |
| `prompt-optimization-log.md` | prompt、context、audit 迭代实验记录 |
| `scenarios.md` | 评估场景集, 定义后续真实 Agent 行为测试和回归测试 |

## 2. 评估分层

| 层级 | 评估对象 | 当前状态 | 证据来源 |
|---|---|---|---|
| 规则层 | `verification_agent` 和 `repair_policy` 是否给出正确判断 | 已建立基线 | `tests\verification_agent_stage_test.py` |
| 观察层 | `agent_validation` 和 `repair_plan` 是否写入 ToolMessage | 已建立基线 | `tests\chat_service_validation_integration_test.py` |
| 决策层 | 主控 Agent 是否遵循 `repair_plan` | 已建立基线 | `tests\agent_behavior_harness_eval.py`、`logs\agent-trace.jsonl` |
| 修正结果层 | 遵循修正策略后, 下一次工具结果是否通过验证 | 已建立第一版 | `tests\agent_repair_success_eval.py`、`logs\agent-repair-success.jsonl` |
| 工程护栏层 | retry、上下文优先级和参数级修正是否稳定 | 已建立基线 | `tests\agent_guardrail_eval.py` |
| 端到端工具层 | GeoServer 图层读取、报告生成和产物校验是否闭环 | 已建立第一版 | `tests\e2e_geoserver_report_eval.py` |
| **①任务规划层** | **多步任务首步工具选择与参数完整性** | **已建立基线** | `tests\task_planning_eval.py` |
| **③失败探索层** | **失败后探索方向是否合理（非盲目重试）** | **已建立基线** | `tests\exploration_eval.py` |
| **④思维链质量层** | **务实度/问题导向/简洁/第一性原理（LLM-as-judge）** | **已建立基线** | `tests\thinking_quality_eval.py`（裁判 `gpt-5.6-terra`） |
| 上下文层 | 动态上下文冲突时是否选择最新证据 | 已建立基础护栏 | `backend\agent\context\context_priority.py` |
| 反思层 | 成功修复是否沉淀为高质量 lesson | 未建立 | 后续 `lesson_policy` 测试和记忆写入 trace |
| 技能层 | 高频流程是否能稳定复用 skill | 未建立 | 后续 `skill_selector` 和技能执行 trace |
| worker 层 | 专业 worker 是否按协议执行和汇报 | 已建立第一版 | `tests\segment_auto_report_test.py`, 后续补 `rs_agent` / `publish_agent` 测试 |

> ★ 四层能力评估（①任务规划/②抑制幻觉/③失败探索/④思维链质量）设计见 [capability-four-layers.md](capability-four-layers.md)。现有 1-6 层测 harness 约束有效性（"刹车灵不灵"），四层测 Agent 完整能力（"会不会开车"）。

## 3. 当前基线

当前已建立的是执行、验证、修正、审计闭环基线:

```text
验证规则能正确识别失败
→ repair_policy 能生成修正策略
→ ToolMessage 能把 agent_validation 和 repair_plan 回灌给主控 Agent
→ decision_audit 能判断真实 LLM 是否跟随 repair_plan
→ repair_success 能判断修正后工具结果是否真的 passed
→ guardrail 能覆盖 retry_guard、context_priority 和参数级修正验证
```

当前基线文件:

```text
docs\agent-evaluation\baseline-2026-07-08.md
```

当前固定评估命令:

```powershell
python tests\agent_evaluation_dataset.py
python tests\agent_guardrail_eval.py
python tests\agent_behavior_harness_eval.py
python tests\agent_repair_success_eval.py
python tests\segment_auto_report_test.py
conda run -n sam3 python tests\e2e_geoserver_report_eval.py
```

四层能力评估命令（①任务规划/③失败探索/④思维链质量，②复用行为评估）:

```powershell
python tests\task_planning_eval.py
python tests\exploration_eval.py
python tests\thinking_quality_eval.py
```

该命令会输出当前可量化指标, 并写入:

```text
tests\artifacts\agent_evaluation_metrics.json
tests\artifacts\agent_guardrail_metrics.json
tests\artifacts\agent_behavior_metrics.json
tests\artifacts\agent_repair_success_metrics.json
tests\artifacts\e2e_geoserver_report_metrics.json
tests\artifacts\e2e_segment_auto_report_metrics.json
tests\artifacts\task_planning_metrics.json
tests\artifacts\exploration_metrics.json
tests\artifacts\thinking_quality_metrics.json
logs\agent-trace.jsonl
logs\agent-repair-success.jsonl
logs\exploration-trace.jsonl
logs\judge-trace.jsonl
logs\task-planning-trace.jsonl
```

分层验证命令:

```powershell
python tests\verification_agent_stage_test.py
python tests\chat_service_validation_integration_test.py
python -c "from backend.agent.prompt import build_system_prompt, build_system_prompt_blocks; p=build_system_prompt('摘要','画像','状态'); b=build_system_prompt_blocks('摘要','画像','状态'); print('PROMPT_OK', '会话摘要' in p, len(b), isinstance(b[0], dict))"
```

## 4. 评估原则

- 工具结果、验证结果和产物证据优先于模型自述。
- `agent_validation.status=failed/warning` 后, 不能用最终回复代替修正动作。
- 评估必须记录输入上下文、期望决策、实际动作和证据。
- 未建立 instrumentation 的指标必须标记为未建立, 不能伪造数值。
- 固定评估命令必须能在本地重复运行, 并生成可追踪指标产物。
- 进度文档只记录 checkpoint 结论, 详细评估数据放在本目录。

## 5. 后续接入顺序

| 顺序 | 评估能力 | 目的 |
|---|---|---|
| 1 | 扩展真实 `repair_success_rate` 样本 | 把第一版小样本扩展到遥感、发布、报告、下载工具链 |
| 2 | `lesson_policy` | 验证反思记忆是否只沉淀高价值 lesson |
| 3 | `context_conflict_resolver` | 扩展真实动态上下文冲突样本 |
| 4 | `skill_selector` | 验证技能是否被正确选择、执行和回退 |
| 5 | worker 评估 | 在 report_agent 第一版基础上, 验证遥感、发布 worker 的输入输出和汇报协议 |

当前已新增真实行为观测命令:

```powershell
python tests\agent_behavior_harness_eval.py
```

该命令会调用真实 LLM, 并输出:

```text
logs\agent-trace.jsonl
tests\artifacts\agent_behavior_metrics.json
```
