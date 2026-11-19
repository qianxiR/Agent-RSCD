// ==================== 样本标注地图编辑器 ====================
// 把 sandbox/sample-ui 的绘制 / 顶点 / 框选交互移植到工作台地图：
// 形状几何以影像像素坐标存储（与沙盒一致），渲染与命中检测经 3857 extent 换算。
import { watch } from 'vue'
import Feature from 'ol/Feature'
import Polygon from 'ol/geom/Polygon'
import Point from 'ol/geom/Point'
import LineString from 'ol/geom/LineString'
import VectorLayer from 'ol/layer/Vector'
import VectorSource from 'ol/source/Vector'
import Style from 'ol/style/Style'
import Fill from 'ol/style/Fill'
import Stroke from 'ol/style/Stroke'
import CircleStyle from 'ol/style/Circle'
import Modify from 'ol/interaction/Modify'
import DragBox from 'ol/interaction/DragBox'
import DoubleClickZoom from 'ol/interaction/DoubleClickZoom'
import { always } from 'ol/events/condition'
import { useSampleStudio } from '@/composables/useSampleStudio'

const VERTEX_TOL = 8
const EDGE_TOL = 8

const {
  active,
  frame,
  instances,
  selectedId,
  selectedIds,
  selectedVertex,
  mode,
  addShape,
  selectShape,
  selectMany,
  removeShape,
  removeSelected,
  markDirty,
  classOf,
  pixelToCoord,
  coordToPixel,
} = useSampleStudio()

let map = null
let shapeSource = null
let vertexSource = null
let draftSource = null
let shapeLayer = null
let vertexLayer = null
let draftLayer = null
let modify = null
let dragBox = null
let doubleClickZoom = null
const draft = { points: [], cursor: null }

function findShape(id) {
  return instances.value.find((item) => item.id === id) || null
}

function classColor(classId) {
  return classOf(classId).color
}

function isSelected(id) {
  return id === selectedId.value || selectedIds.value.includes(id)
}

function pointInPolygon(px, py, points) {
  let inside = false
  for (let i = 0, j = points.length - 1; i < points.length; j = i, i += 1) {
    const [xi, yi] = points[i]
    const [xj, yj] = points[j]
    if (((yi > py) !== (yj > py))
      && (px < ((xj - xi) * (py - yi)) / ((yj - yi) || 1e-9) + xi)) inside = !inside
  }
  return inside
}

function distToSegment(px, py, ax, ay, bx, by) {
  const abx = bx - ax
  const aby = by - ay
  const length2 = abx * abx + aby * aby || 1
  let t = ((px - ax) * abx + (py - ay) * aby) / length2
  t = Math.max(0, Math.min(1, t))
  return {
    dist: Math.hypot(px - (ax + abx * t), py - (ay + aby * t)),
    x: ax + abx * t,
    y: ay + aby * t,
  }
}

function ringOf(points) {
  const ring = points.map(([x, y]) => pixelToCoord(x, y))
  if (ring.length) ring.push(ring[0])
  return ring
}

// ==================== 渲染 ====================

function rebuildShapes() {
  if (!shapeSource || !frame.value) return
  shapeSource.clear()
  instances.value.forEach((shape) => {
    if (shape.points.length < 3) return
    shapeSource.addFeature(new Feature({
      geometry: new Polygon([ringOf(shape.points)]),
      shapeId: shape.id,
      classId: shape.classId,
    }))
  })
}

function rebuildVertices() {
  if (!vertexSource || !frame.value) return
  vertexSource.clear()
  const targets = []
  const single = findShape(selectedId.value)
  if (mode.value === 'edit' && single) targets.push(single)
  selectedIds.value.forEach((id) => {
    const shape = findShape(id)
    if (shape && !targets.includes(shape)) targets.push(shape)
  })
  targets.forEach((shape) => {
    shape.points.forEach(([x, y], index) => {
      vertexSource.addFeature(new Feature({
        geometry: new Point(pixelToCoord(x, y)),
        shapeId: shape.id,
        vertexIndex: index,
      }))
    })
  })
}

function rebuildDraft() {
  if (!draftSource || !frame.value) return
  draftSource.clear()
  if (!draft.points.length) return
  const preview = draft.cursor ? draft.points.concat([draft.cursor]) : draft.points
  const ring = preview.map(([x, y]) => pixelToCoord(x, y))
  if (ring.length >= 3) {
    draftSource.addFeature(new Feature({
      geometry: new Polygon([ring.concat([ring[0]])]),
    }))
  } else if (ring.length === 2) {
    draftSource.addFeature(new Feature({ geometry: new LineString(ring) }))
  }
  draft.points.forEach(([x, y]) => {
    draftSource.addFeature(new Feature({ geometry: new Point(pixelToCoord(x, y)) }))
  })
}

// ==================== 命中检测 ====================

function hitVertex(eventPixel) {
  const shape = findShape(selectedId.value)
  if (!shape) return null
  let best = null
  shape.points.forEach(([x, y], index) => {
    const screen = map.getPixelFromCoordinate(pixelToCoord(x, y))
    const dist = Math.hypot(screen[0] - eventPixel[0], screen[1] - eventPixel[1])
    if (dist <= VERTEX_TOL && (!best || dist < best.dist)) best = { index, dist }
  })
  return best
}

function hitEdge(eventPixel, shape) {
  if (!shape) return null
  const coordinate = map.getCoordinateFromPixel(eventPixel)
  const [cx, cy] = coordToPixel(coordinate)
  const size = map.getSize() || [frame.value?.width || 1, 1]
  const scale = frame.value ? frame.value.width / (size[0] || frame.value.width) : 1
  let best = null
  for (let index = 0; index < shape.points.length; index += 1) {
    const [ax, ay] = shape.points[index]
    const [bx, by] = shape.points[(index + 1) % shape.points.length]
    const info = distToSegment(cx, cy, ax, ay, bx, by)
    if (info.dist <= EDGE_TOL * scale && (!best || info.dist < best.dist)) {
      best = { index, dist: info.dist, x: info.x, y: info.y }
    }
  }
  return best
}

function hitShape(eventPixel) {
  const coordinate = map.getCoordinateFromPixel(eventPixel)
  const [cx, cy] = coordToPixel(coordinate)
  for (let index = instances.value.length - 1; index >= 0; index -= 1) {
    if (pointInPolygon(cx, cy, instances.value[index].points)) return instances.value[index]
  }
  return null
}

function eventPixelToImage(event) {
  return coordToPixel(event.coordinate)
}

function insideFrame([x, y]) {
  const current = frame.value
  if (!current) return false
  return x >= 0 && y >= 0 && x <= current.width && y <= current.height
}

// ==================== 交互 ====================

function finishDraft() {
  const points = draft.points.slice()
  draft.points = []
  draft.cursor = null
  rebuildDraft()
  if (points.length < 3) return false
  addShape(points)
  return true
}

function deleteVertex(shape, index) {
  if (shape.points.length <= 3) return false
  shape.points.splice(index, 1)
  shape.edited = true
  selectedVertex.value = null
  rebuildShapes()
  rebuildVertices()
  markDirty()
  return true
}

function drawClick(event) {
  const pixel = eventPixelToImage(event)
  if (!insideFrame(pixel)) return
  if (!draft.points.length) {
    draft.points = [[pixel[0], pixel[1]]]
    rebuildDraft()
    return
  }
  const first = draft.points[0]
  const firstScreen = map.getPixelFromCoordinate(pixelToCoord(first[0], first[1]))
  if (draft.points.length >= 3
    && Math.hypot(firstScreen[0] - event.pixel[0], firstScreen[1] - event.pixel[1]) <= VERTEX_TOL) {
    finishDraft()
    return
  }
  draft.points.push([pixel[0], pixel[1]])
  rebuildDraft()
}

function editClick(event) {
  const vertex = hitVertex(event.pixel)
  if (vertex) {
    const shape = findShape(selectedId.value)
    if (!shape) return
    if (event.originalEvent?.shiftKey) {
      deleteVertex(shape, vertex.index)
      return
    }
    selectedVertex.value = { shapeId: shape.id, index: vertex.index }
    rebuildVertices()
    return
  }
  const shape = findShape(selectedId.value)
  const edge = shape ? hitEdge(event.pixel, shape) : null
  if (edge) {
    shape.points.splice(edge.index + 1, 0, [edge.x, edge.y])
    shape.edited = true
    selectedVertex.value = { shapeId: shape.id, index: edge.index + 1 }
    rebuildShapes()
    rebuildVertices()
    markDirty()
    return
  }
  const hit = hitShape(event.pixel)
  selectShape(hit ? hit.id : '')
  rebuildShapes()
  rebuildVertices()
}

function onClick(event) {
  if (!active.value || !frame.value || mode.value === 'range') return
  if (mode.value === 'draw') {
    drawClick(event)
    return
  }
  if (mode.value === 'edit') {
    editClick(event)
    return
  }
  const shape = hitShape(event.pixel)
  selectShape(shape ? shape.id : '')
  selectedIds.value = []
  rebuildShapes()
  rebuildVertices()
}

function onPointerMove(event) {
  if (mode.value !== 'draw' || !draft.points.length) return
  const pixel = eventPixelToImage(event)
  draft.cursor = [pixel[0], pixel[1]]
  rebuildDraft()
}

function onModifyEnd(event) {
  event.features.forEach((feature) => {
    const shape = findShape(feature.get('shapeId'))
    const index = feature.get('vertexIndex')
    if (!shape || index == null) return
    const [rawX, rawY] = coordToPixel(feature.getGeometry().getCoordinates())
    if (!Number.isFinite(rawX) || !Number.isFinite(rawY)) return
    const x = Math.max(0, Math.min(frame.value.width, rawX))
    const y = Math.max(0, Math.min(frame.value.height, rawY))
    shape.points[index] = [Math.round(x * 10) / 10, Math.round(y * 10) / 10]
    shape.edited = true
    rebuildShapes()
    rebuildVertices()
    markDirty()
  })
}

function shapeInRect(shape, rect) {
  const [rx0, ry0, rx1, ry1] = rect
  if (shape.points.some(([x, y]) => x >= rx0 && x <= rx1 && y >= ry0 && y <= ry1)) return true
  return [[rx0, ry0], [rx1, ry0], [rx1, ry1], [rx0, ry1]]
    .some(([x, y]) => pointInPolygon(x, y, shape.points))
}

function onBoxEnd() {
  const extent = dragBox.getGeometry().getExtent()
  const [x0, y0] = coordToPixel([extent[0], extent[3]])
  const [x1, y1] = coordToPixel([extent[2], extent[1]])
  const rect = [Math.min(x0, x1), Math.min(y0, y1), Math.max(x0, x1), Math.max(y0, y1)]
  const picked = instances.value.filter((shape) => shapeInRect(shape, rect))
  selectMany(picked.map((shape) => shape.id))
  rebuildShapes()
  rebuildVertices()
}

// ==================== 模式 ====================

function setMode(next) {
  mode.value = next
  draft.points = []
  draft.cursor = null
  selectedVertex.value = null
  rebuildDraft()
  modify?.setActive(next === 'edit')
  dragBox?.setActive(next === 'select')
  rebuildVertices()
  if (map) map.render()
}

// 入参: 键盘事件 | 方法: F2 / Enter / Backspace / Esc / Delete | 出参: 是否已处理
function onKeydown(event) {
  if (!active.value) return false
  if (mode.value === 'range') return false
  const tag = event.target?.tagName
  if (tag === 'INPUT' || tag === 'TEXTAREA' || event.target?.isContentEditable) return false
  const key = event.key

  if (key === 'F2') {
    if (mode.value === 'draw' && draft.points.length) {
      event.preventDefault()
      finishDraft()
      return true
    }
    if (mode.value === 'edit' && selectedVertex.value) {
      const vertex = selectedVertex.value
      const shape = findShape(vertex.shapeId)
      if (shape && deleteVertex(shape, vertex.index)) {
        event.preventDefault()
        return true
      }
    }
    return false
  }

  if (mode.value === 'draw' && draft.points.length) {
    if (key === 'Backspace') {
      event.preventDefault()
      draft.points.pop()
      if (!draft.points.length) draft.cursor = null
      rebuildDraft()
      return true
    }
    if (key === 'Enter') {
      event.preventDefault()
      finishDraft()
      return true
    }
    if (key === 'Escape') {
      event.preventDefault()
      draft.points = []
      draft.cursor = null
      rebuildDraft()
      setMode('browse')
      return true
    }
  }

  if (key === 'Escape') {
    setMode('browse')
    return true
  }

  if (key === 'Delete' || key === 'Backspace') {
    if (mode.value === 'edit' && selectedVertex.value) {
      const vertex = selectedVertex.value
      const shape = findShape(vertex.shapeId)
      if (shape && deleteVertex(shape, vertex.index)) {
        event.preventDefault()
        return true
      }
      return false
    }
    const removed = removeSelected()
    if (removed) {
      event.preventDefault()
      rebuildShapes()
      rebuildVertices()
      return true
    }
  }
  return false
}

// ==================== 生命周期 ====================

function styleShape(feature) {
  const rgb = classColor(feature.get('classId'))
  const selected = isSelected(feature.get('shapeId'))
  const color = `rgb(${rgb.join(',')})`
  return [
    new Style({
      stroke: new Stroke({ color: '#ffffff', width: selected ? 5 : 3 }),
    }),
    new Style({
      fill: new Fill({ color: `rgba(${rgb.join(',')},${selected ? 0.3 : 0.18})` }),
      stroke: new Stroke({ color, width: selected ? 2.5 : 1.5 }),
    }),
  ]
}

// 入参: OL Map | 方法: 创建样本图层与交互并挂到主图 | 出参: 无
function enter(nextMap) {
  if (!nextMap) return
  map = nextMap

  shapeSource = new VectorSource()
  vertexSource = new VectorSource()
  draftSource = new VectorSource()

  shapeLayer = new VectorLayer({ source: shapeSource, style: styleShape, zIndex: 40 })
  vertexLayer = new VectorLayer({
    source: vertexSource,
    zIndex: 45,
    style: (feature) => {
      const selected = selectedVertex.value
        && selectedVertex.value.shapeId === feature.get('shapeId')
        && selectedVertex.value.index === feature.get('vertexIndex')
      return new Style({
        image: new CircleStyle({
          radius: selected ? 6 : 5,
          fill: new Fill({ color: selected ? '#1a1a18' : 'rgba(255,255,255,0.95)' }),
          stroke: new Stroke({ color: '#1a1a18', width: selected ? 2 : 1.5 }),
        }),
      })
    },
  })
  draftLayer = new VectorLayer({
    source: draftSource,
    zIndex: 50,
    style: [
      new Style({
        stroke: new Stroke({ color: 'rgba(255,255,255,0.95)', width: 4, lineDash: [5, 4] }),
      }),
      new Style({
        fill: new Fill({ color: 'rgba(26,26,24,0.10)' }),
        stroke: new Stroke({ color: '#1a1a18', width: 1.5, lineDash: [5, 4] }),
        image: new CircleStyle({
          radius: 4,
          fill: new Fill({ color: '#1a1a18' }),
          stroke: new Stroke({ color: '#ffffff', width: 1.5 }),
        }),
      }),
    ],
  })

  map.addLayer(shapeLayer)
  map.addLayer(vertexLayer)
  map.addLayer(draftLayer)

  modify = new Modify({ source: vertexSource, pixelTolerance: 8, insertVertexCondition: () => false })
  modify.on('modifyend', onModifyEnd)
  modify.setActive(false)
  map.addInteraction(modify)

  dragBox = new DragBox({ condition: always })
  dragBox.on('boxend', onBoxEnd)
  dragBox.setActive(false)
  map.addInteraction(dragBox)

  // 标注期间禁用双击缩放，避免闭合/编辑时误触发
  doubleClickZoom = map.getInteractions().getArray()
    .find((interaction) => interaction instanceof DoubleClickZoom) || null
  if (doubleClickZoom) map.removeInteraction(doubleClickZoom)

  map.on('click', onClick)
  map.on('pointermove', onPointerMove)

  rebuildShapes()
  rebuildVertices()
}

function exit() {
  if (map) {
    map.un('click', onClick)
    map.un('pointermove', onPointerMove)
    if (modify) map.removeInteraction(modify)
    if (dragBox) map.removeInteraction(dragBox)
    if (doubleClickZoom) map.addInteraction(doubleClickZoom)
    if (shapeLayer) map.removeLayer(shapeLayer)
    if (vertexLayer) map.removeLayer(vertexLayer)
    if (draftLayer) map.removeLayer(draftLayer)
  }
  shapeSource = null
  vertexSource = null
  draftSource = null
  shapeLayer = null
  vertexLayer = null
  draftLayer = null
  modify = null
  dragBox = null
  doubleClickZoom = null
  draft.points = []
  draft.cursor = null
  map = null
}

// 实例 / 选中 / 模式变化时重绘
watch(
  [instances, selectedId, selectedIds, selectedVertex, mode],
  () => {
    if (!active.value) return
    rebuildShapes()
    rebuildVertices()
  },
  { deep: true },
)

export function useSampleEditor() {
  return { enter, exit, setMode, onKeydown, removeShape, removeSelected }
}
