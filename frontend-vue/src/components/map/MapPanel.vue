<script setup>
// ==================== 地图面板（OpenLayers 容器）====================
// ★ 对应原生 map/wms-render.js 的 initWmsMap
//   样式对齐原生 .wms-panel / .wms-stage / .ol-map-host
import { onMounted, onBeforeUnmount, nextTick, useTemplateRef } from 'vue'
import Map from 'ol/Map'
import View from 'ol/View'
import TileLayer from 'ol/layer/Tile'
import XYZ from 'ol/source/XYZ'
import { defaults as defaultControls, ScaleLine, MousePosition } from 'ol/control'
import { createStringXY } from 'ol/coordinate'
import { setMap, clearMap, loadDefaultLayers, setupFeatureInfo, setBasemap, getBasemapType } from '@/composables/useMap'
import 'ol/ol.css'

const mapRef = useTemplateRef('mapRef')
const popupRef = useTemplateRef('popupRef')
let map = null
let resizeObserver = null

// 底图切换选项（value 对应 useMap.js 的 TDT_BASEMAP_TYPES 键）
const basemapOptions = [
  { value: 'none', label: '无' },
  { value: 'img', label: '影像' },
  { value: 'vec', label: '矢量' },
  { value: 'ter', label: '地形' },
]
// 当前激活底图类型（响应式，来自 useMap 模块单例）
const currentBasemap = getBasemapType()
// 入参: type 底图类型
// 出参: 无 —— 调用 useMap.setBasemap 切换底图（移旧底图→加新底图）
const onSwitchBasemap = (type) => setBasemap(type)

onMounted(() => {
  // 入参: 无（target 取 ref DOM）
  // 方法: 初始化 OL Map，EPSG:4326 视图 + ScaleLine/MousePosition；底图由 useMap.loadDefaultLayers() 注入
  //   layers 留空：底图（天地图影像/注记）通过 loadDefaultLayers 按 VITE_TDT_KEY 注入，业务图层在其上
  // 出参: 无（map 实例供后续图层操作）
  map = new Map({
    target: mapRef.value,
    layers: [],
    view: new View({
      projection: 'EPSG:4326',
      center: [103.943905, 30.764575],
      zoom: 9,
    }),
    controls: defaultControls({ attribution: false }).extend([
      new ScaleLine(),
      new MousePosition({
        coordinateFormat: createStringXY(5),
        projection: 'EPSG:4326',
        undefinedHTML: '0, 0',
      }),
    ]),
  })

  setMap(map)
  // 默认加载矢量图层已禁用, 改为按需从图层面板手动加载
  loadDefaultLayers()
  setupFeatureInfo(popupRef.value)
  resizeObserver = new ResizeObserver(() => map?.updateSize())
  resizeObserver.observe(mapRef.value)

  // 入参: 无（操作 mapRef 内的 OL 控件 DOM）
  // 方法: 将经纬度(MousePosition)与比例尺(ScaleLine)两个 control 元素移入统一白底卡片 .ol-info-bar，
  //   由 flex 横向自动拼接；避免原先 right:150px 硬编码偏移在比例尺变宽时产生缝隙/重叠
  //   为什么用 nextTick + requestAnimationFrame: OL 控件在 setTarget 后异步渲染进 DOM，
  //   需等其挂载完成才能 querySelector 到 .ol-mouse-position/.ol-scale-line
  // 出参: 无
  nextTick(() => {
    requestAnimationFrame(() => {
      const host = mapRef.value
      const mousePos = host.querySelector('.ol-mouse-position')
      const scaleLine = host.querySelector('.ol-scale-line')
      if (!mousePos || !scaleLine) return
      const bar = document.createElement('div')
      bar.className = 'ol-info-bar'
      bar.appendChild(mousePos)
      bar.appendChild(scaleLine)
      host.appendChild(bar)
    })
  })
})

onBeforeUnmount(() => {
  resizeObserver?.disconnect()
  clearMap()
  map?.setTarget(undefined)
  map = null
})
</script>

<template>
  <div class="wms-panel shell-fade-in">
    <!-- 地图舞台（对齐原生 .wms-stage） -->
    <div class="wms-stage">
      <div ref="mapRef" class="ol-map-host"></div>
      <div ref="popupRef" class="wms-popup"></div>
      <!-- 底图切换按钮组（右上角） -->
      <div class="basemap-switcher">
        <button
          v-for="opt in basemapOptions"
          :key="opt.value"
          type="button"
          class="basemap-btn"
          :class="{ active: currentBasemap === opt.value }"
          :title="opt.value === 'none' ? '不显示底图' : `切换为${opt.label}底图`"
          @click="onSwitchBasemap(opt.value)"
        >{{ opt.label }}</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* ===== 地图面板（对齐原生 .wms-panel） ===== */
.wms-panel {
  flex: 1;
  min-width: 0;
  background: var(--bg-panel);
  padding: 10px;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  height: 100%;
}

/* ===== 地图舞台（对齐原生 .wms-stage） ===== */
.wms-stage {
  flex: 1;
  min-height: 0;
  overflow: hidden;
  position: relative;
  background: #fff;
  border-radius: var(--r-shell);
  box-shadow: var(--shadow-shell);
}

/* ===== OL 容器（对齐原生 .ol-map-host） ===== */
.ol-map-host {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
}
.ol-map-host :deep(.ol-zoom) {
  top: 10px;
  left: 10px;
  background: rgba(255, 255, 255, 0.92);
  border: 1px solid var(--border-strong);
  border-radius: var(--r-sm);
  padding: 2px;
  backdrop-filter: blur(8px);
}
.ol-map-host :deep(.ol-zoom button) {
  background: transparent;
  color: var(--text-secondary);
  font-size: var(--fs-xl);
  font-weight: 600;
  border-radius: var(--r-xs);
}
.ol-map-host :deep(.ol-zoom button:hover) {
  background: var(--accent-soft);
  color: var(--accent);
}
.ol-map-host :deep(.ol-attribution) {
  bottom: 4px;
  left: 4px;
  font-size: var(--fs-xs);
}
/* ===== 底图切换按钮组（右上角，紧凑白底卡片，风格对齐 .ol-info-bar）===== */
/* 入参: 无 | 方法: absolute 定位右上角，按钮 flex 横排；激活态用主题色填充 */
/* 出参: 一组紧凑按钮，激活项高亮区分当前底图 */
.basemap-switcher {
  position: absolute;
  top: 10px;
  right: 10px;
  display: flex;
  gap: 2px;
  background: #fff;
  padding: 2px;
  border: 1px solid var(--border-strong);
  border-radius: var(--r-sm);
  box-shadow: var(--shadow-shell);
  z-index: 10;
}
.basemap-btn {
  padding: 3px 10px;
  font-size: var(--fs-sm);
  color: var(--text-secondary);
  background: transparent;
  border: none;
  border-radius: var(--r-xs);
  cursor: pointer;
  transition: background 0.15s, color 0.15s;
}
.basemap-btn:hover {
  background: var(--accent-soft);
  color: var(--accent);
}
.basemap-btn.active {
  background: var(--accent);
  color: #fff;
  font-weight: 600;
}

/* ===== 经纬度 + 比例尺 统一白底卡片（flex 横向自动拼接）===== */
/* 入参: 无 | 方法: 父容器 absolute 定位右下角 + flex 子项并排；子控件去掉各自定位与背景回归文档流 */
/* 出参: 单张白底圆角卡片，宽度随比例尺/经纬度自适应，永不产生缝隙 */
.ol-map-host :deep(.ol-info-bar) {
  position: absolute;
  right: 8px;
  bottom: 8px;
  display: flex;
  align-items: center;
  gap: 10px;
  background: #fff;
  padding: 4px 10px;
  border: 1px solid var(--border-strong);
  border-radius: var(--r-sm);
  box-shadow: var(--shadow-shell);
  z-index: 10;
}
.ol-map-host :deep(.ol-mouse-position) {
  position: static;
  background: transparent;
  padding: 0;
  font-size: var(--fs-sm);
  font-family: var(--mono);
  color: var(--accent);
}
.ol-map-host :deep(.ol-scale-line) {
  position: static;
  background: transparent;
  padding: 0;
  color: var(--accent);
}
.ol-map-host :deep(.ol-scale-line-inner) {
  border-color: var(--accent);
  color: var(--accent);
}

</style>
