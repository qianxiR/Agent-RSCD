<script setup>
// ==================== AI 过程流步骤 ====================
// ★ 只负责渲染一条 AI 消息内的有序 steps，MessageBubble 继续负责头像与消息操作。
import { computed, ref } from 'vue'
import { renderMarkdown } from '@/utils/markdown'

const props = defineProps({
  message: { type: Object, required: true },
  streaming: { type: Boolean, default: false },
})

const expandedSteps = ref({})
const steps = computed(() => normalizeSteps(props.message))
const imageUrls = computed(() => steps.value.filter((step) => step.type === 'image' && step.url).map((step) => step.url))
const hasError = computed(() => props.message.status === 'error' && props.message.error)
const isStopped = computed(() => props.message.status === 'stopped')

function normalizeSteps(message) {
  // 入参: message AI 消息对象
  // 方法: 优先使用 store 生成的 steps；旧数据缺失 steps 时按固定顺序降级生成
  // 出参: 可渲染 steps 数组
  if (Array.isArray(message.steps) && message.steps.length) return message.steps
  const fallback = []
  if ((message.thinking || '').trim()) fallback.push({ id: `${message.id}-thinking`, type: 'thinking', text: message.thinking })
  ;(message.toolCalls || []).forEach((call, index) => fallback.push({ id: `${message.id}-tool-${index}`, type: 'tool', call }))
  if ((message.content || '').trim()) fallback.push({ id: `${message.id}-content`, type: 'content', text: message.content })
  ;(message.images || []).forEach((image, index) => fallback.push({ id: `${message.id}-image-${index}`, type: 'image', ...image }))
  return fallback
}

function stepKey(step, index) {
  // 入参: step 过程节点, index 渲染位置
  // 方法: 优先使用稳定 id，缺失时用类型和位置兜底，保证展开状态可定位
  // 出参: string
  return step.id || `${step.type}-${index}`
}

function toggleStep(key) {
  // 入参: key stepKey 生成的步骤键
  // 方法: 使用对象映射维护每个过程节点的独立折叠状态
  // 出参: 无
  expandedSteps.value[key] = !expandedSteps.value[key]
}

function isExpanded(key) {
  // 入参: key stepKey 生成的步骤键
  // 方法: 读取步骤展开状态，默认折叠以控制长思考和大参数占用空间
  // 出参: boolean
  return !!expandedSteps.value[key]
}

function shouldShowCursor(index) {
  // 入参: index 当前步骤下标
  // 方法: 只在流式中的最后一个正文步骤后显示光标，避免多个正文段同时闪烁
  // 出参: boolean
  return props.streaming && index === steps.value.length - 1
}

function onContentClick(e) {
  // 入参: e 点击事件
  // 方法: 委托处理 markdown 富表格复制按钮，把表格内容转换为 TSV 写入剪贴板
  // 出参: 无
  const btn = e.target.closest('.rich-table-copy-btn')
  if (!btn) return
  const card = btn.closest('.rich-table-card')
  const table = card?.querySelector('.rich-table')
  if (!table) return
  const rows = []
  table.querySelectorAll('tr').forEach((tr) => {
    rows.push([...tr.querySelectorAll('th,td')].map((c) => c.textContent).join('\t'))
  })
  copyTextToClipboard(rows.join('\n')).then(() => {
    btn.classList.add('copied')
    btn.title = '已复制'
    setTimeout(() => {
      btn.classList.remove('copied')
      btn.title = '复制为 TSV'
    }, 1500)
  })
}

function copyTextToClipboard(text) {
  // 入参: text 待复制文本
  // 方法: 优先使用 Clipboard API，失败时回退到隐藏 textarea + execCommand
  // 出参: Promise<void>
  if (navigator.clipboard?.writeText && window.isSecureContext) {
    return navigator.clipboard.writeText(text).catch(() => copyTextWithTextarea(text))
  }

  return copyTextWithTextarea(text)
}

function copyTextWithTextarea(text) {
  // 入参: text 待复制文本
  // 方法: 使用隐藏 textarea 触发浏览器原生命令，规避 Clipboard API 代理服务连接失败
  // 出参: Promise<void>
  return new Promise((resolve, reject) => {
    const textarea = document.createElement('textarea')
    textarea.value = text
    textarea.setAttribute('readonly', 'readonly')
    textarea.style.position = 'fixed'
    textarea.style.top = '-9999px'
    textarea.style.left = '-9999px'
    textarea.style.opacity = '0'
    document.body.appendChild(textarea)
    textarea.focus()
    textarea.select()
    let ok = false
    try {
      ok = document.execCommand('copy')
    } catch {
      ok = false
    }
    document.body.removeChild(textarea)
    if (ok) resolve()
    else reject(new Error('copy failed'))
  })
}

function parseToolResult(raw, fallbackToolName = '') {
  // 入参: raw 工具结果原始字符串, fallbackToolName 工具名兜底
  // 方法: JSON 优先解析并提取摘要；非 JSON 按纯文本摘要展示
  // 出参: { parsed, status, title, summary, detail }
  const text = (raw || '').trim()
  if (!text) {
    return { parsed: null, status: 'empty', title: '工具结果', summary: '无返回内容', detail: '' }
  }
  let parsed = null
  try {
    parsed = JSON.parse(text)
  } catch {
    parsed = null
  }
  if (!parsed || typeof parsed !== 'object') {
    return {
      parsed: null,
      status: 'text',
      title: '工具结果',
      summary: normalizeResultText(text),
      detail: text,
    }
  }
  const status = parsed.type || parsed.status || parsed.validation_status || 'result'
  const summary = parsed.summary || parsed.msg || parsed.message || parsed.error || parsed.description || summarizeObject(parsed)
  const toolName = fallbackToolName || parsed.tool || parsed.tool_name || ''
  return {
    parsed,
    status,
    title: toolName ? `工具结果 · ${toolName}` : '工具结果',
    summary: normalizeResultText(summary),
    detail: JSON.stringify(parsed, null, 2),
  }
}

function getToolCallResult(call) {
  // 入参: call 工具调用对象
  // 方法: 从挂载在工具调用上的 result 字段中解析展示摘要
  // 出参: 工具结果展示模型
  return parseToolResult(call?.result?.content || '', call?.result?.toolName || call?.name || '')
}

function thinkingSummary(step) {
  // 入参: step 思考步骤
  // 方法: 使用思考文本首行生成折叠摘要，替代固定“思考过程”标题
  // 出参: string
  return compactDisplayText(step?.text || '', { maxChars: 96, maxLines: 1 }) || '正在整理上下文'
}

function toolSummary(call) {
  // 入参: call 工具调用对象
  // 方法: 优先显示工具名，附带简短参数摘要，替代固定“工具调用”标题
  // 出参: string
  const name = call?.name || 'unknown_tool'
  const args = call?.args && Object.keys(call.args).length ? compactDisplayText(JSON.stringify(call.args), { maxChars: 96, maxLines: 1 }) : ''
  return args ? `${name} ${args}` : name
}

function summarizeObject(data) {
  // 入参: data 结构化工具结果
  // 方法: 在没有显式摘要字段时，从前几个标量字段生成短摘要
  // 出参: string
  const pairs = Object.entries(data)
    .filter(([, value]) => typeof value !== 'object' || value == null)
    .slice(0, 4)
    .map(([key, value]) => `${key}: ${compactDisplayText(String(value), { maxChars: 80, maxLines: 1 })}`)
  return pairs.join('；') || '已返回结构化结果'
}

function normalizeResultText(text) {
  // 入参: text 工具结果展示文本
  // 方法: 规范换行和空白，保证摘要紧凑但不丢失主要语义
  // 出参: string
  return String(text || '')
    .replace(/\r\n/g, '\n')
    .split('\n')
    .map((line) => line.trim().replace(/\s+/g, ' '))
    .filter(Boolean)
    .join('\n')
}

function compactDisplayText(text, { maxChars = 360, maxLines = 6 } = {}) {
  // 入参: text 原始展示文本, options 压缩上限
  // 方法: 去掉空行、压缩连续空白、限制行数和总长度；只影响 UI 展示
  // 出参: string
  const normalized = String(text || '')
    .replace(/\r\n/g, '\n')
    .split('\n')
    .map((line) => line.trim().replace(/\s+/g, ' '))
    .filter(Boolean)
  const lines = normalized.slice(0, maxLines)
  let result = lines.join('\n')
  if (normalized.length > maxLines) result += '\n...'
  if (result.length > maxChars) result = `${result.slice(0, maxChars).trim()}...`
  return result
}

function formatToolArgs(args) {
  // 入参: args 工具调用参数
  // 方法: JSON 格式化并限制长度，避免长路径或大对象撑乱消息列
  // 出参: string
  if (!args) return ''
  const text = JSON.stringify(args, null, 2)
  return text.length > 420 ? `${text.slice(0, 420)}\n...` : text
}
</script>

<template>
  <div class="assistant-steps">
    <template v-for="(step, index) in steps" :key="stepKey(step, index)">
      <div v-if="step.type === 'thinking' && step.text" class="trace-card thinking-card">
        <button
          type="button"
          class="trace-head trace-toggle"
          :class="{ expanded: isExpanded(stepKey(step, index)) }"
          @click.stop="toggleStep(stepKey(step, index))"
        >
          <span class="trace-icon">?</span>
          <span class="trace-summary">{{ thinkingSummary(step) }}</span>
          <span class="trace-chevron">›</span>
        </button>
        <pre v-show="isExpanded(stepKey(step, index))" class="trace-body thinking-body">{{ compactDisplayText(step.text, { maxChars: streaming ? 900 : 520, maxLines: streaming ? 14 : 8 }) }}</pre>
      </div>

      <div v-else-if="step.type === 'tool' && step.call" class="trace-card tool-call-group">
        <button
          type="button"
          class="trace-head trace-toggle"
          :class="{ expanded: isExpanded(stepKey(step, index)) }"
          @click.stop="toggleStep(stepKey(step, index))"
        >
          <span class="trace-icon run">></span>
          <span class="trace-summary trace-tool-name">{{ toolSummary(step.call) }}</span>
          <span class="trace-chevron">›</span>
        </button>
        <div v-show="isExpanded(stepKey(step, index))" class="tool-call-list">
          <div class="tool-call-card">
            <div class="tool-call-head">
              <span class="tool-call-index">#{{ index + 1 }}</span>
              <code>{{ step.call.name || 'unknown_tool' }}</code>
            </div>
            <pre v-if="step.call.args" class="tool-call-args">{{ formatToolArgs(step.call.args) }}</pre>
            <div v-if="step.call.result" class="tool-inline-result">
              <div class="tool-inline-result-head">
                <span>工具结果</span>
                <span class="tool-result-status" :class="getToolCallResult(step.call).status">{{ getToolCallResult(step.call).status }}</span>
              </div>
              <div class="tool-result-summary">{{ getToolCallResult(step.call).summary }}</div>
            </div>
          </div>
        </div>
      </div>

      <div v-else-if="step.type === 'content' && step.text" class="message-bubble assistant-content-bubble">
        <div class="content-text" v-html="renderMarkdown(step.text)" @click="onContentClick"></div>
        <span v-if="shouldShowCursor(index)" class="cursor">▋</span>
        <slot name="content-actions"></slot>
      </div>

      <div v-else-if="step.type === 'image' && step.url" class="chat-images">
        <div class="chat-image-item">
          <div v-if="step.caption" class="chat-image-caption">{{ step.caption }}</div>
          <el-image
            class="chat-image-thumb"
            :src="step.url"
            :preview-src-list="imageUrls"
            :initial-index="imageUrls.indexOf(step.url)"
            preview-teleported
            fit="cover"
            hide-on-click-modal
          />
        </div>
      </div>
    </template>

    <div v-if="hasError || isStopped" class="message-bubble assistant-content-bubble">
      <div v-if="hasError" class="error-text">error: {{ message.error }}</div>
      <div v-if="isStopped" class="stopped-text">（已停止）</div>
    </div>
  </div>
</template>

<style scoped>
.assistant-steps {
  width: 100%;
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-width: 0;
}

.message-bubble {
  max-width: 100%;
  padding: 10px 12px;
  border-radius: var(--r-shell);
  font-size: 12px;
  font-weight: 400;
  line-height: 1.7;
  word-wrap: break-word;
  overflow-wrap: break-word;
}

.assistant-content-bubble {
  margin-top: 0;
  background: var(--bg-elevated);
  color: var(--text-primary);
  border: 1px solid var(--border);
  box-shadow: 0 2px 8px color-mix(in srgb, var(--text-primary) 6%, transparent);
}

.trace-card {
  width: 100%;
  box-sizing: border-box;
  background: #fff;
  border: 1px solid rgba(157, 188, 213, 0.78);
  border-left: 1px solid rgba(157, 188, 213, 0.78);
  border-radius: var(--r-shell);
  padding: 7px 9px;
  box-shadow: none;
}

.trace-card.tool-call-group {
  border-left-color: rgba(157, 188, 213, 0.78);
  background: #fff;
}

.trace-head {
  width: 100%;
  display: flex;
  align-items: center;
  gap: 8px;
  min-height: 18px;
  font-size: 12px;
  color: var(--text-muted);
  user-select: none;
  list-style: none;
}

.trace-toggle {
  padding: 0;
  border: none;
  background: transparent;
  cursor: pointer;
  text-align: left;
}

.trace-toggle:hover {
  color: var(--text-primary);
}

.trace-toggle:hover .trace-icon {
  background: var(--bg-hover);
}

.trace-icon {
  width: 16px;
  height: 16px;
  border-radius: var(--r-xs);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background: var(--bg-inset);
  color: var(--text-muted);
  font-family: var(--mono);
  font-size: var(--fs-xs);
  font-weight: 700;
  flex-shrink: 0;
}

.trace-icon.run {
  background: var(--bg-inset);
  color: var(--text-muted);
}

.trace-summary {
  font-family: var(--sans);
  font-size: 12px;
  font-weight: 600;
  flex: 1;
  min-width: 0;
  color: var(--text-secondary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.trace-tool-name {
  padding: 1px 5px;
  border-radius: var(--r-xs);
  background: transparent;
  color: var(--text-secondary);
  font-family: inherit;
  font-size: inherit;
}

.trace-chevron {
  width: 16px;
  height: 16px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  color: var(--text-muted);
  font-family: var(--mono);
  font-size: 16px;
  line-height: 1;
  transition: transform var(--t-base) var(--ease-out);
}

.trace-toggle.expanded .trace-chevron {
  transform: rotate(90deg);
}

.trace-body {
  margin-top: 7px;
  max-height: 260px;
  overflow: auto;
  background: #fff;
  border: 1px solid var(--border);
  padding: 8px 12px;
  border-radius: var(--r-sm);
  font-size: 12px;
  color: var(--text-secondary);
  font-family: var(--mono);
  white-space: pre-wrap;
  word-break: break-word;
}

.thinking-body {
  border-left: 1px solid var(--border);
}

.tool-call-list {
  margin-top: 7px;
  max-height: 260px;
  overflow: auto;
  display: flex;
  flex-direction: column;
  gap: 7px;
}

.tool-call-card {
  background: #fff;
  border: 1px solid var(--border);
  padding: 8px 10px;
  border-radius: var(--r-sm);
}

.tool-call-head {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  color: var(--text-secondary);
  font-family: var(--mono);
}

.tool-call-index {
  color: var(--text-muted);
  font-size: 12px;
}

.tool-call-args {
  margin-top: 6px;
  max-height: 220px;
  overflow: auto;
  padding: 7px 9px;
  border-radius: var(--r-sm);
  background: #fff;
  border: 1px solid rgba(157, 188, 213, 0.45);
  color: var(--text-secondary);
  font-family: var(--mono);
  font-size: 12px;
  line-height: 1.55;
  white-space: pre-wrap;
  word-break: break-word;
}

.tool-inline-result {
  margin-top: 8px;
  padding: 8px 10px;
  border-radius: var(--r-sm);
  border: 1px solid var(--border);
  background: rgba(233, 242, 249, 0.68);
}

.tool-inline-result-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 5px;
  color: var(--text-secondary);
  font-size: 12px;
  font-weight: 600;
}

.tool-result-status {
  margin-left: auto;
  padding: 1px 6px;
  border-radius: 999px;
  background: var(--bg-inset);
  color: var(--text-secondary);
  font-size: 12px;
  font-family: var(--mono);
}

.tool-result-status.error,
.tool-result-status.failed {
  background: rgba(207, 68, 68, 0.1);
  color: var(--accent-red);
}

.tool-result-summary {
  color: var(--text-primary);
  font-size: 12px;
  line-height: 1.65;
  white-space: pre-wrap;
  word-break: break-word;
}

.cursor {
  color: var(--accent);
  animation: blink 1s step-end infinite;
}

@keyframes blink {
  50% { opacity: 0; }
}

.error-text {
  color: var(--accent-red);
  font-family: var(--mono);
}

.stopped-text {
  color: var(--accent-amber);
  font-size: 12px;
}

.chat-images {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  max-width: 100%;
}

.chat-image-item {
  display: flex;
  flex-direction: column;
  gap: 4px;
  background: var(--bg-elevated);
  border: 1px solid var(--border);
  border-radius: var(--r-shell);
  padding: 6px;
  max-width: 260px;
}

.chat-image-caption {
  font-size: 12px;
  color: var(--text-secondary);
  word-break: break-word;
}

.chat-image-thumb {
  width: 100%;
  max-width: 240px;
  max-height: 200px;
  border-radius: var(--r-sm);
  cursor: zoom-in;
  display: block;
}

.chat-image-thumb :deep(img) {
  max-height: 200px;
  object-fit: cover;
}
</style>
