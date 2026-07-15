import { frontendActionHandlers } from './frontend-actions'
import { useMap } from './useMap'
import { useWebSocket } from './useWebSocket'
import { useConversationStore } from '@/stores/conversation'

// ==================== frontend_action 分发器 (composable) ====================
// ★ 对应原生 executeFrontendAction (chat/thinking.js:26) 的派发 + finishAction 回传
//
// 核心职责(单一):
//   收到 { event_type, event_data, request_id } → 查注册表执行 handler
//   → 拿结果统一 sendToolResult 回传后端(解除 wait_for_frontend_result 阻塞)
//
// 分层约束: 本层不实现任何具体动作逻辑(全在 frontend-actions/ 各 handler 内),
//   也不碰 DOM, 只做「分发 + 回传 + 兜底」三件事。

// 构造上下文: 注入地图能力, 供图层类 handler 使用
function buildContext(convId) {
  return { map: useMap(), convId, store: useConversationStore() }
}

// 入参: { event_type, event_data, request_id, conversation_id }
// 方法: 查注册表 → 执行 handler → 拿 {status, ...} 回传 tool_result;
//   未命中 event_type → 兜底回传 {status:'rendered'}(对齐原生 default 分支);
//   handler 抛错 → 回传 {status:'error'}(对齐原生 finishAction catch)
// 出参: 无(副作用: sendToolResult 回传后端)
function dispatch({ event_type, event_data, request_id, conversation_id }) {
  const ws = useWebSocket()
  const handler = frontendActionHandlers[event_type]
  const ctx = buildContext(conversation_id)

  // 兜底: 未知 event_type, 直接回传 rendered(原生 default 分支语义)
  if (!handler) {
    reply(ws, request_id, { status: 'rendered', data: event_data })
    return
  }

  // 执行 handler; await 异步结果; 异常回传 error
  Promise.resolve()
    .then(() => handler(event_data, ctx))
    .then((payload) => reply(ws, request_id, payload))
    .catch((err) => reply(ws, request_id, { status: 'error', msg: err.message || String(err) }))
}

// 入参: ws(useWebSocket 实例), requestId, payload(handler 返回的结果对象)
// 方法: 组装 tool_result 并回传; request_id 缺失则不回传(单向事件无需解除阻塞)
// 出参: 无
function reply(ws, requestId, payload) {
  if (!requestId) return
  ws.sendToolResult({
    request_id: requestId,
    status: payload.status || 'success',
    data: payload,
  })
}

export function useFrontendAction() {
  return { dispatch }
}
