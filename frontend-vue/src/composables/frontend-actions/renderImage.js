// ==================== handler: render_image (完整版) ====================
// ★ 对应原生 renderImage (wms-render.js:355) 的 Step1-4
//   四步: 底图(WMS) + 变化面(WFS) + 边缘线(WFS) + 图例浮层
//   wait_for_result=False, 但本方案统一回传以保持协议一致
//
// await 语义(对齐原生): 仅 await 底图成败 → status; 变化面/边缘线/图例 fire-and-forget

// 入参: data = { base_layer{layer_name,workspace,wms_url}, caption, image_url, legend, bbox?,
//                polygon_layer{layer_name,workspace,bbox,wms_url}?, edge_layer{...}? }
//      ctx.map = useMap() 图层操作能力(含 addWmsLayer/addWfsLayer/setLegend)
// 方法: Step1 底图 await → Step2/3 矢量叠加(fire-and-forget)
// 出参: { status, loaded, error?, image_url }
export async function handleRenderImage(data, ctx) {
  const { base_layer, polygon_layer, edge_layer, caption } = data

  // Step1 底图(必须 await, status 据此)
  const result = loadBase(base_layer)
  if (!result) {
    return { status: 'error', loaded: false, error: '原始影像未发布到 GeoServer', image_url: data.image_url }
  }
  const r = ctx.map.addWmsLayer({
    name: result.name,
    workspace: result.workspace,
    bbox: data.bbox,
    layerType: 'raster',
  })

  // Step2 变化面(fire-and-forget, forceFit:false 避免抢底图视角)
  if (polygon_layer && polygon_layer.layer_name) {
    ctx.map.addWfsLayer({
      name: polygon_layer.layer_name,
      workspace: polygon_layer.workspace,
      bbox: polygon_layer.bbox,
      forceFit: false,
    })
  }
  // Step3 边缘线(fire-and-forget)
  if (edge_layer && edge_layer.layer_name) {
    ctx.map.addWfsLayer({
      name: edge_layer.layer_name,
      workspace: edge_layer.workspace,
      bbox: edge_layer.bbox,
      forceFit: false,
    })
  }

  return {
    status: r.loaded ? 'success' : 'error',
    loaded: r.loaded,
    error: r.error || null,
    image_url: data.image_url,
  }
}

// 入参: baseLayer(data.base_layer)
// 出参: {name, workspace} 或 null(底图未发布)
function loadBase(baseLayer) {
  if (!baseLayer || !baseLayer.layer_name) return null
  return { name: baseLayer.layer_name, workspace: baseLayer.workspace }
}
