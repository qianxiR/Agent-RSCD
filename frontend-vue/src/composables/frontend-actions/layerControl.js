// ==================== handler: layer_control ====================
// ★ 对应原生 renderLayerControl (wms-render.js:271)
//   显示/隐藏/切换单个图层; wait_for_result=True, 必须回传 tool_result

// 入参: data = { layer_name, workspace, action: 'show'|'hide'|'toggle', bbox, wms_url, layer_type? }
//      ctx.map = useMap() 图层操作能力(addWmsLayer/addWfsLayer/removeLayer/hasLayer)
// 方法: 按 action 分发 —— hide 或 toggle(已存在) 移除图层; show 或 toggle(未存在) 叠加
//   ★ 矢量走 WFS (VectorLayer 客户端渲染), 栅格走 WMS
// 出参: { layer_name, action, status, visible, error? }
export async function handleLayerControl(data, ctx) {
  const { layer_name, workspace, action, bbox, layer_type } = data
  const map = ctx.map
  const exists = map.hasLayer(layer_name, workspace)

  // 已存在 + (hide 或 toggle) → 移除
  if (action === 'hide' || (action === 'toggle' && exists)) {
    map.removeLayer(layer_name, workspace)
    return { layer_name, action, status: 'success', visible: false }
  }

  // show 或 toggle(未存在) → 叠加
  const ltype = layer_type || 'unknown'
  if (ltype === 'vector') {
    const wfsResult = await map.addWfsLayer({ name: layer_name, workspace, bbox, forceFit: true })
    return {
      layer_name,
      action,
      status: wfsResult.loaded ? 'success' : 'error',
      visible: wfsResult.loaded,
      error: wfsResult.error || null,
    }
  }

  const r = map.addWmsLayer({ name: layer_name, workspace, bbox, layerType: ltype })
  return {
    layer_name,
    action,
    status: r.loaded ? 'success' : 'error',
    visible: r.loaded,
    error: r.error || null,
  }
}
