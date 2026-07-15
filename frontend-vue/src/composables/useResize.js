import { onBeforeUnmount } from 'vue'

// ==================== 统一拖拽调整尺寸 ====================
// ★ 抽取自 MainLayout(左右栏水平) 与 ChatPanel(输入框垂直) 两处拖拽，
//   复用同一套 pointer 事件 + 钳位逻辑，方向与语义由参数区分。
//
// 设计原则：
//   - composable 只负责「算数值」(pointer 坐标 → 区间内的目标尺寸)
//   - DOM/ref 的写入交给调用方 onUpdate，避免耦合各组件内部结构
//   - 副作用(body 光标/选区)与监听解绑统一在此处兜底

// 入参: 无
// 方法: 返回 startResize 工厂 —— 由调用方绑到拖拽条的事件上
//   startResize(ev, opts) 形参:
//     ev       pointerdown/mousedown 事件对象(取 clientX/clientY 作为起点)
//     opts.axis    'x' | 'y'        拖拽方向(x=水平改宽, y=垂直改高)
//     opts.invert  boolean          true 表示坐标减小(往右下)尺寸增大(右栏/垂直往上)
//     opts.min     number           最小尺寸(含)
//     opts.max     number           最大尺寸(含)
//     opts.start   number           起始尺寸(通常为当前 ref 的值)
//     opts.onUpdate(next:number)    每帧钳位后的新尺寸回调(调用方写 ref/DOM)
//     opts.onStart?():void          拖拽开始回调(如 ChatPanel 标记 userResized)
// 出参: { startResize } 供模板 @pointerdown 调用
export function useResize() {
  let active = null // 拖拽态: { axis, invert, min, max, origin, base, onUpdate, onMove, onUp }

  function stopResize() {
    if (!active) return
    window.removeEventListener('pointermove', active.onMove)
    window.removeEventListener('pointerup', active.onUp)
    document.body.style.cursor = ''
    document.body.style.userSelect = ''
    active = null
  }

  function startResize(ev, opts) {
    // 重复按下时先回收上一次监听，杜绝监听器泄漏
    stopResize()
    const axis = opts.axis
    const origin = axis === 'x' ? ev.clientX : ev.clientY
    const onMove = (e) => {
      if (!active) return
      const cur = axis === 'x' ? e.clientX : e.clientY
      const delta = cur - origin
      // invert: 右栏(往左拖 clientX 减小要变宽) 与 垂直(往上拖 clientY 减小要变高)
      const next = opts.invert ? opts.start - delta : opts.start + delta
      active.onUpdate(Math.max(opts.min, Math.min(opts.max, next)))
    }
    const onUp = () => stopResize()
    active = { axis, invert: opts.invert, min: opts.min, max: opts.max, origin, base: opts.start, onUpdate: opts.onUpdate, onMove, onUp }
    document.body.style.cursor = axis === 'x' ? 'col-resize' : 'ns-resize'
    document.body.style.userSelect = 'none'
    window.addEventListener('pointermove', onMove)
    window.addEventListener('pointerup', onUp)
    if (typeof opts.onStart === 'function') opts.onStart()
  }

  // 组件卸载兜底：若拖拽中卸载则解绑监听，避免悬空回调
  onBeforeUnmount(stopResize)

  return { startResize }
}
