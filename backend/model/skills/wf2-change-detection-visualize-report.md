<!-- skill-contract
{
  "version": "1.0",
  "trigger_conditions": ["双时相遥感影像变化检测", "违建或新增建筑监测"],
  "required_inputs": ["t1_path", "t2_path"],
  "steps": [
    {"step_id": "detect", "goal": "执行双时相变化检测", "tool_name": "detect_change", "required_inputs": ["t1_path", "t2_path"], "expected_outputs": ["vector_url", "mask_tif_url", "stats"], "verification_rules": ["agent_validation.status=passed"], "required": true},
    {"step_id": "chart", "goal": "生成变化统计图表", "tool_name": "generate_change_stats_chart", "required_inputs": ["change_geojson_path"], "expected_outputs": ["chart_path"], "verification_rules": ["agent_validation.status=passed"], "required": true},
    {"step_id": "report", "goal": "生成变化监测报告", "tool_name": "generate_monitor_report", "required_inputs": ["change_geojson_path"], "expected_outputs": ["report_path", "report_url"], "verification_rules": ["agent_validation.status=passed", "report_path exists"], "required": true}
  ],
  "tool_allowlist": ["detect_change", "generate_change_stats_chart", "generate_monitor_report"],
  "expected_outputs": ["mask_tif_url", "vector_url", "chart_path", "report_url"],
  "verification_rules": ["每个必需步骤均为 agent_validation.status=passed"],
  "fallback_rule": "失败时执行 repair_plan；无显著变化时保留无变化证据并生成报告",
  "promotion_evidence": "变化矢量、统计图和报告均通过客观验证"
}
-->

# 工作流：一键变化检测 + 可视化 + 报告

> 当用户上传两张时相影像并要求"变化检测""对比变化""违建监测"时，自动执行完整的检测→可视化→报告链路。

> ★ 前端为**地图容器**（OpenLayers）：变化结果以「T1 原图层（底图）+ 变化掩膜 GeoTIFF 半透明叠加层」在地图上呈现，不再需要后端预生成 overlay 混色图或边缘叠加图。如需精确变化边缘描线，可手动调 `overlay_edge_on_image`。

## 触发场景

用户通过上传按钮选 2 张影像（前时相 T1 + 后时相 T2），并表达以下意图之一：
- "检测这两张图的变化""对比 T1 和 T2 的变化"
- "违建监测""新增建筑检测""地物变化分析"
- "这个区域一段时间内发生了什么变化"

典型用户话术：
- "帮我对比这两张图的变化"
- "检测这里有没有违章建筑新增"
- "T1 和 T2 哪里发生了变化"

## 前置条件

- 用户已上传 2 张影像（前端自动按 T1/T2 标注并显示）
- 两张图最好是**同一区域不同时间**的影像（分辨率/范围一致效果最佳）
- 后端在 sam3 环境

## 标准步骤

### 步骤 1：执行变化检测

调用 `detect_change` 工具：
```
detect_change(
    t1_path="<T1 影像路径>",
    t2_path="<T2 影像路径>",
    classes="建筑"
)
```
- `classes`：变化检测的目标类别。违建监测用"建筑"；通用变化用默认 7 类
- 工具自动产出：变化检测 PNG + **GeoTIFF 掩膜（带 CRS，前端地图做半透明叠加层）** + 矢量 GeoJSON + Shapefile + 统计数据
- 结果自动渲染到地图容器：**T1 原图层（底图）→ 变化掩膜 GeoTIFF 半透明叠加层 → 图例**（边缘效果由图层透明度自动实现，无需单独生成叠加图）
- 成功条件：`detect_change` 返回 `data.vector_url` 时，矢量变化图斑可叠加到地图；若 `data.shp_path` 存在，则 `.shp` 主文件必须真实落地

### 步骤 2：（可选）变化区域边缘描线

> 默认**不需要**：地图容器用图层透明度叠加变化掩膜即可看清变化区域边界。仅当用户明确要求"标出变化的精确边界线"时，才手动调 `overlay_edge_on_image`（红色描线叠加 T2 后时相原图）。
```
overlay_edge_on_image(
    source_image_path="<T2 影像路径>",
    mask_path="<detect_change 返回的 data.mask_tif_path>",
    method="distance", edge_color="255,0,0", edge_thickness=2
)
```

### 步骤 3：生成变化统计图表

调用 `generate_change_stats_chart`：
```
generate_change_stats_chart(
    change_geojson_path="<detect_change 的矢量 GeoJSON>",
    chart_type="both"
)
```
- 产出饼图（各类变化占比）+ 柱图（各类变化面积/图斑数）
- 自动渲染到地图容器 / 消息区

### 步骤 4：生成监测报告

调用 `generate_monitor_report` 生成 PDF 报告：
```
generate_monitor_report(
    change_geojson_path="<矢量 GeoJSON>",
    output_format="pdf"
)
```
- 报告含：变化统计表 + 变化图 + 饼图/柱图
- 自动触发前端下载

### 步骤 5：导出变化矢量（推荐）

变化检测结果导出为 Shapefile，供 GIS 软件使用：
```
export_change_vector(
    change_geojson_path="<矢量 GeoJSON>",
    output_format="shapefile",
    extract_shp=true
)
```

## 完成判断

- 地图容器显示：T1 原图层 → 变化掩膜 GeoTIFF 半透明叠加层 → 图例 → 统计图表
- `detect_change` 成功返回 `data.vector_url`（矢量变化图斑可叠加到地图）；`data.mask_tif_url` 指向带 CRS 的变化掩膜（地图叠加层）
- 报告 PDF 已生成可下载
- 矢量 Shapefile 已导出
- 向用户总结：变化总面积、主要变化类别、变化图斑数

## 异常处理

- **无变化**（变化掩码全黑）：明确告知用户"两期影像未检测到显著变化"，仍生成报告（记录"无变化"结论）
- **SamSeg 不可用**：告知需 sam3 环境
- **矢量可视化失败**（geopandas/matplotlib 缺失或 GeoJSON 为空）：不阻断变化检测主结果，告知用户“GeoJSON 已生成但未渲染为工作区图片”，必要时切换 sam3 环境或单独调用 `visualize_vector`
- **报告/图表失败**：降级 Markdown，不阻断主流程
