# Vendored Tools 子集

本目录是从 [qianxiR/Tools](https://github.com/qianxiR/Tools) 复制的**最小代码子集**，用于让系统在目标机器上不依赖 `E:\1代码\系统\Tools` 也能运行相关工具。

## 来源

- 仓库：`https://github.com/qianxiR/Tools`
- Commit：`73451d80d98b6e1fb853616d3952a3ca6f3250d1`
- 复制日期：2026-11-01

## 包含

```text
raster_io.py                 GeoTIFF I/O 与地理信息
spectral/                    NDVI/NDWI 等光谱指数
spatial/                     buffer / intersection / erase / 几何工具
mask_postprocess/            掩码后处理与栅格转矢量（仅必要模块）
knowledge/                   KnowledgeRAG 知识条目、案例、工具说明（md）
```

## 不包含

- 模型权重（`*.pt` 等）
- 栅格/矢量数据（`*.tif` 等）
- 训练、推理、GEE、数据集生成代码
- `crf_postpro`、`remove_small_object`、`post_precess`、`combine_different_class`、`simplify_polygon` 等未被当前工具链引用的模块

## 修改

只把原包的绝对导入改为相对导入：

```python
from Tools.DataProcessing.spectral.indices import ...
# ->
from .indices import ...
```

业务逻辑未修改。

## 依赖

- `raster_io`：numpy、GDAL（`osgeo`）、rasterio
- `spectral`：numpy、pandas
- `spatial`：shapely
- `mask_postprocess`：numpy、opencv-python、scipy、geopandas、rasterio、shapely、GDAL
