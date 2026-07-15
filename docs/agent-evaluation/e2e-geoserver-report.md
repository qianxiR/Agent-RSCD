# GeoServer 图层到报告端到端评估

本文记录当前 GeoServer 模型结果读取、GeoJSON 落盘、报告生成和产物校验的端到端基线。

## 1. 评估目标

验证后端工具链是否能从 GeoServer 读取指定模型结果图层, 并基于读取到的矢量数据生成可校验的监测报告。报告中必须包含图斑矢量叠加到原始遥感影像后的 PNG 证据图。

该评估回答的问题是:

```text
当上游模型结果已经发布到 GeoServer 后, Agent 工具链能否基于真实服务数据完成读取、统计、叠加图生成、报告生成和产物验证。
```

## 2. 固定命令

```powershell
conda run -n sam3 python tests\e2e_geoserver_report_eval.py
```

运行约束:

```text
需要本机 GeoServer 可访问。
需要 sam3 conda 环境。
基础 Python 环境缺少 geopandas 时, 不作为端到端失败结论。
```

## 3. 输入图层

| 图层 | 说明 |
|---|---|
| `9ff53e2a__seg_s47d` | 分割面结果 |
| `9ff53e2a__seg_s47d_edge` | 分割边界结果 |

GeoServer 解析后的图层名:

```text
samseg:9ff53e2a__seg_s47d
samseg:9ff53e2a__seg_s47d_edge
```

## 4. 当前基线

| 指标 | 当前值 |
|---|---:|
| `geoserver_available` | true |
| `layers_requested` | 2 |
| `layers_read_rate` | 2 / 2 |
| `overlay_image_generated` | true |
| `report_generated` | true |
| `report_verification_ok` | true |

图层读取摘要:

| 图层 | 要素数 | 几何类型 | 总面积 m2 |
|---|---:|---|---:|
| `9ff53e2a__seg_s47d` | 28 | MultiPolygon | 463193.05 |
| `9ff53e2a__seg_s47d_edge` | 28 | MultiLineString | 463193.05 |

报告摘要:

```text
28 个图斑
总面积 463193.05 m2
折合 46.32 ha
主导变化: building
叠加图: 已嵌入
业务类型: 分割结果监测
```

## 5. 产物

指标产物:

```text
tests\artifacts\e2e_geoserver_report_metrics.json
```

本地 GeoJSON:

```text
tests\artifacts\e2e_geoserver_report\9ff53e2a__seg_s47d.geojson
tests\artifacts\e2e_geoserver_report\9ff53e2a__seg_s47d_edge.geojson
```

叠加 PNG:

```text
agent-files\analysis\agent_ev\e2e_geos\e2e_geos_vector_ospf.png
```

Markdown 摘要:

```text
tests\artifacts\e2e_geoserver_report\e2e_geoserver_report_summary.md
```

PDF 报告:

```text
agent-files\report\agent_ev\e2e_geos\e2e_geos_report_d512.pdf
```

## 6. 当前边界

- 当前 PDF 校验确认文件存在且大小有效, 并通过二进制结构检查发现 `/Subtype /Image` 图片对象。
- 当前 `sam3` 环境未安装 `pypdf` 或 `PyPDF2`, 因此未做页数级深度校验。
- 当前评估不调用真实 LLM, 只验证真实工具链和真实 GeoServer 数据读取。
- 当前图层名固定, 后续可以扩展为多项目、多工作空间和缺失图层失败样本。

## 7. 下一步

| 扩展项 | 目的 |
|---|---|
| PDF 页数和文本校验 | 证明报告不是空 PDF |
| 缺失图层失败样本 | 验证 `missing_vector_artifact` 或 GeoServer 读取失败策略 |
| 报告 Agent 接入 | 让 `report_agent` 使用该 E2E 作为验收命令 |
| 多轮主控调用样本 | 观察主控 Agent 是否能从 GeoServer 证据推进到报告产物 |
