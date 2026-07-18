import { defineStore } from 'pinia'

// ==================== 多对话状态层 ====================
// ★ 替代原生 core/state.js 的全局 convStates + activeConvId
//   去掉 DOM 元素耦合（currentAssistantEl / container），改为纯数据驱动：
//   流式内容直接写入 messages 内的稳定 AI 消息，UI 始终 v-for 渲染同一条消息。

let _msgSeq = 0
function nextMsgId() {
  _msgSeq += 1
  return `m${_msgSeq}`
}

// 入参: message 当前 AI 消息
// 方法: 基于消息内 steps 长度生成局部稳定 step id，避免流式追加时使用数组下标作为 key
// 出参: string
function nextStepId(message) {
  return `${message.id}-s${(message.steps || []).length + 1}`
}

// 入参: role 后端消息角色
// 方法: 仅保留前端可展示角色；system/unknown 不进入聊天 UI，避免历史回填污染消息流
// 出参: 'human' | 'ai' | 'tool' | null
function normalizeRole(role) {
  if (role === 'human' || role === 'ai' || role === 'tool') return role
  return null
}

// 入参: value 任意消息内容
// 方法: 把后端可能返回的 null/list/object 统一成字符串，保留 JSON 结构便于工具结果查看
// 出参: string
function normalizeText(value) {
  if (typeof value === 'string') return value.trim()
  if (value == null) return ''
  return JSON.stringify(value, null, 2)
}

// 入参: thinking 推理文本, content 最终发送文本
// 方法: 后端流式阶段会把最终回复也推入 thinking；若 thinking 末尾与 content 重复，则只保留真正思考过程
// 出参: 去重后的 thinking 文本
function stripSentContentFromThinking(thinking, content) {
  const rawThinking = normalizeText(thinking)
  const rawContent = normalizeText(content)
  if (!rawThinking || !rawContent) return rawThinking
  if (rawThinking === rawContent) return ''
  const idx = rawThinking.lastIndexOf(rawContent)
  if (idx < 0) return rawThinking
  const trailing = rawThinking.slice(idx + rawContent.length).trim()
  if (trailing) return rawThinking
  return rawThinking.slice(0, idx).trim()
}

// 入参: content/thinking/toolCalls 一条 AI 消息的三类展示数据
// 方法: 区分工具规划消息与最终回复；工具规划只展示思考+工具调用，最终回复展示正文并剔除重复 thinking
// 出参: { content, thinking, toolCalls }
function normalizeAssistantParts({ content = '', thinking = '', toolCalls = [] }) {
  const calls = Array.isArray(toolCalls) ? toolCalls : []
  const text = normalizeText(content)
  const thought = normalizeText(thinking)
  if (calls.length) {
    return {
      content: thought === text ? '' : text,
      thinking: stripSentContentFromThinking(thought, text) || (thought === text ? text : thought),
      toolCalls: calls,
    }
  }
  return {
    content: text,
    thinking: stripSentContentFromThinking(thought, text),
    toolCalls: calls,
  }
}

// 入参: 一条 AI 消息的聚合字段
// 方法: 历史消息缺失真实交错顺序时，按思考 → 工具 → 回复 → 图片生成可渲染步骤
// 出参: 有序 steps 数组
function buildAssistantSteps({ id, thinking = '', toolCalls = [], content = '', images = [] }) {
  const steps = []
  const textThinking = normalizeText(thinking)
  const textContent = normalizeText(content)
  if (textThinking) steps.push({ id: `${id}-history-thinking`, type: 'thinking', text: textThinking })
  ;(Array.isArray(toolCalls) ? toolCalls : []).forEach((call, index) => {
    steps.push({ id: `${id}-history-tool-${index + 1}`, type: 'tool', call })
  })
  if (textContent) steps.push({ id: `${id}-history-content`, type: 'content', text: textContent })
  ;(Array.isArray(images) ? images : []).forEach((img, index) => {
    steps.push({ id: `${id}-history-image-${index + 1}`, type: 'image', url: img.url, caption: img.caption || '' })
  })
  return steps
}

// 入参: status 初始状态
// 方法: 创建一条稳定 AI 消息；流式片段后续只更新这条消息，不再 pending → messages 切换
// 出参: AI 消息对象
function createAssistantMessage(status = 'streaming') {
  const id = nextMsgId()
  return {
    id,
    role: 'ai',
    content: '',
    thinking: '',
    toolCalls: [],
    images: [],
    steps: [],
    status,
    error: '',
  }
}

// 入参: st 当前对话状态
// 方法: 优先按 currentAssistantId 取当前 AI 消息；缺失时补建，保证乱序流式事件仍有稳定落点
// 出参: AI 消息对象
function ensureCurrentAssistant(st) {
  const current = st.messages.find((m) => m.id === st.currentAssistantId && m.role === 'ai')
  if (current) return current
  const message = createAssistantMessage()
  st.messages.push(message)
  st.currentAssistantId = message.id
  st.isSending = true
  return message
}

// 入参: message 当前 AI 消息, type 文本步骤类型, field 聚合字段名, content 增量文本
// 方法: 连续同类文本 chunk 合并到最后一个 step，保留 ReAct 交错顺序且避免 token 级碎片
// 出参: 无
function appendTextStep(message, type, field, content) {
  if (!content) return
  message[field] += content
  const last = message.steps[message.steps.length - 1]
  if (last?.type === type) {
    last.text += content
    return
  }
  message.steps.push({ id: nextStepId(message), type, text: content })
}

// 入参: steps 步骤数组, content 当前最终回复文本
// 方法: 仅移除紧邻最终回复前、内容完全重复的 thinking 步骤，保留真实思考和工具调用顺序
// 出参: 过滤后的 steps 数组
function pruneDuplicateContentThinkingSteps(steps, content) {
  const normalizedContent = normalizeText(content)
  if (!Array.isArray(steps) || !normalizedContent) return Array.isArray(steps) ? steps : []
  const nextSteps = steps.slice()
  for (let i = nextSteps.length - 1; i >= 0; i -= 1) {
    const step = nextSteps[i]
    if (step.type !== 'content') continue
    const prev = nextSteps[i - 1]
    if (prev?.type === 'thinking' && normalizeText(prev.text) === normalizedContent) {
      nextSteps.splice(i - 1, 1)
    }
    return nextSteps
  }
  return nextSteps
}

// 入参: message 当前 AI 消息, parts 去重后的聚合字段
// 方法: 同步完成态聚合字段；steps 只移除重复最终回复，避免收尾时误删过程节点
// 出参: 无
function finalizeAssistantMessage(message, parts) {
  message.content = parts.content
  message.thinking = parts.thinking
  message.toolCalls = parts.toolCalls.slice()
  message.steps = pruneDuplicateContentThinkingSteps(message.steps, parts.content)
}

// 入参: message 当前 AI 消息, errorMsg 错误文本
// 方法: 判断消息是否包含任何可见内容，用于完成空回合时移除占位 AI 消息
// 出参: boolean
function hasAssistantContent(message, errorMsg = '') {
  return !!(
    message?.thinking ||
    message?.content ||
    message?.toolCalls?.length ||
    message?.images?.length ||
    message?.steps?.length ||
    errorMsg
  )
}

// 入参: 后端 /messages 返回的单行消息
// 方法: 映射为前端稳定展示模型，保留 tool role，避免工具结果 JSON 被当作普通 AI 回复
// 出参: 可渲染消息对象；不可展示角色返回 null
function normalizeHistoryMessage(m) {
  const role = normalizeRole(m.role)
  if (!role) return null
  const id = m.id || nextMsgId()
  if (role === 'human') {
    return {
      id,
      role,
      content: normalizeText(m.content),
      images: Array.isArray(m.images) ? m.images : [],
      selectedLayers: Array.isArray(m.selected_layers) ? m.selected_layers : [],
      nodeId: m.node_id || null,
      parentNodeId: m.parent_id || null,
    }
  }
  if (role === 'tool') {
    return {
      id,
      role,
      content: normalizeText(m.content),
      toolName: m.name || '',
      toolCallId: m.tool_call_id || '',
      status: 'done',
      nodeId: m.node_id || null,
      parentNodeId: m.parent_id || null,
    }
  }
  const parts = normalizeAssistantParts({
    content: m.content,
    thinking: m.thinking_content,
    toolCalls: m.tool_calls,
  })
  return {
    id,
    role,
    content: parts.content,
    thinking: parts.thinking,
    toolCalls: parts.toolCalls,
    images: Array.isArray(m.images) ? m.images : null,
    steps: buildAssistantSteps({
      id,
      thinking: parts.thinking,
      toolCalls: parts.toolCalls,
      content: parts.content,
      images: Array.isArray(m.images) ? m.images : [],
    }),
    status: 'done',
    nodeId: m.node_id || null,
    parentNodeId: m.parent_id || null,
  }
}

// 入参: toolCalls 当前 AI 消息工具调用列表, toolMsg 后端 ToolMessage 展示模型
// 方法: 优先按 tool_call_id 合并工具结果；缺少 id 时按工具名回退匹配，原地写入 call.result 以保持 steps 引用同步
// 出参: boolean 是否成功挂载
function attachToolResultToCalls(toolCalls, toolMsg) {
  if (!Array.isArray(toolCalls) || !toolCalls.length || !toolMsg) return false
  let idx = toolCalls.findIndex((tc) => tc.id && toolMsg.toolCallId && tc.id === toolMsg.toolCallId)
  if (idx < 0) {
    idx = toolCalls.findIndex((tc) => !tc.result && tc.name && toolMsg.toolName && tc.name === toolMsg.toolName)
  }
  if (idx < 0) {
    idx = toolCalls.findIndex((tc) => !tc.result)
  }
  if (idx < 0) return false
  toolCalls[idx].result = {
    content: toolMsg.content,
    toolName: toolMsg.toolName,
    toolCallId: toolMsg.toolCallId,
    status: toolMsg.status || 'done',
  }
  return true
}

// 入参: st 当前对话状态
// 方法: 优先找正在流式输出的 AI 消息；若结果稍晚到达，则回退到最近一条含工具调用的 AI 消息
// 出参: AI 消息对象或 null
function findAssistantForToolResult(st) {
  const current = st.messages.find((m) => m.id === st.currentAssistantId && m.role === 'ai')
  if (current?.toolCalls?.length) return current
  for (let i = st.messages.length - 1; i >= 0; i -= 1) {
    const message = st.messages[i]
    if (message.role === 'ai' && message.toolCalls?.length) return message
  }
  return null
}

// 入参: prev 前一条 AI 消息, next 当前 AI 消息
// 方法: 历史消息按 human 分隔轮次；同一轮内连续 AI 片段都属于同一个机器人气泡
// 出参: boolean 是否允许合并
function canMergeHistoryAssistant(prev, next) {
  if (!prev || !next) return false
  if (prev.role !== 'ai' || next.role !== 'ai') return false
  return true
}

// 入参: target 工具规划 AI 消息, source 最终回复 AI 消息
// 方法: 把同一用户问题后的连续 AI 片段合并为一条 AI，并重建固定顺序 steps
// 出参: 合并后的 AI 消息对象
function mergeHistoryAssistant(target, source) {
  target.content = [target.content, source.content].filter(Boolean).join('\n\n')
  target.thinking = [target.thinking, source.thinking].filter(Boolean).join('\n\n')
  target.toolCalls = [...(target.toolCalls || []), ...(source.toolCalls || [])]
  target.images = [...(target.images || []), ...(source.images || [])]
  target.status = source.status || target.status
  target.error = source.error || target.error || ''
  target.nodeId = source.nodeId || target.nodeId || null
  target.steps = buildAssistantSteps(target)
  return target
}

// 入参: 后端 /messages 原始数组
// 方法: 顺序规范化消息，并把 tool 角色结果合并进最近的 AI 工具调用内部，不再生成独立工具结果气泡
// 出参: 前端聊天消息数组
function buildHistoryMessages(messages = []) {
  const result = []
  messages.forEach((m) => {
    const item = normalizeHistoryMessage(m)
    if (!item) return
    if (item.role !== 'tool') {
      const prev = result[result.length - 1]
      if (canMergeHistoryAssistant(prev, item)) {
        mergeHistoryAssistant(prev, item)
        return
      }
      result.push(item)
      return
    }
    for (let i = result.length - 1; i >= 0; i--) {
      const prev = result[i]
      if (prev.role !== 'ai') continue
      if (attachToolResultToCalls(prev.toolCalls, item)) {
        prev.steps = buildAssistantSteps(prev)
        return
      }
    }
  })
  return result
}

// 创建一份对话默认状态
function createConvState(title = '(新对话)') {
  // 入参: title 对话标题
  // 方法: 创建纯数据状态；currentAssistantId 指向当前流式 AI 消息，streamTick 驱动滚动刷新
  // 出参: 对话状态对象
  return {
    title,
    isSending: false,
    _stopped: false,
    currentAssistantId: null,
    streamTick: 0,
    // ★ 兼容聚合字段：由当前稳定 AI 消息同步维护，避免旧调用点失效
    thinkingBuffer: '',
    currentContent: '',
    pendingToolCalls: [],
    pendingImages: [],
    // ★ 已归档的完整消息列表（UI v-for 渲染源）
    messages: [],
    // ★ 当前进行中的重工具任务（进度条展示用，null 表示无）
    activeTask: null,
    _callbacksRegistered: false,
  }
}

export const useConversationStore = defineStore('conversation', {
  state: () => ({
    convStates: {},        // convId → 对话状态
    activeConvId: null,    // 当前激活对话
    wsStatus: 'disconnected', // connecting / connected / disconnected / error
    sessionId: null,       // 后端确认的会话 ID
  }),

  getters: {
    // 当前活跃对话的状态引用（便捷访问，等价原生 cs()）
    active(state) {
      return state.activeConvId ? state.convStates[state.activeConvId] : null
    },
  },

  actions: {
    // ==================== 连接状态 ====================
    setWsStatus(status) {
      this.wsStatus = status
    },
    setSessionId(id) {
      this.sessionId = id
    },

    // ==================== 对话生命周期 ====================
    // get or create：首次访问时初始化一份默认状态
    getConv(convId, title) {
      if (!this.convStates[convId]) {
        this.convStates[convId] = createConvState(title)
      }
      return this.convStates[convId]
    },

    // 切换激活对话
    setActive(convId) {
      this.activeConvId = convId
    },

    // 删除对话状态
    removeConv(convId) {
      delete this.convStates[convId]
      if (this.activeConvId === convId) this.activeConvId = null
    },

    // ==================== 消息累积（流式回调写入） ====================
    // 追加用户消息（整条，非流式）
    appendUser(convId, { content, images = [], selectedLayers = [], id = null, nodeId = null }) {
      const st = this.getConv(convId)
      st.messages.push({
        id: id || nextMsgId(),
        role: 'human',
        content,
        images,
        selectedLayers,
        nodeId,
      })
    },

    // 开启一条 AI 回复累积区：重置进行中区 + 标记发送中
    beginAssistant(convId) {
      // 入参: convId 对话 ID
      // 方法: 直接创建一条稳定 AI 消息作为本轮流式落点，避免完成时重建 MessageBubble
      // 出参: 无
      const st = this.getConv(convId)
      const message = createAssistantMessage()
      st.isSending = true
      st._stopped = false
      st.currentAssistantId = message.id
      st.thinkingBuffer = ''
      st.currentContent = ''
      st.pendingToolCalls = []
      st.pendingImages = []
      st.messages.push(message)
      st.streamTick += 1
    },

    // 累积思考内容（增量片段）
    appendThinking(convId, content) {
      // 入参: convId 对话 ID, content 思考增量文本
      // 方法: 写入当前稳定 AI 消息，并同步兼容 thinkingBuffer
      // 出参: 无
      const st = this.getConv(convId)
      if (st._stopped) return
      st.thinkingBuffer += content
      appendTextStep(ensureCurrentAssistant(st), 'thinking', 'thinking', content)
      st.streamTick += 1
    },

    // 累积正文内容（增量片段）
    appendContent(convId, content) {
      // 入参: convId 对话 ID, content 回复正文增量文本
      // 方法: 写入当前稳定 AI 消息，并同步兼容 currentContent
      // 出参: 无
      const st = this.getConv(convId)
      if (st._stopped) return
      st.currentContent += content
      appendTextStep(ensureCurrentAssistant(st), 'content', 'content', content)
      st.streamTick += 1
    },

    // 追加工具调用
    appendToolCall(convId, { id = null, name, args }) {
      // 入参: convId 对话 ID, 工具调用对象
      // 方法: 工具调用作为独立 step 追加，保留其在思考和正文之间的真实位置
      // 出参: 无
      const st = this.getConv(convId)
      if (st._stopped) return
      const message = ensureCurrentAssistant(st)
      const call = { id, name, args }
      st.pendingToolCalls.push(call)
      message.toolCalls.push(call)
      message.steps.push({ id: nextStepId(message), type: 'tool', call })
      st.streamTick += 1
    },

    // 挂载工具结果
    attachToolResult(convId, { id = null, name = '', content = '', status = 'done' }) {
      // 入参: convId 对话 ID, 工具结果对象
      // 方法: 将后端实时返回的工具结果合并到对应 tool call，使当前 steps 内联展示结果
      // 出参: 无
      const st = this.getConv(convId)
      if (st._stopped) return
      const message = findAssistantForToolResult(st)
      if (!message) return
      const ok = attachToolResultToCalls(message.toolCalls, {
        toolCallId: id || '',
        toolName: name || '',
        content: normalizeText(content),
        status,
      })
      if (!ok) return
      st.pendingToolCalls = message.toolCalls
      st.streamTick += 1
    },

    // 追加聊天图片 (沙盒 chat_image action 产物, 累积到当前进行中消息)
    appendChatImage(convId, { url, caption = '' }) {
      // 入参: convId 对话 ID, 图片 URL 与说明
      // 方法: 图片作为独立 step 追加，让沙盒图片出现在真实过程流位置
      // 出参: 无
      const st = this.getConv(convId)
      if (st._stopped) return
      if (!url) return
      const message = ensureCurrentAssistant(st)
      const image = { url, caption }
      st.pendingImages.push(image)
      message.images.push(image)
      message.steps.push({ id: nextStepId(message), type: 'image', ...image })
      st.streamTick += 1
    },

    // ==================== 回合收尾 ====================
    // 把进行中区归档成一条 message，清空累积，结束发送态
    // status: 'done' | 'stopped' | 'error'
    finishAssistant(convId, status = 'done', errorMsg = '') {
      // 入参: convId 对话 ID, status 完成状态, errorMsg 错误文本
      // 方法: 只更新当前 AI 消息状态并做完成态去重，不再 push 新消息
      // 出参: 无
      const st = this.convStates[convId]
      if (!st) return
      const message = st.messages.find((m) => m.id === st.currentAssistantId && m.role === 'ai')
      st.isSending = false
      st._stopped = false
      if (message && hasAssistantContent(message, errorMsg)) {
        const parts = normalizeAssistantParts({
          content: message.content,
          thinking: message.thinking,
          toolCalls: message.toolCalls,
        })
        finalizeAssistantMessage(message, parts)
        message.status = status
        message.error = errorMsg || ''
      } else if (message) {
        st.messages = st.messages.filter((m) => m.id !== message.id)
      }
      st.currentAssistantId = null
      st.thinkingBuffer = ''
      st.currentContent = ''
      st.pendingToolCalls = []
      st.pendingImages = []
      st.streamTick += 1
    },

    // 用户停止标记（onChatStopped 前的中间态）
    markStopped(convId) {
      // 入参: convId 用户主动停止的对话 ID。
      // 方法: 立即归档当前回复并恢复输入，同时保留停止屏障以丢弃后端迟到片段。
      // 出参: 无；后端 chat_stopped 到达后由 finishAssistant 清除停止屏障。
      const st = this.convStates[convId]
      if (!st) return
      st._stopped = true
      this.finishAssistant(convId, 'stopped')
      st._stopped = true
    },

    // ==================== 历史回填 ====================
    // 把后端历史消息灌入 messages 数组（不触发流式累积）
    loadHistory(convId, messages = []) {
      const st = this.getConv(convId)
      st.messages = buildHistoryMessages(messages)
      st.isSending = false
      st._stopped = false
      st.currentAssistantId = null
      st.streamTick += 1
    },

    // ==================== 任务进度（重工具轮询写入）====================
    // 入参: convId, task({id, tool_name, status, progress, error?})
    // 方法: 写入对话进行中任务状态（UI 进度条渲染源）
    setActiveTask(convId, task) {
      const st = this.getConv(convId)
      st.activeTask = task
    },

    // 清除对话进行中任务（轮询命中终态或 done 时调用）
    clearActiveTask(convId) {
      const st = this.convStates[convId]
      if (st) st.activeTask = null
    },
  },
})
