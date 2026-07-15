// ==================== handler: layer_group_control ====================
// ★ 批量操作图层组; wait_for_result=True, 必须回传 tool_result
//   按 group_name 匹配已加载的业务图层(匹配 workspace 或 layerName 前缀)

// 入参: data = { group_name, action: 'show'|'hide' }
//      ctx.map = useMap() 图层操作能力
// 方法: hide → 按 name+workspace 调 useMap.removeLayer 移除匹配图层;
//      show → 将匹配图层设为可见(仅对已加载图层生效)
// 出参: { group_name, action, status, affected, error? }
export async function handleLayerGroupControl(data, ctx) {
  const { group_name, action } = data
  const map = ctx.map.getMap && ctx.map.getMap()
  if (!map) {
    return { group_name, action, status: 'error', affected: 0, error: '地图未初始化' }
  }

  // 收集匹配的业务图层(前缀命中 workspace 或 layerName), 记下 name+workspace 供 removeLayer 使用
  const matched = []
  map.getLayers().forEach((layer) => {
    if (!layer.get('business')) return
    const ws = layer.get('workspace') || ''
    const name = layer.get('layerName') || ''
    if (ws.startsWith(group_name) || name.startsWith(group_name)) matched.push({ layer, name, ws })
  })

  if (action === 'hide') {
    matched.forEach((m) => ctx.map.removeLayer(m.name, m.ws))
  } else {
    // show: 仅对已加载图层设为可见(MVP; 未加载图层需另行发现, 暂不支持)
    matched.forEach((m) => m.layer.setVisible(true))
  }
  return { group_name, action, status: 'success', affected: matched.length }
}
