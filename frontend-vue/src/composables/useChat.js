import { useConversationStore } from '@/stores/conversation'
import { useProjectStore } from '@/stores/project'
import { useWebSocket } from '@/composables/useWebSocket'
import { getConvMessages, regenerateConversation } from '@/services/api'

// ==================== 发送编排层（composable）====================
// ★ 对应原生 core/ws-send.js 的 sendMessage + chat/conv-core.js 的对话激活逻辑
//   把「新建对话 / 发送 / 停止 / 首次激活拉历史」收敛为一组编排函数

// UUID 生成（兼容旧浏览器，与原生 generateUUID 一致）
function generateUUID() {
  if (crypto.randomUUID) return crypto.randomUUID()
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0
    return (c === 'x' ? r : (r & 0x3) | 0x8).toString(16)
  })
}

// 入参: title?（新对话标题）
// 方法: 生成 UUID → conversationStore 初始化状态并设为激活 → projectStore 写入摘要
//   (左侧列表数据源是 projectStore.convList, 不写则新建对话需刷新页面才出现)
// 出参: 新对话 convId
function newConversation(title = '(新对话)') {
  const store = useConversationStore()
  const projectStore = useProjectStore()
  const convId = generateUUID()
  store.getConv(convId, title)
  store.setActive(convId)
  // ★ 同步摘要到项目列表: 归属当前激活项目, 立即出现在左侧对话管理
  projectStore.upsertConv({
    id: convId,
    project_id: projectStore.activeProjectId || null,
    title,
    updated_at: '',
    message_count: 0,
  })
  return convId
}

// 入参: convId
// 方法: 首次激活某对话时拉取历史消息灌入 store
//   幂等：_callbacksRegistered 标记防止重复拉取
// 出参: 无
async function ensureHistoryLoaded(convId) {
  const store = useConversationStore()
  const st = store.getConv(convId)
  if (st._callbacksRegistered) return
  st._callbacksRegistered = true
  try {
    const data = await getConvMessages(convId)
    store.loadHistory(convId, data.messages || [])
  } catch (e) {
    console.warn('[chat] 历史加载失败:', convId, e)
  }
}

// ★ 发送一条消息的完整编排：
//   1. 无激活对话 → 自动新建
//   2. 追加用户消息到 store
//   3. 开启 AI 回复累积区
//   4. 经 WS 发送 chat_request
// 入参: { prompt, projectId?, images?, selectedLayers? }
// 出参: boolean 是否发送成功
function send({ prompt, projectId = null, images = [], selectedLayers = [] }) {
  // 入参: prompt、项目、影像和图层上下文。
  // 方法: 先确认 WebSocket 可发送，再创建本地消息状态，避免断线时产生悬空回复。
  // 出参: boolean；连接不可用或会话正在发送时返回 false，且不修改消息列表。
  const store = useConversationStore()
  const ws = useWebSocket()

  if (!ws.isConnected()) return false

  let convId = store.activeConvId
  if (!convId) {
    convId = newConversation()
  }
  const st = store.getConv(convId)
  if (st.isSending) return false

  store.appendUser(convId, { content: prompt, images, selectedLayers })
  store.beginAssistant(convId)
  return ws.sendChat({ prompt, conversation_id: convId, project_id: projectId, images, selected_layers: selectedLayers })
}

// 停止当前激活对话
function stop() {
  const store = useConversationStore()
  const ws = useWebSocket()
  const convId = store.activeConvId
  if (!convId) return false
  store.markStopped(convId)
  return ws.sendStop(convId)
}

// 切换到指定对话（含首次历史加载）
function switchTo(convId) {
  // 入参: convId 目标对话 ID
  // 方法: 激活目标对话并确保历史已加载；目标已激活但尚未加载时仍补拉历史
  // 出参: 无
  const store = useConversationStore()
  if (convId === store.activeConvId) {
    ensureHistoryLoaded(convId)
    return
  }
  store.getConv(convId)
  store.setActive(convId)
  ensureHistoryLoaded(convId)
}

// ★ 重新生成编排：普通模式从节点后分叉，编辑模式替换目标用户消息及后续分支
// 入参: { nodeId, prompt, replace?, images?, selectedLayers? }
// 方法: reload 历史补齐节点链 → 调用对应分叉模式 → 刷新历史 → 经 WS 重发消息
// 出参: boolean 是否成功发起
async function regenerate({ nodeId, prompt, replace = false, images = [], selectedLayers = [] }) {
  const store = useConversationStore()
  const convId = store.activeConvId
  if (!convId) return false
  // reload 历史，确保拿到完整 nodeId 链（实时生成的 AI 消息需此步补 nodeId）
  const data = await getConvMessages(convId)
  store.loadHistory(convId, data.messages || [])
  const targetNode = replace ? nodeId : resolveForkNode(store.active.messages, nodeId)
  if (!targetNode) {
    console.warn('[chat] 未找到分叉点 node:', nodeId)
    return false
  }
  const result = await regenerateConversation(convId, {
    parent_node_id: replace ? null : targetNode,
    replace_node_id: replace ? targetNode : null,
    prompt,
  })
  if (result.status !== 'success') {
    throw new Error(result.msg || '对话分支更新失败')
  }
  // 分叉后重灌历史，再用编辑后的上下文发起新回合。
  const refreshed = await getConvMessages(convId)
  store.loadHistory(convId, refreshed.messages || [])
  return send({ prompt, images, selectedLayers })
}

// 入参: messages 当前消息列表, targetNodeId 目标消息 nodeId
// 方法: 若目标是 human 则直接返回自身；若目标是 ai 则向前找最近 human 作为分叉点
// 出参: 分叉点 nodeId —— human 直接命中自身；ai 回退到其前置 human
function resolveForkNode(messages, targetNodeId) {
  const idx = messages.findIndex((m) => m.nodeId === targetNodeId)
  if (idx < 0) return targetNodeId
  if (messages[idx].role === 'human') return messages[idx].nodeId || targetNodeId
  // 往前找最近的 human 消息作为分叉点
  for (let i = idx - 1; i >= 0; i--) {
    if (messages[i].role === 'human') return messages[i].nodeId || targetNodeId
  }
  return targetNodeId
}

export function useChat() {
  return {
    newConversation,
    send,
    stop,
    switchTo,
    regenerate,
    ensureHistoryLoaded,
    generateUUID,
  }
}
