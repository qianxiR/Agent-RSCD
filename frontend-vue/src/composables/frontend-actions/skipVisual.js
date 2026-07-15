// ==================== handler: skipVisual (静默策略) ====================
// ★ 对应原生 executeFrontendAction 的 skipVisual 策略 (thinking.js:36)
//   render_table / render_chart / ui_panel_control: 结果由 AI 文字回复承载, 前端不渲染
//   wait_for_result=False, 统一回传 success

// 入参: data 任意(表格/图表/面板数据); ctx 未使用
// 方法: 直接 resolve success, 不产生任何视觉副作用
// 出参: { status: 'success', data }
export async function handleSkipVisual(data, ctx) {
  return { status: 'success', data }
}
