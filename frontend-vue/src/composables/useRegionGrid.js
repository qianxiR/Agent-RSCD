// ==================== 样本区域：红色边界 + 正方形格网 ====================
// 框选后一直保留到退出标注：红边标出取值范围，内部按长边固定等分成正方形格网，
// 格宽由框选面积自动算出，不写死。
import Feature from 'ol/Feature'
import Polygon from 'ol/geom/Polygon'
import LineString from 'ol/geom/LineString'
import VectorLayer from 'ol/layer/Vector'
import VectorSource from 'ol/source/Vector'
import Style from 'ol/style/Style'
import Stroke from 'ol/style/Stroke'
import Fill from 'ol/style/Fill'
import { transformExtent } from 'ol/proj'
import { getMap } from '@/composables/useMap'

// 长边固定等分格数
const GRID_CELLS = 10
const LAYER_NAME = 'sample:region-grid'

const boundStyle = new Style({
  stroke: new Stroke({ color: 'rgba(181, 74, 74, 0.95)', width: 2 }),
  fill: new Fill({ color: 'rgba(181, 74, 74, 0.035)' }),
})
const gridStyle = new Style({
  stroke: new Stroke({ color: 'rgba(181, 74, 74, 0.5)', width: 1 }),
})

let layer = null

function ensureLayer(map) {
  if (layer && map.getLayers().getArray().includes(layer)) return layer
  layer = new VectorLayer({
    source: new VectorSource(),
    zIndex: 30,
    style: (feature) => (feature.get('role') === 'grid' ? gridStyle : boundStyle),
  })
  layer.set('layerName', LAYER_NAME)
  layer.set('workspace', 'sample-region')
  map.addLayer(layer)
  return layer
}

// 入参: bbox EPSG:4326, cells 长边格数 | 方法: 逐格生成边界与格线（3857 下正方形） | 出参: 格宽（米）
export function showRegionGrid(bbox, { cells = GRID_CELLS } = {}) {
  const map = getMap()
  if (!map || !bbox?.length) return 0
  const [minx, miny, maxx, maxy] = transformExtent(bbox, 'EPSG:4326', map.getView().getProjection())
  const width = maxx - minx
  const height = maxy - miny
  if (!(width > 0) || !(height > 0)) return 0

  // 格宽按长边等分得出，短边自然少几格，剩余部分不足一格
  const unit = Math.max(width, height) / Math.max(1, cells)
  const features = [
    new Feature({
      role: 'bound',
      geometry: new Polygon([[
        [minx, miny], [maxx, miny], [maxx, maxy], [minx, maxy], [minx, miny],
      ]]),
    }),
  ]
  for (let x = minx + unit; x < maxx - 1e-6; x += unit) {
    features.push(new Feature({ role: 'grid', geometry: new LineString([[x, miny], [x, maxy]]) }))
  }
  for (let y = miny + unit; y < maxy - 1e-6; y += unit) {
    features.push(new Feature({ role: 'grid', geometry: new LineString([[minx, y], [maxx, y]]) }))
  }

  const target = ensureLayer(map)
  target.getSource().clear()
  target.getSource().addFeatures(features)
  return unit
}

// 入参: 无 | 方法: 移除红框与格网 | 出参: 无
export function clearRegionGrid() {
  const map = getMap()
  if (layer && map) map.removeLayer(layer)
  layer = null
}

export function useRegionGrid() {
  return { showRegionGrid, clearRegionGrid }
}
