# OL 10.x WMS 源对极小范围图层 tileUrlFunction 返回 undefined

## 现象

图层面板勾选矢量图层后地图定位到图层范围正确，但图层内容不可见。控制台日志：

```
首个瓦片URL: (无URL)
瓦片加载失败 ✗ url=undefined
```

## 触发条件

- OpenLayers ≥ 9.x（测试了 9.2.4 CDN 和 10.9.0 npm），`TileWMS` / `ImageWMS` 均复现
- 图层范围极小（如 0.01°≈1km），`fitExtent` 后 zoom 较高
- `VERSION=1.1.1` + `TILED=true` 组合下，OL 内部 tileGrid 无法正确映射 tileCoord → bbox

## 根因

OL 10.x 的 WMS source（TileWMS / ImageWMS）在以下时序下 `tileUrlFunction` 返回 `undefined`：

1. `addLayer` 时 view 仍在成都全局（zoom 9）
2. OL 尝试按当前 view 生成瓦片坐标 → 0.01° 范围在 zoom 9 下映射不出有效 tileCoord
3. `tileUrlFunction` 返回 `undefined` → 所有瓦片进入 `tileloaderror(url=undefined)`
4. 后续 `fitExtent` 把 view 移到目标范围，但已失败的瓦片不会被重新生成

即使改为「先 fit 后 addLayer」顺序，`ImageWMS` 也同样出现 `imageloaderror` 且 `src` 为空。

## 原生版（frontend/）为何不受影响

原生版 `vendor/ol.js`（约 v9 之前的版本）TileWMS 行为不同。

更关键的是：**原生版对 AI 分割结果的矢量图层用 `showWfsInPanel`（WFS VectorLayer）**，不走 WMS 渲染管道。AI 首次加载正常，而图层面板勾选走的是 `showWmsInPanel`（WMS）——原生版 OL 老版本侥幸不受影响，Vue 版 OL 10.x 暴露此问题。

## 修复

图层面板对 `type === 'vector'` 的图层改用 `addWfsLayer`（WFS → VectorLayer），与 AI 首次加载路径、原生版 `showWfsInPanel` 统一。栅格图层仍走 WMS。

### 改动文件

| 文件 | 改动 |
|------|------|
| `LayerPanel.vue` | `onToggleVisible` / `onLocate` 对矢量调用 `addWfsLayer` |
| `layerControl.js` | 矢量分支 `await addWfsLayer` |
| `layerDisplay.js` | 矢量分支 `await addWfsLayer` |
| `layerLocate.js` | 矢量分支 `await addWfsLayer` |
| `useMap.js` | 移除 `addWmsLayer` 内部 SLD 自动注入；先 fit 后 addLayer 顺序 |

### 核心原则

> 矢量数据 → WFS VectorLayer（客户端 `_vectorStyle` 渲染）  
> 栅格数据 → WMS TileLayer（服务端 GeoServer 渲染）

## 验证

- `cd_shp:chengduqu` / `chengdushi`（大范围边界线）：WMS TileWMS + SLD 正常
- `samseg:0865e300__seg_9k9x`（面，MultiPolygon）：WFS VectorLayer 正常
- `samseg:0865e300__seg_9k9x_edge`（线，MultiLineString）：WFS VectorLayer 正常
- 关闭后重新勾选：正常（`removeLayer` 先清理残留再重建）

## 日期

2026-06-27
