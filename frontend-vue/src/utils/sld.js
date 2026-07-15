// ==================== GeoServer SLD 样式生成 ====================
// ★ 对应原生 wms-render.js 的 _makeBoundarySLD
//   面透明填充 + 边界线描边 + name 字段文字标注（白色光晕 Halo）

// 入参: layerName 完整图层名(如 cd_shp:chengduqu), strokeColor 边界/文字色, showLabel 是否渲染 name 注记
// 方法: 生成 SLD 1.0 XML：LineSymbolizer(线) + PolygonSymbolizer(面透明填充+描边) + TextSymbolizer(name标注)
//   为什么同时包含 Line 和 Polygon 符号: 矢量图层可能同时含线要素(分割边缘 MultiLineString)
//   和面要素(变化图斑 MultiPolygon); GeoServer 按要素几何类型自动选匹配的符号化器,
//   若只放一种则另一种几何类型渲染为空瓦片。
// 出参: SLD XML 字符串
export function makeBoundarySLD(layerName, strokeColor = '#2c6fbd', showLabel = true) {
  const c = strokeColor
  const labelSymbolizer = showLabel
    ? '<TextSymbolizer>' +
      '<Label><ogc:PropertyName>name</ogc:PropertyName></Label>' +
      `<Fill><CssParameter name="fill">${c}</CssParameter></Fill>` +
      '<Font><CssParameter name="font-family">Microsoft YaHei, sans-serif</CssParameter>' +
      '<CssParameter name="font-size">12</CssParameter></Font>' +
      '<Halo><Fill><CssParameter name="fill">#ffffff</CssParameter></Fill>' +
      '<Radius><ogc:Literal>1</ogc:Literal></Radius></Halo>' +
      '</TextSymbolizer>'
    : ''
  return (
    '<?xml version="1.0" encoding="UTF-8"?>' +
    '<StyledLayerDescriptor version="1.0.0" xmlns="http://www.opengis.net/sld" xmlns:ogc="http://www.opengis.net/ogc">' +
    `<NamedLayer><Name>${layerName}</Name><UserStyle><FeatureTypeStyle><Rule>` +
    '<LineSymbolizer>' +
    `<Stroke><CssParameter name="stroke">${c}</CssParameter>` +
    '<CssParameter name="stroke-width">1</CssParameter></Stroke>' +
    '</LineSymbolizer>' +
    '<PolygonSymbolizer>' +
    '<Fill><CssParameter name="fill">#000000</CssParameter>' +
    '<CssParameter name="fill-opacity">0</CssParameter></Fill>' +
    `<Stroke><CssParameter name="stroke">${c}</CssParameter>` +
    '<CssParameter name="stroke-width">1</CssParameter></Stroke>' +
    '</PolygonSymbolizer>' +
    labelSymbolizer +
    '</Rule></FeatureTypeStyle></UserStyle></NamedLayer></StyledLayerDescriptor>'
  )
}
