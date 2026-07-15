<script setup>
// ==================== 单条消息气泡 ====================
// ★ 对应原生 chat/msg-ui.js 的消息 DOM 结构
//   AI 消息带机器人 SVG 头像；human 消息无头像（与原生一致）
//   正文用 markdown-it 渲染 + 富表格增强（v-html）
import { computed } from 'vue'
import { ElMessage } from 'element-plus'
import { renderMarkdown } from '@/utils/markdown'
import AssistantSteps from './AssistantSteps.vue'

const props = defineProps({
  message: { type: Object, required: true },
  streaming: { type: Boolean, default: false },
})

// emit regenerate / edit: 父组件接住后分别走 fork 重发与编辑重发
const emit = defineEmits(['regenerate', 'edit'])

// 入参: 无
// 方法: 派发 regenerate 事件，携带整条 message（含 nodeId + content 供父组件定位/预填）
// 出参: 无
function onRegenerate() {
  emit('regenerate', props.message)
}

// 入参: 无
// 方法: 派发 edit 事件，携带整条用户消息供父组件弹框编辑后重新发送
// 出参: 无
function onEdit() {
  emit('edit', props.message)
}

const isUser = computed(() => props.message.role === 'human')
const isTool = computed(() => props.message.role === 'tool')
const isAssistant = computed(() => !isUser.value && !isTool.value)
const rendered = computed(() => renderMarkdown(props.message.content))
const copyText = computed(() => (props.message.content || props.message.thinking || '').trim())
const toolResult = computed(() => parseToolResult(props.message.content, props.message.toolName))
const userImages = computed(() => Array.isArray(props.message.images) ? props.message.images : [])
const userLayers = computed(() => Array.isArray(props.message.selectedLayers) ? props.message.selectedLayers : [])

// 入参: 无
// 方法: 复制当前消息主文本到剪贴板；优先复制内容，空内容时复制思考区文本
// 出参: 无
function onCopy() {
  const text = copyText.value
  if (!text) return
  copyTextToClipboard(text)
    .then(() => ElMessage.success('已复制消息'))
    .catch(() => ElMessage.error('复制失败'))
}

// 入参: text 待复制文本
// 方法: 优先使用 Clipboard API；失败时回退到隐藏 textarea + execCommand
// 出参: Promise<void>
function copyTextToClipboard(text) {
  if (navigator.clipboard?.writeText && window.isSecureContext) {
    return navigator.clipboard.writeText(text).catch(() => copyTextWithTextarea(text))
  }

  return copyTextWithTextarea(text)
}

// 入参: text 待复制文本
// 方法: 使用隐藏 textarea 触发浏览器原生命令，作为 Clipboard API 被内置浏览器拦截失败后的本地兜底
// 出参: Promise<void>
function copyTextWithTextarea(text) {
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

// 入参: raw 工具结果原始字符串
// 方法: 尝试解析 JSON，完整展示 status/msg/summary 等结果字段；解析失败则按纯文本工具输出展示
// 出参: { parsed, status, title, summary, detail }
function parseToolResult(raw, fallbackToolName = '') {
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

// 入参: data 工具结果对象
// 方法: 在没有显式摘要字段时，从对象键值生成稳定短摘要
// 出参: string
function summarizeObject(data) {
  const pairs = Object.entries(data)
    .filter(([, value]) => typeof value !== 'object' || value == null)
    .slice(0, 4)
    .map(([key, value]) => `${key}: ${compactDisplayText(String(value), { maxChars: 80, maxLines: 1 })}`)
  return pairs.join('；') || '已返回结构化结果'
}

// 入参: text 工具结果展示文本
// 方法: 仅规范换行和空白，不做长度截断，保证工具结果文字完整显示
// 出参: string
function normalizeResultText(text) {
  return String(text || '')
    .replace(/\r\n/g, '\n')
    .split('\n')
    .map((line) => line.trim().replace(/\s+/g, ' '))
    .filter(Boolean)
    .join('\n')
}

// 入参: text 原始展示文本, options 压缩上限
// 方法: 去掉空行、压缩连续空白、限制行数和总长度；只影响 UI 展示，不改变原始消息
// 出参: 压缩后的展示文本
function compactDisplayText(text, { maxChars = 360, maxLines = 6 } = {}) {
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

</script>

<template>
  <div :class="['message', isUser ? 'user' : isTool ? 'tool' : 'ai']" class="shell-fade-in">
    <div class="message-avatar" :class="{ user: isUser, tool: isTool }">
      <svg v-if="isAssistant" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <rect x="3" y="11" width="18" height="10" rx="2" />
        <circle cx="12" cy="5" r="2" />
        <path d="M12 7v4" />
        <line x1="8" y1="16" x2="8" y2="16.01" />
        <line x1="16" y1="16" x2="16" y2="16.01" />
      </svg>
      <svg v-else-if="isUser" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M20 21a8 8 0 0 0-16 0" />
        <circle cx="12" cy="8" r="4" />
      </svg>
      <svg v-else width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <polyline points="4 17 10 11 4 5" />
        <line x1="12" y1="19" x2="20" y2="19" />
      </svg>
    </div>

    <div class="message-col">
      <template v-if="isUser">
        <div v-if="message.content" class="message-bubble">
          <div class="content-text" v-html="rendered"></div>
        </div>
        <div v-if="userImages.length || userLayers.length" class="user-attachments">
          <div
            v-for="(img, idx) in userImages"
            :key="`${img.path || img.preview_url || img.name}-${idx}`"
            class="user-attachment image"
          >
            <el-image
              v-if="img.preview_url || img.url"
              class="user-attachment-thumb"
              :src="img.preview_url || img.url"
              :preview-src-list="[img.preview_url || img.url]"
              fit="cover"
              preview-teleported
            />
            <span v-else class="user-attachment-icon">▦</span>
            <span class="user-attachment-name" :title="img.name || img.full_name">{{ img.name || img.full_name || '影像' }}</span>
          </div>
          <div
            v-for="lyr in userLayers"
            :key="lyr.full_name"
            class="user-attachment layer"
          >
            <span class="user-attachment-name" :title="lyr.full_name">{{ lyr.full_name }}</span>
          </div>
        </div>
      </template>

      <template v-else-if="isAssistant">
        <AssistantSteps :message="message" :streaming="streaming">
          <template #content-actions>
            <div class="message-actions assistant-content-actions">
              <button class="message-action-btn" title="复制消息" @click="onCopy">
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                  <rect x="9" y="9" width="13" height="13" rx="2" />
                  <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
                </svg>
              </button>
              <button
                v-if="message.nodeId"
                class="message-action-btn"
                title="重新生成"
                @click="onRegenerate"
              >
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                  <path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8" />
                  <path d="M21 3v5h-5" />
                  <path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16" />
                  <path d="M8 16H3v5" />
                </svg>
              </button>
            </div>
          </template>
        </AssistantSteps>
      </template>

      <div v-else class="message-bubble tool-result-bubble">
        <div class="tool-result-head">
          <span class="tool-result-title">{{ toolResult.title }}</span>
          <span class="tool-result-status" :class="toolResult.status">{{ toolResult.status }}</span>
        </div>
        <div class="tool-result-summary">{{ toolResult.summary }}</div>
      </div>

      <div v-if="isUser || isTool" class="message-actions" :class="{ user: isUser }">
        <button class="message-action-btn" title="复制消息" @click="onCopy">
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <rect x="9" y="9" width="13" height="13" rx="2" />
            <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
          </svg>
        </button>
        <button
          v-if="isUser && message.nodeId"
          class="message-action-btn"
          title="编辑并重新发送"
          @click="onEdit"
        >
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M12 20h9" />
            <path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z" />
          </svg>
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.message {
  display: flex;
  gap: 8px;
  max-width: 100%;
  padding: 1px 0;
}
.message.user {
  flex-direction: row-reverse;
}
.message.ai {
  flex-direction: row;
  align-items: flex-start;
}
.message.tool {
  flex-direction: row;
  align-items: flex-start;
}

/* AI 头像（对齐原生 .message-avatar） */
.message-avatar {
  width: 24px;
  height: 24px;
  border-radius: 50%;
  background: var(--accent-soft);
  color: var(--accent);
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  border: 1px solid var(--border);
  margin-top: 2px;
}
.message-avatar.user {
  background: var(--bg-panel);
  color: var(--primary);
  border-color: rgba(44, 111, 189, 0.22);
}
.message-avatar.tool {
  background: var(--bg-inset);
  color: var(--accent);
  border-color: var(--border);
}

.message-col {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
  max-width: calc(100% - 34px);
}

.message.user .message-col {
  align-items: flex-end;
  max-width: 82%;
}
.message.ai .message-col {
  align-items: flex-start;
  width: min(88%, 640px);
  max-width: 88%;
}
.message.tool .message-col {
  align-items: flex-start;
  width: min(88%, 640px);
  max-width: 88%;
}

.message-actions {
  display: flex;
  gap: 4px;
  justify-content: flex-start;
  transition:
    opacity var(--t-base) var(--ease-out),
    transform var(--t-base) var(--ease-out);
}
.message-actions.user {
  justify-content: flex-end;
}
.assistant-content-actions {
  margin-top: 8px;
  padding-top: 8px;
  border-top: 1px solid rgba(44, 111, 189, 0.12);
}

/* 气泡（对齐原生 .message-bubble） */
.message-bubble {
  max-width: 100%;
  padding: 10px 12px;
  border-radius: var(--r-shell);
  font-size: var(--fs-lg);
  font-weight: 400;
  line-height: 1.7;
  word-wrap: break-word;
  overflow-wrap: break-word;
}
.message.user .message-bubble {
  background: var(--primary);
  color: #fff;
  border-bottom-right-radius: 4px;
  box-shadow: 0 2px 8px rgba(43, 125, 233, 0.16);
}
.message.ai .message-bubble {
  background: var(--bg-elevated);
  color: var(--text-primary);
  border: 1px solid rgba(157, 188, 213, 0.78);
  border-bottom-left-radius: 4px;
  box-shadow: 0 2px 8px rgba(15, 35, 58, 0.06);
}
.message.tool .message-bubble {
  background: var(--bg-panel);
  color: var(--text-primary);
  border: 1px solid var(--border);
  border-bottom-left-radius: 4px;
  box-shadow: 0 1px 3px rgba(15, 35, 58, 0.04);
}

/* 分段块：回复正文 */
.section-title {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: var(--fs-md);
  font-weight: 600;
  color: var(--accent);
  margin-bottom: 6px;
}
.content-title {
  margin-top: 2px;
}

/* 轨迹栈：思考过程 / 工具调用与最终回复分离显示 */
.trace-stack {
  width: 100%;
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-width: 0;
}
.trace-card {
  width: 100%;
  box-sizing: border-box;
  background: rgba(241, 247, 252, 0.72);
  border: 1px solid var(--border);
  border-left: 2px solid var(--accent);
  border-radius: var(--r-shell);
  padding: 7px 9px;
  box-shadow: none;
}
.trace-card.tool-call-group {
  border-left-color: var(--accent);
  background: rgba(241, 247, 252, 0.72);
}
.trace-head {
  width: 100%;
  display: flex;
  align-items: center;
  gap: 8px;
  min-height: 18px;
  font-size: var(--fs-md);
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
  background: var(--accent-soft);
  color: var(--accent);
  font-family: var(--mono);
  font-size: var(--fs-xs);
  font-weight: 700;
  flex-shrink: 0;
}
.trace-icon.run {
  background: var(--accent-soft);
  color: var(--accent);
}
.trace-title {
  font-weight: 600;
  flex: 0 0 auto;
  color: var(--text-secondary);
}
.thinking-card .trace-title {
  flex: 1;
}
.trace-tool-name {
  min-width: 0;
  flex: 1;
  padding: 1px 5px;
  border-radius: var(--r-xs);
  background: var(--accent-soft);
  color: var(--accent);
  font-family: var(--mono);
  font-size: var(--fs-xs);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.trace-count {
  min-width: 18px;
  height: 18px;
  padding: 0 6px;
  border-radius: 999px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background: var(--accent-soft);
  color: var(--accent);
  font-family: var(--mono);
  font-size: var(--fs-xs);
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
  background: rgba(255, 255, 255, 0.7);
  border: 1px solid var(--border);
  padding: 8px 12px;
  border-radius: var(--r-sm);
  font-size: var(--fs-md);
  color: var(--text-secondary);
  font-family: var(--mono);
  white-space: pre-wrap;
  word-break: break-word;
}
.thinking-body {
  border-left: 2px solid var(--accent);
}
.assistant-content-bubble {
  margin-top: 0;
}

/* 工具调用卡片 */
.tool-call-list {
  margin-top: 7px;
  max-height: 260px;
  overflow: auto;
  display: flex;
  flex-direction: column;
  gap: 7px;
}
.tool-call-card {
  background: rgba(255, 255, 255, 0.72);
  border: 1px solid var(--border);
  padding: 8px 10px;
  border-radius: var(--r-sm);
}
.tool-call-head {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: var(--fs-md);
  color: var(--accent);
  font-family: var(--mono);
}
.tool-call-index {
  color: var(--text-muted);
  font-size: var(--fs-sm);
}
.tool-call-args {
  margin-top: 6px;
  max-height: 220px;
  overflow: auto;
  padding: 7px 9px;
  border-radius: var(--r-sm);
  background: rgba(255, 255, 255, 0.52);
  border: 1px solid rgba(157, 188, 213, 0.45);
  color: var(--text-secondary);
  font-family: var(--mono);
  font-size: var(--fs-sm);
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
  color: var(--accent);
  font-size: var(--fs-md);
  font-weight: 600;
}

/* 工具结果：聊天面板只显示摘要，原始内容交给任务日志承载 */
.tool-result-bubble {
  width: 100%;
}
.tool-result-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
}
.tool-result-title {
  font-size: var(--fs-md);
  font-weight: 600;
  color: var(--accent);
}
.tool-result-status {
  margin-left: auto;
  padding: 1px 6px;
  border-radius: 999px;
  background: var(--accent-soft);
  color: var(--accent);
  font-size: var(--fs-xs);
  font-family: var(--mono);
}
.tool-result-status.error,
.tool-result-status.failed {
  background: rgba(207, 68, 68, 0.1);
  color: var(--accent-red);
}
.tool-result-summary {
  color: var(--text-primary);
  font-size: var(--fs-md);
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
  font-size: var(--fs-md);
}

/* AI 消息操作区（hover 显示，对齐 ConvSidebar 的 hover 显示约定） */
.msg-ops {
  display: none;
  gap: 8px;
  margin-top: 4px;
  padding-left: 2px;
}
.message.ai:hover .msg-ops {
  display: flex;
}
.msg-op-btn {
  font-size: var(--fs-xs);
  color: var(--text-muted);
  background: transparent;
  border: none;
  cursor: pointer;
  padding: 2px 8px;
  border-radius: var(--r-xs);
  font-family: var(--mono);
  transition: all var(--t-base) var(--ease-out);
}
.msg-op-btn:hover {
  background: var(--bg-hover);
  color: var(--accent);
}

.message-action-btn {
  width: 24px;
  height: 24px;
  border: 1px solid transparent;
  border-radius: 999px;
  background: var(--bg-elevated);
  color: var(--text-muted);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  box-shadow: 0 1px 2px rgba(15, 35, 58, 0.05);
}
.message-action-btn:hover {
  background: var(--bg-hover);
  color: var(--accent);
  border-color: rgba(44, 111, 189, 0.18);
}

/* 沙盒聊天图片: 缩略图卡片 + 点击 el-image 放大预览 */
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
  font-size: var(--fs-sm);
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
.user-attachments {
  max-width: 100%;
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  justify-content: flex-end;
}
.user-attachment {
  max-width: 240px;
  min-height: 34px;
  display: inline-flex;
  align-items: center;
  gap: 7px;
  padding: 6px;
  border: 1px solid rgba(44, 111, 189, 0.18);
  border-radius: var(--r-shell);
  background: var(--bg-panel);
  color: var(--text-secondary);
  box-shadow: 0 1px 3px rgba(15, 35, 58, 0.04);
}
.user-attachment-thumb {
  width: 56px;
  height: 42px;
  border-radius: var(--r-sm);
  cursor: zoom-in;
  flex-shrink: 0;
  background: var(--bg-inset);
}
.user-attachment-thumb :deep(img) {
  width: 56px;
  height: 42px;
  object-fit: cover;
}
.user-attachment-icon {
  width: 28px;
  height: 28px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: var(--r-xs);
  color: var(--accent);
  background: var(--accent-soft);
  flex-shrink: 0;
}
.user-attachment-name {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: var(--fs-sm);
  font-family: var(--mono);
}
</style>
