// ==================== 框选解译状态（地图 → 弹窗 → 会话）====================
import { ref } from 'vue'

const drawRequested = ref(false)
const dialogOpen = ref(false)
const bbox = ref(null)

export function useCaptureInterpret() {
  // 入参: 无 | 方法: 请求地图进入框选模式 | 出参: 无
  function requestDraw() {
    drawRequested.value = true
  }

  // 入参: bbox | 方法: 框选完成 → 记录范围并打开解译确认弹窗 | 出参: 无
  function finishDraw(nextBbox) {
    if (Array.isArray(nextBbox) && nextBbox.length === 4) bbox.value = nextBbox
    drawRequested.value = false
    dialogOpen.value = true
  }

  // 入参: 无 | 方法: Esc 取消框选 | 出参: 无
  function cancelDraw() {
    drawRequested.value = false
  }

  // 入参: 无 | 方法: 关闭解译确认弹窗 | 出参: 无
  function closeDialog() {
    dialogOpen.value = false
  }

  return { drawRequested, dialogOpen, bbox, requestDraw, finishDraw, cancelDraw, closeDialog }
}
