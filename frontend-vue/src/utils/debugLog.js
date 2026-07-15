// ==================== 诊断日志工具（排查地图渲染，用完可删）====================
// 入参: msg 字符串（或对象，自动 JSON.stringify）
// 方法: console.log 输出；POST 到 /api/v1/debug/log 已移除（后端未启时刷屏 ECONNREFUSED）
// 出参: 无
export function debugLog(msg) {
  const text = typeof msg === 'string' ? msg : JSON.stringify(msg)
  console.log('[WMS-DEBUG]', text)
}
