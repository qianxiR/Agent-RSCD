// ==================== handler: layer_locate ====================
// ★ 对应原生 locateLayerFromPanel → showWmsInPanel(forceFit:true)
//   定位到图层范围; wait_for_result=True, 必须回传 tool_result
//   后端 layer_locate 的 params 不含 workspace, 但 layer_name 可能是 "ws:name" 全名 → 此处解析

// 入参: data = { layer_id, layer_name, bbox, workspace?, layer_type? }
//      ctx.map = useMap() 图层操作能力
// 方法: 解析 "ws:name" 全名 → 矢量走 WFS, 栅格走 WMS, bbox 定位
// 出参: { layer_id, status, loaded, error? }
export async function handleLayerLocate(data, ctx) {
  const { layer_id, layer_name, bbox, layer_type } = data
  // 解析全名: "ws:name" → workspace + name; 裸名则用透传 workspace
  const fullName = layer_name || layer_id
  const hasWs = typeof fullName === 'string' && fullName.includes(':')
  const workspace = data.workspace || (hasWs ? fullName.split(':')[0] : '')
  const name = hasWs ? fullName.split(':')[1] : fullName

  const ltype = layer_type || 'unknown'

  if (ltype === 'vector') {
    const r = await ctx.map.addWfsLayer({ name, workspace, bbox, forceFit: true })
    return { layer_id, status: r.loaded ? 'success' : 'error', loaded: r.loaded, error: r.error || null }
  }

  const r = ctx.map.addWmsLayer({ name, workspace, bbox, layerType: ltype })
  return { layer_id, status: r.loaded ? 'success' : 'error', loaded: r.loaded, error: r.error || null }
}
