<!-- skill-contract
{
  "version": "1.0",
  "trigger_conditions": ["单张遥感影像语义分割", "提取指定地物并生成监测报告"],
  "required_inputs": ["image_path", "classes"],
  "steps": [
    {"step_id": "segment", "goal": "执行指定类别语义分割", "tool_name": "segment_image", "required_inputs": ["image_path", "classes"], "expected_outputs": ["vector_url", "mask_tif_url", "stats"], "verification_rules": ["agent_validation.status=passed"], "required": true},
    {"step_id": "report", "goal": "基于分割矢量生成监测报告", "tool_name": "generate_monitor_report", "required_inputs": ["change_geojson_path"], "expected_outputs": ["report_path", "report_url"], "verification_rules": ["agent_validation.status=passed", "report_path exists"], "required": true}
  ],
  "tool_allowlist": ["segment_image", "generate_monitor_report"],
  "expected_outputs": ["mask_tif_url", "vector_url", "report_url"],
  "verification_rules": ["每个必需步骤均为 agent_validation.status=passed"],
  "fallback_rule": "失败时执行 repair_plan；证据不足时不得宣告完成",
  "promotion_evidence": "分割产物和报告均通过真实路径验证"
}
-->

# 工作流：一键分割 + 可视化 + 报告

> 当用户上传一张影像并要求"分割""识别地物""分析类别"时，自动执行完整的分割→可视化→报告链路。

> ★ 前端为**地图容器**（OpenLayers）：分割结果以「原图层（底图）+ 掩膜 GeoTIFF 半透明叠加层」在地图上呈现，不再需要后端预生成 overlay 混色图或边缘叠加图。如需精确边缘描线，可手动调 `overlay_edge_on_image`。

## 触发场景

用户上传单张影像（通过上传按钮选 1 张图），并表达以下意图之一：
- "分割这张图""识别这张图的地物""分析这张影像"
- "看看这张图里有什么建筑/道路/水体"
- "这张图的地物分布是怎样的"

典型用户话术：
- "帮我分割这张图"
- "分析这张遥感影像的地物类别"
- "这张图里哪些是建筑？"

## 前置条件

- 用户已通过上传按钮上传了 1 张影像（前端会自动显示原图）
- 后端在 sam3 环境（torch + SamSeg 权重可用）
- 如果只说"分割"但没上传图，先引导用户上传

## 标准步骤

### 步骤 1：执行语义分割

调用 `segment_image` 工具：
```
segment_image(image_path="<用户上传的原图路径>", classes="建筑")
```
- `classes` 参数：根据用户需求选择类别。默认 7 类（建筑、道路、水体、植被、裸地、农业、其他）；用户指定某类时只分割该类
- 工具会自动产出：彩色掩码 PNG + **GeoTIFF 掩膜（带 CRS，前端地图做半透明叠加层）** + 矢量 GeoJSON + Shapefile + 统计数据
- 结果会自动渲染到前端地图容器：**原图层（底图）→ 掩膜 GeoTIFF 半透明叠加层 → 图例**（边缘效果由图层透明度自动实现，无需单独生成叠加图）
- 成功条件：`segment_image` 返回 `data.vector_url` 时，矢量图斑可叠加到地图；若 `data.shp_path` 存在，则 `.shp` 主文件必须真实落地

### 步骤 2：（可选）边缘描线

> 默认**不需要**：地图容器用图层透明度叠加掩膜即可看清边界。仅当用户明确要求"标注每个图斑的精确边界线"时，才手动调 `overlay_edge_on_image`（红色描线叠加原图）。
```
overlay_edge_on_image(
    source_image_path="<原图路径>",
    mask_path="<segment_image 返回的 data.mask_tif_path>",
    method="distance", edge_color="255,0,0", edge_thickness=2
)
```

### 步骤 3：（可选）连通域分析

如果用户想看"有多少个独立的建筑图斑"，调用 `analyze_connected_components`：
```
analyze_connected_components(
    mask_path="<segment_image 的 mask.tif>",
    export_geojson=true,
    source_image_path="<原图路径>"
)
```

### 步骤 4：生成监测报告

调用 `generate_monitor_report` 生成 PDF/Word 报告：
```
generate_monitor_report(
    change_geojson_path="<segment_image 的矢量 GeoJSON 路径>",
    output_format="pdf"
)
```
- 报告含：分割统计表（每类面积/占比/图斑数）+ 分割结果图
- 生成后自动触发前端下载

### 步骤 5：（可选）导出矢量

如果用户需要 GIS 可用数据：
```
export_change_vector(
    change_geojson_path="<矢量 GeoJSON 路径>",
    output_format="shapefile",
    extract_shp=true
)
```

## 完成判断

- 地图容器显示：输入原图层 → 掩膜 GeoTIFF 半透明叠加层 → 图例
- `segment_image` 成功返回 `data.vector_url`（矢量图斑可叠加到地图）；`data.mask_tif_url` 指向带 CRS 的掩膜（地图叠加层）
- 统计数据已告知用户（每类面积/占比）
- 报告已生成并可下载
- 向用户总结分割结果（主要类别分布）

## 异常处理

- **SamSeg 不可用**（base 环境无 torch）：明确告知用户"分割功能需要 sam3 环境"，不假装成功
- **分割结果为空**（全黑掩码）：提示用户"未检测到指定类别，尝试换类别或检查影像"
- **矢量可视化失败**（geopandas/matplotlib 缺失或 GeoJSON 为空）：不阻断分割主结果，告知用户“GeoJSON 已生成但未渲染为工作区图片”，必要时切换 sam3 环境或单独调用 `visualize_vector`
- **报告生成失败**（reportlab 缺失）：降级生成 Markdown 报告
