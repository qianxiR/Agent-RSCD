# 工作流：上传影像到 GeoServer + 前端显示

> 当用户想把本地影像发布为 GeoServer 图层服务并在界面显示时，执行上传→发布→加载链路。

## 触发场景

用户上传或指定一个本地影像文件，并表达以下意图之一：
- "把这张影像发布到 GeoServer""上传到地图服务"
- "让这张图在地图上显示""发布为图层"
- "把这个 GeoTIFF 发布出去"

典型用户话术：
- "把这张影像发布到 GeoServer"
- "上传这张图到地图服务并显示"
- "发布这个 GeoTIFF 为图层"

## 前置条件

- 用户提供了本地影像文件的绝对路径（GeoTIFF/GeoJSON/Shapefile）
- GeoServer 服务已启动且可连通
- 影像文件有正确的坐标系（CRS），否则发布后无法正确定位

## 标准步骤

### 步骤 1：上传影像到 GeoServer

根据文件类型选择上传工具：

**栅格影像（GeoTIFF）**：
```
upload_raster_layer(
    file_path="<本地 GeoTIFF 绝对路径>",
    layer_name="<图层名>",
    workspace="<工作空间>"
)
```

**矢量 Shapefile**：
```
upload_shapefile_layer(
    file_path="<本地 .shp 或 .zip 绝对路径>",
    layer_name="<图层名>",
    workspace="<工作空间>"
)
```

**GeoJSON 矢量**（分割/连通域产物）：
```
publish_geojson_layer(
    geojson_path="<GeoJSON 绝对路径>",
    layer_name="<图层名>",
    show_immediately=true
)
```

- `layer_name`：留空则用文件名；中文会被 ASCII 化
- `workspace`：留空用默认工作空间
- 上传成功后，图层元数据会自动登记到业务数据库（image_metadata / vector_layer）

### 步骤 2：加载图层到前端显示

上传成功后，调用 `load_geoserver_layer` 把图层加载到卡片墙：
```
load_geoserver_layer(
    layer_id="<图层名>",
    layer_name="<图层名>",
    workspace="<工作空间>",
    layer_type="raster"
)
```
- `layer_type`：栅格用 "raster"，矢量用 "vector"
- 这个工具会触发前端 WMS GetMap 请求，把图层渲染成卡片显示

### 步骤 3：（可选）调整显示

如果需要调整图层显示状态：
- 显示/隐藏：`toggle_layer_visibility(layer_name="<图层名>", action="show")`
- 定位到图层范围：`locate_to_layer_bounds(layer_name="<图层名>")`
- 隐藏图层：`hide_layer(layer_name="<图层名>")`

## 完成判断

- GeoServer 中已存在该图层（可用 `list_geoserver_services` 确认）
- 图层已加载到前端卡片墙显示
- 业务数据库已登记元数据
- 向用户确认：图层名、工作空间、WMS 访问 URL

## 异常处理

- **GeoServer 不可用**：告知用户"GeoServer 服务未启动"，不继续上传
- **文件格式不支持**：提示支持的格式（GeoTIFF/Shapefile/GeoJSON）
- **上传失败**（权限/空间不足）：返回 GeoServer 的错误信息，建议检查工作空间配置
- **图层已存在**：upload 会覆盖同名图层（更新语义），告知用户"已更新"
- **无坐标系**：发布后定位异常，提示用户"影像缺少 CRS，显示位置可能不准"

## 相关工具

- `delete_geoserver_layer`：删除已发布的图层（需要清理时）
- `download_geoserver_layer`：下载图层（备份/转格式）
- `search_images_by_region` / `search_images_by_time`：检索已登记的影像元数据
