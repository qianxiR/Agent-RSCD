<script setup>
// ==================== 图例浮层 ====================
// ★ 对应原生 _renderLegendOverlay (wms-render.js:1060) 的响应式复刻
//   挂在 .wms-stage 右上角, SamSeg 分割/变化检测结果的颜色图例
//   legend 项结构: { name: '建筑', hex: '#ff0000' }
defineProps({
  title: { type: String, default: '图例' },
  items: { type: Array, default: () => [] },
})
</script>

<template>
  <div class="seg-legend-overlay">
    <div class="legend-title">{{ title }}</div>
    <span v-for="(it, i) in items" :key="i" class="legend-chip">
      <span class="legend-swatch" :style="{ background: it.hex }"></span>
      {{ it.name }}
    </span>
  </div>
</template>

<style scoped>
/* 对齐原生 .seg-legend-overlay: 悬浮在地图右上角 */
.seg-legend-overlay {
  position: absolute;
  top: 10px;
  right: 10px;
  z-index: 5;
  display: flex;
  flex-wrap: wrap;
  gap: 6px 12px;
  align-items: center;
}
.legend-title {
  width: 100%;
  font-size: var(--fs-xs);
  color: var(--text-primary);
  margin-bottom: 2px;
  font-family: var(--mono);
}
.legend-chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: var(--fs-sm);
  font-family: var(--mono);
  color: var(--text-primary);
}
.legend-swatch {
  display: inline-block;
  width: 10px;
  height: 10px;
  border-radius: 2px;
  border: 1px solid rgba(0, 0, 0, 0.15);
}
</style>
