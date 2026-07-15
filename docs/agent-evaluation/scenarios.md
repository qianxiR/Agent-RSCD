# Agent 评估场景集

本文定义后续 Agent 评估场景。场景用于驱动测试、trace 采集和回归检查, 不是聊天演示脚本。

## 1. 场景设计原则

- 每个场景必须有明确输入、期望动作和可验证证据。
- 失败场景必须验证 `repair_plan` 是否影响下一轮动作。
- 真实 LLM 行为场景必须记录 trace, 不能只看最终回复。
- 评估场景优先覆盖高频错误、高风险错误和会污染记忆的错误。

## 2. E1 验证规则场景

当前固定命令:

```powershell
python tests\agent_evaluation_dataset.py
```

| 编号 | 输入 | 期望验证结果 | 期望修正策略 |
|---|---|---|---|
| E1-1 | 工具返回 error | `status=failed`, `failure_type=tool_error` | 诊断错误, 修正参数后重试 |
| E1-2 | 产物路径不存在 | `status=failed`, `failure_type=missing_artifact` | 检查输出路径, 重跑原工具 |
| E1-3 | 文件过小或空文件 | `status=failed/warning` | 检查上游数据, 重跑或更换格式 |
| E1-4 | 矢量要素数为 0 | `status=failed`, `failure_type=empty_vector` | 回到分割阶段, 保持类别并扩展同义词 |
| E1-5 | Shapefile ZIP 缺组件 | `status=failed/warning` | 重导 Shapefile 或改用 GeoJSON |
| E1-6 | 参数非法 | `status=failed`, `failure_type=invalid_arguments` | 按 schema 和上下文修正参数 |
| E1-7 | 证据不足 | `status=warning` | 补证据, 不能直接收尾 |
| E1-8 | 本地文件不存在 | `status=failed`, `failure_type=missing_input_file` | 请求有效路径或上传文件, 不重复同一路径 |

## 3. E2 Observation 回灌场景

| 编号 | 输入 | 期望 ToolMessage |
|---|---|---|
| E2-1 | 工具 error | 包含 `agent_validation.status=failed` |
| E2-2 | 缺失产物 | 包含 `repair_plan.next_action` |
| E2-3 | 空矢量 | summary 包含 `parameter_changes` |
| E2-4 | warning 证据不足 | summary 明确不能收尾, 要补证据 |

## 4. E3 Prompt 契约场景

当前固定命令会检查以下 prompt 契约:

| 编号 | 契约 | 目的 |
|---|---|---|
| P1 | `agent_validation.status` 为 failed 或 warning 时禁止直接最终回复 | 防止 false success |
| P2 | 必须优先读取 `repair_plan.next_action` | 确保下一步修正有入口 |
| P3 | 下一轮工具调用体现 `recommended_tool` | 防止忽略推荐工具 |
| P4 | 下一轮工具调用体现 `parameter_changes` | 防止同参数机械重试 |
| P5 | 下一轮工具调用体现 `fallback_tools` | 防止推荐工具失败后无备选 |
| P6 | 当前可验证结果优先于历史记忆 | 防止腐烂上下文覆盖最新证据 |

## 5. E4 主控决策场景

这些场景需要 `decision_audit` 落地后执行。

当前固定命令:

```powershell
python tests\agent_behavior_harness_eval.py
```

| 编号 | 输入上下文 | 期望主控动作 | 判定方式 |
|---|---|---|---|
| E3-1 | 上一轮 `agent_validation.failed`, `repair_plan.recommended_tool=segment_image` | 下一轮调用推荐工具或 fallback 工具 | trace 比对 expected tool 与 actual tool |
| E3-2 | `missing_input_file`, 同一路径已失败 | 不重复同一路径导入, 请求有效路径或上传 | trace 中同一 retry signature 未重复 |
| E3-3 | `empty_vector`, 用户目标是建筑物 | 保持建筑物目标, 增加同义词回到分割 | tool_args 中目标类别未被替换 |
| E3-4 | `warning: evidence_insufficient` | 补证据或运行验证工具, 不直接最终回复成功 | final response 与 tool_call trace |
| E3-5 | `repair_plan.fallback_tools` 非空, 推荐工具连续失败 | 切换 fallback tool 或请求最小输入 | trace 中 actual tool 命中 fallback |

当前已执行行为样本:

| 编号 | 场景 | 当前结果 |
|---|---|---|
| A1 | 参数 `classes` 为空 | 未通过, 模型调用 `segment_image` 但参数表达未被审计规则认定为体现修正 |
| A2 | 本地文件不存在 | 通过, 模型不调用工具并请求有效路径或上传 |
| A3 | 空矢量 | 通过, 模型没有被旧摘要诱导到记忆工具 |
| A4 | 渲染产物路径不存在 | 通过, 模型调用 `run_shell_command` 检查路径 |
| A5 | Shapefile ZIP 缺组件 | 通过, 模型重导 Shapefile |
| A6 | 报告缺少可验证证据 | 通过, 模型检查报告产物证据 |
| A7 | GeoJSON 发布输入文件不存在 | 通过, 模型不调用工具并请求有效文件 |
| A8 | 沙盒下载源文件不存在 | 通过, 模型不调用工具并请求有效沙盒文件名 |
| A9 | GeoJSON 损坏不可读 | 通过, 模型调用 `run_python_code` 检查 / 修复格式 |
| A10 | 栅格发布权限路径错误 | 通过, 模型调用 fallback 工具检查 / 修复 |

## 6. E5 上下文冲突场景

这些场景需要 `context_priority` 和 `conflict_resolver` 落地后执行。

| 编号 | 冲突 | 期望裁决 |
|---|---|---|
| E4-1 | 会话摘要说任务完成, 最新 ToolMessage 显示 failed | 选择最新 ToolMessage |
| E4-2 | 长期记忆建议旧路径, 当前工具显示路径不存在 | 选择当前工具结果 |
| E4-3 | 用户最新要求改变输出格式, skill 默认输出旧格式 | 选择用户最新要求 |
| E4-4 | worker 汇报成功, verification_agent 显示产物缺失 | 选择 verification_agent |

## 7. E6 反思记忆场景

这些场景需要 `lesson_policy` 落地后执行。

| 编号 | 失败与修复 | 是否写入 lesson | 期望 lesson |
|---|---|---|---|
| E5-1 | 缺失本地输入文件, 请求用户上传后成功 | 是 | 本地文件不存在时不要重复 sandbox 导入 |
| E5-2 | 临时网络波动, 重试后成功 | 否或低优先级 | 不写入长期行为规则 |
| E5-3 | 空矢量, 扩展同义词后成功 | 是 | 空矢量时保持用户目标并扩展类别表达 |
| E5-4 | 参数非法, schema 修正后成功 | 是 | 参数修正应遵循工具 schema |

## 8. E7 技能和 worker 场景

这些场景需要 `skill_selector` 和专业 worker 落地后执行。

| 编号 | 任务 | 期望行为 |
|---|---|---|
| E6-1 | 遥感分割任务开始 | skill 或 worker 先检查输入文件、目标类别和输出格式 |
| E6-2 | 发布地图服务 | publish worker 汇报服务地址、图层名和验证结果 |
| E6-3 | 生成报告 | report worker 汇报证据来源、图件、表格和未满足条件 |
| E6-4 | worker 失败 | worker 只汇报失败和建议, 主控 Agent 决定下一步 |

## 9. 场景执行记录模板

每次真实 Agent 行为评估应按以下结构记录:

```text
case_id:
user_goal:
input_context:
expected_decision:
actual_tool_call:
actual_observation:
agent_validation:
repair_plan:
final_response:
pass_or_fail:
failure_reason:
next_repair:
```
