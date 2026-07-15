# Agent 固定评估命令集

本文定义当前阶段固定评估命令。固定命令的作用是把评估数据集变成可重复运行的基线, 避免只靠聊天记录判断 Agent 是否变好。

## 1. 快速基线命令

用于得到当前核心量化指标:

```powershell
python tests\agent_evaluation_dataset.py
```

当前期望输出包含:

```text
agent_evaluation_dataset: PASS
```

该命令会写入评估产物:

```text
tests\artifacts\agent_evaluation_metrics.json
```

## 2. 分层验证命令

验证 verification_agent 和 repair_policy:

```powershell
python tests\verification_agent_stage_test.py
```

验证 ToolMessage 观察回灌:

```powershell
python tests\chat_service_validation_integration_test.py
```

验证 prompt 构建器基础契约:

```powershell
python -c "from backend.agent.prompt import build_system_prompt, build_system_prompt_blocks; p=build_system_prompt('摘要','画像','状态'); b=build_system_prompt_blocks('摘要','画像','状态'); print('PROMPT_OK', '会话摘要' in p, len(b), isinstance(b[0], dict))"
```

验证修正动作是否真的让后续工具结果通过:

```powershell
python tests\agent_repair_success_eval.py
```

验证分割完成后的 `report_agent` 自动报告 worker:

```powershell
python tests\segment_auto_report_test.py
```

产物:

```text
tests\artifacts\agent_repair_success_metrics.json
```

当前基线:

| 指标 | 当前值 |
|---|---:|
| `repair_attempt_count` | 3 |
| `repair_completed_count` | 2 |
| `repair_success_rate` | 1 / 2 |
| `repair_failed_rate` | 1 / 2 |
| `repair_success_score` | 50.0 / 100 |

## 3. 当前固定数据集覆盖

`python tests\agent_evaluation_dataset.py` 当前覆盖:

| 层级 | 样本数 | 覆盖内容 |
|---|---:|---|
| 验证规则 | 11 | passed、failed、warning、skip 以及多类 failure_type |
| Observation 回灌 | 5 | `agent_validation`、`repair_plan`、summary 修正字段 |
| Prompt 契约 | 6 | failed/warning 不能收尾、读取 repair_plan、体现参数修正 |

当前固定 failure_type 覆盖:

```text
tool_error
missing_artifact
empty_vector
missing_input_file
invalid_arguments
incomplete_shapefile_zip
insufficient_evidence
missing_sandbox_file
missing_vector_artifact
```

## 4. 当前量化指标

固定命令应输出以下指标:

| 指标 | 当前目标值 |
|---|---:|
| `validation_rule_pass_rate` | 11 / 11 |
| `observation_injection_pass_rate` | 5 / 5 |
| `prompt_contract_pass_rate` | 6 / 6 |
| `covered_failure_types` | 9 |
| `repair_success_rate` | 1 / 2 |
| `llm_follow_rate` | 10 / 10 |
| `same_failure_repeat_rate` | 0 / 10 |
| `false_success_rate` | 0 / 10 |

## 5. 后续真实 Agent 评估命令占位

以下命令调用真实主控 Agent 的 system prompt、工具 schema、ToolMessage 和 LLM, 用于观测模型是否遵循 `repair_plan`:

```powershell
python tests\agent_behavior_harness_eval.py
```

产物:

```text
logs\agent-trace.jsonl
tests\artifacts\agent_behavior_metrics.json
```

当前期望输出包含:

```text
agent_behavior_harness_eval: PASS
```

当前基线:

| 指标 | 当前值 |
|---|---:|
| `behavior_case_count` | 10 |
| `agent_behavior_score` | 100.0 / 100 |
| `agent_behavior_score_threshold` | 80 / 100 |
| `llm_follow_rate` | 10 / 10 |
| `false_success_rate` | 0 / 10 |
| `no_false_success_rate` | 10 / 10 |
| `same_failure_repeat_rate` | 0 / 10 |
| `no_same_failure_repeat_rate` | 10 / 10 |

当前评估样本数:

| 层级 | 样本数 | 覆盖内容 |
|---|---:|---|
| 主控决策行为 | 10 | 参数非法、本地文件不存在、空矢量、缺失产物、Shapefile 缺组件、证据不足、发布失败、下载失败、矢量损坏、权限路径错误 |

该命令依赖真实 LLM 配置:

```powershell
$env:DASHSCOPE_API_KEY
```

如果未配置 API Key, 该命令应失败并提示无法执行真实 Agent 行为评估, 不能用规则层评估代替。

## 6. GeoServer 到报告端到端评估

该命令读取 GeoServer 上的两个模型结果图层, 生成本地 GeoJSON、原图叠加矢量 PNG 和 PDF 监测报告:

```powershell
conda run -n sam3 python tests\e2e_geoserver_report_eval.py
```

输入图层:

```text
9ff53e2a__seg_s47d
9ff53e2a__seg_s47d_edge
```

产物:

```text
tests\artifacts\e2e_geoserver_report_metrics.json
tests\artifacts\e2e_geoserver_report\e2e_geoserver_report_summary.md
agent-files\analysis\agent_ev\e2e_geos\e2e_geos_vector_ospf.png
agent-files\report\agent_ev\e2e_geos\e2e_geos_report_d512.pdf
```

当前基线:

| 指标 | 当前值 |
|---|---:|
| `geoserver_available` | true |
| `layers_requested` | 2 |
| `layers_read_rate` | 2 / 2 |
| `overlay_image_generated` | true |
| `report_generated` | true |
| `report_verification_ok` | true |

运行约束:

```text
该命令依赖本机 GeoServer 服务和 sam3 conda 环境。
基础 Python 环境缺少 geopandas 时不要作为失败结论, 应使用 sam3 环境运行。
```

## 7. 真实分割到自动报告端到端评估

该命令调用真实 `segment_image`, 在分割和 GeoServer 发布完成后自动触发 `report_agent`, 并验证叠加 PNG 与 PDF 报告:

```powershell
conda run -n sam3 python tests\e2e_segment_auto_report_eval.py
```

产物:

```text
tests\artifacts\e2e_segment_auto_report_metrics.json
agent-files\report\<project_id>\<conversation_id>\*.pdf
agent-files\report\<project_id>\<conversation_id>\auto_report_inputs\*.geojson
agent-files\report\<project_id>\<conversation_id>\auto_report_inputs\*.tif
```

运行约束:

```text
该命令会运行真实 SamSeg 分割, 耗时明显高于规则层测试。
只有在 sam3 conda 环境、模型权重和 GeoServer 服务都可用时才运行。
```
