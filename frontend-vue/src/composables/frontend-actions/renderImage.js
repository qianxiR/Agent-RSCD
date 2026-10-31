// ==================== handler: render_image (完整版) ====================
// ★ 对应原生 renderImage (wms-render.js:355) 的 Step1-3
//   三步: 底图(WMS) + 面图斑(WFS) + 图例浮层
//   wait_for_result=False, 但本方案统一回传以保持协议一致
//
// await 语义(对齐原生): 仅 await 底图成败 → status; 面图斑/图例 fire-and-forget

// 入参: data = { base_layer{layer_name,workspace,wms_url}, caption, image_url, legend, bbox?,
//                polygon_layer{layer_name,workspace,bbox,wms_url}? }
//      ctx.map = useMap() 图层操作能力(含 addWmsLayer/addWfsLayer/setLegend)
// 方法: Step1 底图 await → Step2 矢量叠加(fire-and-forget)
// 出参: { status, loaded, error?, image_url }
export async function handleRenderImage(data, ctx) {
  const { base_layer, polygon_layer, caption } = data

  // Step1 底图(必须 await, status 据此)
  const result = loadBase(base_layer)
  if (!result) {
    // 框选解译等场景不叠加 GeoServer 原图 WMS：直接叠加本地捕获影像（带 CRS），
    // 保证矢量结果与“原始影像”严格同源、同 transform，消除配准偏移。
    if (data.input_image_path) {
      ctx.map.addStaticImageLayer({ path: data.input_image_path }).catch(() => {})
    }
    if (polygon_layer?.layer_name) {
      ctx.map.addWfsLayer({
        name: polygon_layer.layer_name,
        workspace: polygon_layer.workspace,
        bbox: polygon_layer.bbox,
        forceFit: false,
      })
    }
    const hasVector = !!polygon_layer?.layer_name
    return {
      status: hasVector ? 'success' : 'error',
      loaded: hasVector,
      error: hasVector ? null : '原始影像未发布到 GeoServer',
      image_url: data.image_url,
    }
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
