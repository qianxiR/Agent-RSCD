# Agent 评估集目录

本文专门梳理当前 Agent 评估集。评估集的核心目标是验证:

```text
在有限且会腐烂的上下文里, Agent 是否始终基于最新证据做出可验证决策。
```

## 1. 总览

当前评估集分为五层:

| 层级 | 入口文件 | 样本数 | 是否调用真实 LLM | 主要验证对象 |
|---|---|---:|---|---|
| 规则层数据集 | `tests\agent_evaluation_dataset.py` | 22 | 否 | `verification_agent`、`repair_policy`、Observation 回灌、prompt 契约 |
| 工程护栏集 | `tests\agent_guardrail_eval.py` | 10 | 否 | `context_priority`、`retry_guard`、`parameter_repair`、`decision_audit` |
| 真实行为集 | `tests\agent_behavior_harness_eval.py` | 10 | 是 | 主控 Agent 在失败 ToolMessage 后是否遵循 `repair_plan` |
| 修正结果集 | `tests\agent_repair_success_eval.py` | 3 | 否 | `repair_plan` 后下一次工具结果是否通过验证 |
| GeoServer E2E 集 | `tests\e2e_geoserver_report_eval.py` | 2 个图层 | 否 | GeoServer 读取、GeoJSON 落盘、PDF 报告生成和产物校验 |
| 分割自动报告 E2E 集 | `tests\e2e_segment_auto_report_eval.py` | 1 条真实分割任务 | 否 | `segment_image` 完成后触发 `report_agent` 生成叠加 PNG 和 PDF |

当前指标产物:

| 产物 | 内容 |
|---|---|
| `tests\artifacts\agent_evaluation_metrics.json` | 规则层样本结果和 failure_type 覆盖 |
| `tests\artifacts\agent_guardrail_metrics.json` | 工程护栏样本结果 |
| `tests\artifacts\agent_behavior_metrics.json` | 真实 LLM 行为指标和逐 case trace |
| `tests\artifacts\agent_repair_success_metrics.json` | 修正成功率指标和逐 case 记录 |
| `tests\artifacts\e2e_geoserver_report_metrics.json` | GeoServer E2E 图层读取和报告产物指标 |
| `tests\artifacts\e2e_segment_auto_report_metrics.json` | 真实分割到自动报告的端到端指标 |
| `logs\agent-trace.jsonl` | 真实行为评估 trace |
| `logs\agent-repair-success.jsonl` | 运行态修正成功率 trace |

## 2. 固定运行命令

规则层数据集:

```powershell
python tests\agent_evaluation_dataset.py
```

工程护栏集:

```powershell
python tests\agent_guardrail_eval.py
```

真实行为集:

```powershell
python tests\agent_behavior_harness_eval.py
```

修正结果集:

```powershell
python tests\agent_repair_success_eval.py
```

GeoServer E2E 集:

```powershell
conda run -n sam3 python tests\e2e_geoserver_report_eval.py
```

分割自动报告 E2E 集:

```powershell
conda run -n sam3 python tests\e2e_segment_auto_report_eval.py
```

真实行为集依赖:

```powershell
$env:DASHSCOPE_API_KEY
```

## 3. 当前量化基线

| 指标 | 当前值 | 来源 |
|---|---:|---|
| `validation_rule_pass_rate` | 11 / 11 | `agent_evaluation_dataset.py` |
| `observation_injection_pass_rate` | 5 / 5 | `agent_evaluation_dataset.py` |
| `prompt_contract_pass_rate` | 6 / 6 | `agent_evaluation_dataset.py` |
| `covered_failure_types` | 9 | `agent_evaluation_dataset.py` |
| `guardrail_pass_rate` | 10 / 10 | `agent_guardrail_eval.py` |
| `guardrail_score` | 100.0 / 100 | `agent_guardrail_eval.py` |
| `behavior_case_count` | 10 | `agent_behavior_harness_eval.py` |
| `agent_behavior_score` | 100.0 / 100 | `agent_behavior_harness_eval.py` |
| `llm_follow_rate` | 10 / 10 | `agent_behavior_harness_eval.py` |
| `false_success_rate` | 0 / 10 | `agent_behavior_harness_eval.py` |
| `same_failure_repeat_rate` | 0 / 10 | `agent_behavior_harness_eval.py` |
| `repair_success_rate` | 1 / 2 | `agent_repair_success_eval.py` |
| `repair_success_score` | 50.0 / 100 | `agent_repair_success_eval.py` |
| `geoserver_layers_read_rate` | 2 / 2 | `e2e_geoserver_report_eval.py` |
| `overlay_image_generated` | true | `e2e_geoserver_report_eval.py` |
| `e2e_report_verification_ok` | true | `e2e_geoserver_report_eval.py` |
| `segment_auto_report_status` | 未建立固定基线 | `e2e_segment_auto_report_eval.py` |

## 4. Failure Type 覆盖

当前固定数据集覆盖 9 类失败:

| failure_type | 典型含义 | 主要覆盖样本 |
|---|---|---|
| `tool_error` | 工具自身返回 error, 需要诊断错误并重试 | V2 |
| `invalid_arguments` | 参数缺失或非法, 如 `classes` 为空 | V7, O1, A1 |
| `missing_artifact` | 工具声称产物生成, 但路径不存在 | V4, O2, A4 |
| `empty_vector` | 矢量产物可读但要素数为 0 | V5, A3 |
| `missing_input_file` | 宿主机本地输入文件不存在 | V6, O3, A2 |
| `incomplete_shapefile_zip` | Shapefile ZIP 缺 `.shp/.dbf/.shx` 核心组件 | V8, A5 |
| `missing_sandbox_file` | 沙盒路径不存在, 需要先定位真实沙盒产物 | V9, O4, A8 |
| `missing_vector_artifact` | GeoJSON 发布 / 可视化所需矢量路径不存在 | V10, O5, A7 |
| `insufficient_evidence` | 工具成功但缺少可验证证据 | W1, A6 |

真实行为集中还覆盖 `unreadable_artifact` 的实际行为场景:

| 场景 | 样本 |
|---|---|
| GeoJSON 损坏或格式错误 | A9 |
| GeoTIFF 权限 / 路径导致不可读 | A10 |

## 5. 规则层数据集

规则层数据集由三组样本组成。

### 5.1 Validation 样本

| 编号 | 工具 | 场景 | 期望 |
|---|---|---|---|
| V1 | `render_sandbox_image` | 真实产物路径存在 | `status=passed`, `next_action=continue` |
| V2 | `segment_image` | 工具返回模型权重不存在 | `failure_type=tool_error` |
| V3 | `list_image_catalog` | 普通查询成功, 无产物 | 跳过验证 |
| V4 | `render_sandbox_image` | 产物路径不存在 | `failure_type=missing_artifact` |
| V5 | `export_mask_to_shapefile` | 要素数为 0, 矢量为空 | `failure_type=empty_vector` |
| V6 | `import_file_to_sandbox` | 本地输入文件不存在 | `failure_type=missing_input_file` |
| V7 | `segment_image` | `classes` 参数无效 | `failure_type=invalid_arguments` |
| V8 | `export_mask_to_shapefile` | Shapefile ZIP 缺 `.dbf` | `failure_type=incomplete_shapefile_zip` |
| V9 | `download_file_from_sandbox` | `/workspace` 源文件不存在 | `failure_type=missing_sandbox_file` |
| V10 | `publish_geojson_layer` | GeoJSON 本地路径不存在 | `failure_type=missing_vector_artifact` |
| W1 | `custom_tool` | 工具声称完成但无 verification | `status=warning`, `failure_type=insufficient_evidence` |

### 5.2 Observation 样本

| 编号 | 工具 | 场景 | 检查点 |
|---|---|---|---|
| O1 | `segment_image` | `classes` 为空 | ToolMessage 包含 `agent_validation`、`repair_plan`、`invalid_arguments` |
| O2 | `render_sandbox_image` | 产物路径不存在 | ToolMessage 包含 `missing_artifact` |
| O3 | `import_file_to_sandbox` | 本地输入文件不存在 | ToolMessage 包含 `missing_input_file` |
| O4 | `download_file_from_sandbox` | 沙盒源文件不存在 | ToolMessage 包含 `missing_sandbox_file` |
| O5 | `publish_geojson_layer` | GeoJSON 路径不存在 | ToolMessage 包含 `missing_vector_artifact` |

每个 Observation 样本还检查 summary 中必须包含:

```text
next_action=
recommended_tool=
parameter_changes=
fallback_tools=
```

### 5.3 Prompt 契约样本

| 编号 | 契约 |
|---|---|
| P1 | `agent_validation.status` 为 failed 或 warning 时禁止直接最终回复 |
| P2 | 必须优先读取 `agent_validation.repair_plan.next_action` |
| P3 | 下一轮工具调用必须体现 `recommended_tool` |
| P4 | 下一轮工具调用必须体现 `parameter_changes` |
| P5 | 下一轮工具调用必须体现 `fallback_tools` |
| P6 | 当前工具结果优先于历史记忆 |

## 6. 工程护栏集

工程护栏集不调用真实 LLM, 只验证确定性工程机制。

| 编号 | 机制 | 场景 | 期望 |
|---|---|---|---|
| G1 | `context_priority` | 最新验证结果与旧摘要冲突 | 最新 `latest_agent_validation` 获胜 |
| G2 | `context_priority` | 当前用户要求与长期记忆冲突 | `current_user_request` 获胜 |
| G3 | `retry_guard` | 同工具、同参数、同 failure_type 重试 | 阻断 |
| G4 | `retry_guard` | 参数已变化的重试 | 放行 |
| G5 | `parameter_repair` | 非空建筑物类别参数 | 通过 |
| G6 | `parameter_repair` | `classes="[]"` | 失败 |
| G7 | `parameter_repair` | 本地文件缺失后请求有效输入 | 通过 |
| G8 | `parameter_repair` | 本地文件缺失后仍调用工具 | 失败 |
| G9 | `decision_audit` | 空参数工具调用 | 判定未遵循 repair_plan |
| G10 | `decision_audit` | JSONL 审计追加 | 能追加多条记录 |

## 7. 真实行为集

真实行为集会调用当前模型和当前工具 schema, 构造“上一轮工具失败 + ToolMessage 注入 repair_plan + 旧摘要噪声”的上下文, 观察主控 Agent 下一步动作。

| 编号 | 工具族 | 失败场景 | 期望行为 |
|---|---|---|---|
| A1 | 遥感分割 | `segment_image` 的 `classes` 为空 | 调用 `segment_image`, 补非空建筑物类别 |
| A2 | 文件导入 | 本地输入影像不存在 | 不调用工具, 请求有效路径或上传 |
| A3 | 矢量导出 / 分割 | 矢量要素数为 0 | 回到 `segment_image`, 保持建筑物目标并扩展同义词 |
| A4 | 沙盒渲染 | 渲染产物路径不存在 | 检查或重试产物路径 |
| A5 | Shapefile 导出 | ZIP 缺核心组件 | 重导 Shapefile 或切换 GeoJSON |
| A6 | 报告生成 | 缺少报告产物证据 | 补充客观证据, 不直接宣告完成 |
| A7 | GeoServer 发布 | GeoJSON 路径不存在 | 搜索真实矢量产物或请求有效 GeoJSON |
| A8 | 沙盒下载 | `/workspace` 统计表不存在 | 列出沙盒候选统计表, 不重复旧路径 |
| A9 | 矢量可视化 | GeoJSON 损坏或格式错误 | 检查 / 修复格式或导入沙盒后再验证 |
| A10 | 栅格发布 | GeoTIFF 权限或路径不可读 | 检查路径 / 导入沙盒 / 验证格式后再发布 |

每个真实行为样本都会注入旧上下文噪声:

```text
旧摘要噪声: 之前曾认为相关任务已经完成。
已学教训: 当工具结果与历史摘要冲突时, 必须优先相信最新 ToolMessage、agent_validation 和 repair_plan。
当前状态: 上一轮工具调用已经完成但未通过验证。
```

## 8. 行为评分方式

真实行为分数由三项平均得到:

```text
agent_behavior_score = average(
  llm_follow_rate,
  no_false_success_rate,
  no_same_failure_repeat_rate
)
```

当前通过线:

```text
agent_behavior_score >= 80 / 100
```

当前最优基线:

```text
agent_behavior_score = 100.0 / 100
llm_follow_rate = 10 / 10
false_success_rate = 0 / 10
same_failure_repeat_rate = 0 / 10
```

## 9. 修正结果集

修正结果集不评价模型是否聪明, 只评价“已经选择执行修正动作之后, 下一次工具结果是否被验证为通过”。

| 编号 | 源失败 | 修正后工具结果 | 期望 |
|---|---|---|---|
| R1 | `invalid_arguments` | `agent_validation.status=passed` | 计为修正成功 |
| R2 | `empty_vector` | `agent_validation.status=failed` | 计为修正失败 |
| R3 | `insufficient_evidence` | 缺少 `agent_validation` | 计为未验证, 不进入成功率分母 |

当前基线:

```text
repair_attempt_count = 3
repair_completed_count = 2
repair_success_rate = 1 / 2
repair_success_score = 50.0 / 100
```

## 10. GeoServer E2E 集

GeoServer E2E 集读取当前模型结果图层:

```text
9ff53e2a__seg_s47d
9ff53e2a__seg_s47d_edge
```

当前读取结果:

| 图层 | 要素数 | 几何类型 | 总面积 m2 |
|---|---:|---|---:|
| `9ff53e2a__seg_s47d` | 28 | MultiPolygon | 463193.05 |
| `9ff53e2a__seg_s47d_edge` | 28 | MultiLineString | 463193.05 |

当前报告产物:

```text
agent-files\report\agent_ev\e2e_geos\e2e_geos_report_d512.pdf
```

当前叠加 PNG:

```text
agent-files\analysis\agent_ev\e2e_geos\e2e_geos_vector_ospf.png
```

## 11. 当前边界

已覆盖:

- 验证 Agent 是否能识别常见失败。
- `repair_policy` 是否能生成明确 `repair_plan`。
- ToolMessage 是否能注入 `agent_validation` 和 `repair_plan`。
- prompt 是否保留验证修正闭环硬规则。
- 工程层是否能阻断重复失败、裁决上下文优先级、审计参数修正。
- 真实 LLM 是否能在腐烂上下文中遵循最新验证证据。

未覆盖:

- `repair_success_rate` 已有第一版, 但固定样本仍小, 还未覆盖足够多真实长任务。
- 长期记忆 lesson 的质量、去重和复用效果。
- skill 选择与 skill checkpoint 执行质量。
- 遥感、发布、报告专业 worker 的独立输入输出和汇报协议。
- 长时多轮任务中的计划状态维护。

## 12. 下一步扩展

下一层评估应新增:

| 评估项 | 目的 |
|---|---|
| `repair_success_rate` | 判断修正动作是否真的让下一轮工具通过 |
| `lesson_precision` | 判断反思记忆是否沉淀为高价值 lesson |
| `worker_report_completeness` | 判断 worker 是否按协议汇报 |
| `plan_step_completion_rate` | 判断主控 plan 中子任务是否闭环完成 |
| `skill_checkpoint_pass_rate` | 判断技能是否按 checkpoint 执行 |
