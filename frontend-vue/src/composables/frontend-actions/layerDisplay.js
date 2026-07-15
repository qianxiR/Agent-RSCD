// ==================== handler: layer_display ====================
// ★ 对应原生 renderLayerDisplay (wms-render.js:296)
//   加载图层到地图(可带统计信息); wait_for_result=True, 必须回传 tool_result

// 入参: data = { layer_name, workspace, type, bbox, statistics, wms_url }
//      ctx.map = useMap() 图层操作能力
// 方法: 矢量走 WFS (VectorLayer 客户端渲染, 与原生 showWfsInPanel 一致), 栅格走 WMS
// 出参: { layer_name, status, loaded, error? }
export async function handleLayerDisplay(data, ctx) {
  const { layer_name, workspace, bbox, type } = data
  const ltype = type || 'unknown'

  // ★ 矢量: WFS (VectorLayer, 客户端 _vectorStyle 渲染, 不依赖 GeoServer WMS)
  if (ltype === 'vector') {
    const wfsResult = await ctx.map.addWfsLayer({ name: layer_name, workspace, bbox, forceFit: true })
    return {
      layer_name,
      status: wfsResult.loaded ? 'success' : 'error',
      loaded: wfsResult.loaded,
      error: wfsResult.error || null,
    }
  }

  // 栅格: WMS (TileWMS)
  const r = ctx.map.addWmsLayer({
    name: layer_name,
    workspace,
    bbox,
    layerType: ltype,
  })
  return {
    layer_name,
    status: r.loaded ? 'success' : 'error',
    loaded: r.loaded,
    error: r.error || null,
  }
}
