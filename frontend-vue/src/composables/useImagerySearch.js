// ==================== 影像检索面板状态（跨组件共享单例）====================
// 入口有两个：左侧栏「影像」、检索框内的「框选范围」。
// 用模块级 ref 保证侧栏/地图/弹窗拿到的都是同一份 bbox 与开关状态。
import { ref } from 'vue'

export const REGION_PRESETS = {
  chengdu: { label: '成都市', bbox: [102.9, 30.05, 104.9, 31.45] },
  sichuan: { label: '四川省', bbox: [97.35, 26.05, 108.55, 34.32] },
}

const open = ref(false)
const bbox = ref([...REGION_PRESETS.chengdu.bbox])
const bboxLabel = ref(REGION_PRESETS.chengdu.label)
const drawRequested = ref(false)
const overlaid = ref([])

export function useImagerySearch() {
  // 入参: 无 | 方法: 打开检索弹窗 | 出参: 无
  function openDialog() {
    open.value = true
  }

  // 入参: 无 | 方法: 关闭弹窗（保留已选条件与结果） | 出参: 无
  function closeDialog() {
    open.value = false
  }

  // 入参: 无 | 方法: 收起弹窗并请求地图进入框选模式 | 出参: 无
  function requestDraw() {
    open.value = false
    drawRequested.value = true
  }

  // 入参: bbox [minx,miny,maxx,maxy] | 方法: 框选完成 → 写回范围并重新打开弹窗 | 出参: 无
  function finishDraw(nextBbox) {
    if (Array.isArray(nextBbox) && nextBbox.length === 4) {
      bbox.value = nextBbox
      bboxLabel.value = '框选范围'
    }
    drawRequested.value = false
    open.value = true
  }

  // 入参: 无 | 方法: 取消框选（Esc / 再次点击）→ 恢复弹窗 | 出参: 无
  function cancelDraw() {
    drawRequested.value = false
    open.value = true
  }

  // 入参: label 展示名, nextBbox 范围 | 方法: 切换预设区域 | 出参: 无
  function setRegion(label, nextBbox) {
    if (nextBbox) bbox.value = nextBbox
    bboxLabel.value = label
  }

  // 入参: { id, label } | 方法: 记录已叠加的影像图层 | 出参: 无
  function markOverlaid(entry) {
    if (!entry?.id) return
    if (!overlaid.value.some((item) => item.id === entry.id)) {
      overlaid.value = [...overlaid.value, entry]
    }
  }

  // 入参: 无 | 方法: 清空已叠加记录（地图图层由调用方清） | 出参: 无
  function clearOverlaid() {
    overlaid.value = []
  }

  return {
    open,
    bbox,
    bboxLabel,
    drawRequested,
    overlaid,
    openDialog,
    closeDialog,
    requestDraw,
    finishDraw,
    cancelDraw,
    setRegion,
    markOverlaid,
    clearOverlaid,
  }
}
