import { useConversationStore } from '@/stores/conversation'
import { getTasks } from '@/services/api'

// ==================== WebSocket 传输层（composable）====================
// ★ 对应原生 core/ws-chat.js 的 WsChatClient + core/ws-send.js 的 initWsClient
//
// 核心简化：原生用 conversationCallbacks Map + pendingMessages 缓冲实现多对话路由；
//   Vue 版状态全在 Pinia store 按 convId 隔离，消息直接按 conversation_id
//   路由写入对应 store 状态，UI 按需渲染激活对话，无需注册/缓冲机制。
//
// 传输协议与原生完全一致：
//   WS 端点 /api/v1/agent/ws/chat
//   上行: chat_request / stop_chat / tool_result / get_active_tasks / pong
//   下行: session_started / thinking / tool_call / tool_result / frontend_action /
//         content / error / done / chat_stopped / memory_updated / active_tasks_list / ping

const WS_PATH = '/api/v1/agent/ws/chat'

// 重工具关键词（命中则启动任务进度轮询；后端仅这些类工具创建 ai_task 记录）
const HEAVY_TOOL_RE = /segment|detect_change|report|pyramid|cog|topology|component|shapefile|overlay_edge|visualize_vector/

// 模块级单例：整个应用共用一条 WS 连接
let _ws = null
let _store = null
// 任务轮询定时器：convId → setInterval handle
const _taskPollers = {}

function getStore() {
  if (!_store) _store = useConversationStore()
  return _store
}

// ==================== 任务进度轮询（重工具执行期间刷新 activeTask）====================
// 入参: convId, toolName 触发该轮询的工具名
// 方法: 若是重工具且该会话未在轮询 → 立即查一次 + 启动 3s 轮询; 轻工具忽略
// 出参: 无（副作用: 写 store.activeTask + 启动定时器）
function startTaskPolling(convId, toolName) {
  if (!convId || _taskPollers[convId]) return
  if (!HEAVY_TOOL_RE.test(toolName || '')) return
  // 占位任务（立即让 UI 显示"运行中"，轮询命中真实 task 后覆盖）
  getStore().setActiveTask(convId, { id: null, tool_name: toolName, status: 'running', progress: 0, error: '' })
  pollOnce(convId)
  _taskPollers[convId] = setInterval(() => pollOnce(convId), 3000)
}

// 入参: convId
// 方法: 拉取该会话最新任务 → 终态(done/failed/cancelled)则更新并停止; 进行中则更新进度
async function pollOnce(convId) {
  try {
    const data = await getTasks({ conversation_id: convId, limit: 1 })
    const task = (data.tasks || [])[0]
    if (!task) return
    getStore().setActiveTask(convId, task)
    if (['done', 'failed', 'cancelled'].includes(task.status)) stopTaskPolling(convId)
  } catch (e) {
    // 查询失败不中断轮询（网络抖动等）
  }
}

// 入参: convId
// 方法: 清除该会话的轮询定时器 + 清空占位任务（done/error 时调用）
function stopTaskPolling(convId) {
  if (_taskPollers[convId]) {
    clearInterval(_taskPollers[convId])
    delete _taskPollers[convId]
  }
}


// 入参: event.data 原始字符串
// 方法: JSON 解析 + 按 conversation_id 路由到 store action
// 出参: 无（副作用：写入 store）
function handleRawMessage(raw) {
  let data
  try {
    data = JSON.parse(raw)
  } catch (e) {
    console.warn('[WS] 非法 JSON:', raw)
    return
  }
  if (isSessionMessage(data.type)) {
    handleSessionMessage(data)
    return
  }
  const convId = data.conversation_id
  // 会话级消息（无 conversation_id）
  if (!convId) {
    handleSessionMessage(data)
    return
  }
  // 对话级消息 → 路由到对应 convId 的 store 状态
  handleConvMessage(convId, data)
}

// 入参: type WebSocket 消息类型
// 方法: 识别协议级消息；这些消息即使带 conversation_id 也不进入对话内容流
// 出参: boolean
function isSessionMessage(type) {
  return ['session_started', 'active_tasks_list', 'ping'].includes(type)
}

// 会话级消息：session_started / active_tasks_list / ping
function handleSessionMessage(data) {
  const store = getStore()
  switch (data.type) {
    case 'session_started':
      store.setSessionId(data.conversation_id)
      break
    case 'active_tasks_list':
      console.log('[WS] 活跃任务:', data.conversations || [], data.count || 0)
      break
    case 'ping':
      sendRaw({ type: 'pong' })
      break
    default:
      console.warn('[WS] 未知会话级消息:', data.type, data)
  }
}

// 对话级消息：路由到对应 convId 的累积区
function handleConvMessage(convId, data) {
  const store = getStore()
  switch (data.type) {
    case 'thinking':
      store.appendThinking(convId, data.content || '')
      break
    case 'tool_call':
      ;(data.tool_calls || []).forEach((tc) => {
        store.appendToolCall(convId, { id: tc.id || tc.tool_call_id || null, name: tc.name, args: tc.args })
        // 重工具：启动任务进度轮询（写 store.activeTask，UI 进度条渲染）
        startTaskPolling(convId, tc.name)
      })
      break
    case 'tool_result':
      store.attachToolResult(convId, {
        id: data.tool_call_id || data.id || null,
        name: data.name || data.tool_name || '',
        content: data.content || '',
        status: data.status || 'done',
      })
      break
    case 'frontend_action':
      // ★ 交由分发器执行具体动作 + 统一回传 tool_result(解除后端阻塞)
      //   动态 import 打破循环依赖: useFrontendAction 反向依赖本模块
      import('./useFrontendAction').then(({ useFrontendAction }) => {
        useFrontendAction().dispatch({
          event_type: data.event_type,
          event_data: data.event_data,
          request_id: data.request_id,
          conversation_id: convId,
        })
      })
      break
    case 'content':
      store.appendContent(convId, data.content || '')
      break
    case 'error':
      store.finishAssistant(convId, 'error', data.content || '未知错误')
      stopTaskPolling(convId)
      store.clearActiveTask(convId)
      break
    case 'done':
      store.finishAssistant(convId, 'done')
      stopTaskPolling(convId)
      store.clearActiveTask(convId)
      break
    case 'chat_stopped':
      store.finishAssistant(convId, 'stopped')
      stopTaskPolling(convId)
      store.clearActiveTask(convId)
      break
    case 'memory_updated':
      console.log('[WS] 记忆更新:', data.items || [])
      break
    default:
      console.warn('[WS] 未知对话级消息:', data.type, data)
  }
}

// 裸 JSON 发送（内部用）
function sendRaw(payload) {
  if (!_ws || _ws.readyState !== WebSocket.OPEN) {
    console.error('[WS] 未连接，无法发送:', payload.type)
    return false
  }
  _ws.send(JSON.stringify(payload))
  return true
}

// ==================== 对外接口 ====================

// 入参: 无
// 方法: 建立唯一 WS 连接，绑定 onopen/onmessage/onclose/onerror
// 出参: 无（状态写入 store.wsStatus）
function connect() {
  if (_ws && _ws.readyState === WebSocket.OPEN) return
  const store = getStore()
  store.setWsStatus('connecting')

  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:'
  const url = `${proto}//${location.host}${WS_PATH}`
  _ws = new WebSocket(url)

  _ws.onopen = () => {
    console.log('[WS] 已连接')
    store.setWsStatus('connected')
  }
  _ws.onmessage = (event) => handleRawMessage(event.data)
  _ws.onclose = () => {
    console.log('[WS] 已断开')
    store.setWsStatus('disconnected')
  }
  _ws.onerror = () => {
    console.error('[WS] 连接错误')
    store.setWsStatus('error')
  }
}

function disconnect() {
  if (_ws) {
    _ws.close()
    _ws = null
  }
}

function isConnected() {
  return !!_ws && _ws.readyState === WebSocket.OPEN
}

// 入参: { prompt, conversation_id, project_id, model, temperature, images, selected_layers }
// 方法: 组装 chat_request payload（格式与原生一致）并发送
// 出参: boolean 是否发送成功
function sendChat({ prompt, conversation_id, project_id = null, model = 'qwen-plus', temperature = 0.7, images = [], selected_layers = [] }) {
  return sendRaw({
    type: 'chat_request',
    prompt,
    images,
    selected_layers,
    model,
    temperature,
    conversation_id,
    project_id,
    user_id: 'study_user',
    multi_round: true,
  })
}

// 入参: conversation_id（可选，不传则停止全部）
function sendStop(conversation_id = null) {
  const payload = { type: 'stop_chat' }
  if (conversation_id) payload.conversation_id = conversation_id
  return sendRaw(payload)
}

// 入参: { request_id, status, data } —— 解除后端 wait_for_frontend_result 阻塞
function sendToolResult({ request_id, status = 'success', data = {} }) {
  return sendRaw({ type: 'tool_result', request_id, status, data })
}

// composable 包装：供组件按需 import
export function useWebSocket() {
  return {
    connect,
    disconnect,
    isConnected,
    sendChat,
    sendStop,
    sendToolResult,
  }
}
