<script setup>
// ==================== 聊天面板 ====================
// ★ 对应原生 chat/ 面板：welcome 欢迎页 + 消息列表 + 输入区
//   样式一比一对齐原生 .ai-chat / .welcome-area / .ai-input-area
import { ref, computed, watch, nextTick, onBeforeUnmount, useTemplateRef } from 'vue'
import { storeToRefs } from 'pinia'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useConversationStore } from '@/stores/conversation'
import { useWebSocket } from '@/composables/useWebSocket'
import { useChat } from '@/composables/useChat'
import { useMap } from '@/composables/useMap'
import { uploadSamsegImages, publishGeoServerLayer, getLayerBbox, getGeoServerServices } from '@/services/api'
import { useProjectStore } from '@/stores/project'
import MessageBubble from './MessageBubble.vue'
import TaskProgressBar from './TaskProgressBar.vue'
import TaskMonitorDialog from './TaskMonitorDialog.vue'

// 任务日志监控弹窗显隐（纯只读查看器，对齐原生 task-monitor.js）
const taskDialogVisible = ref(false)

const store = useConversationStore()
const projectStore = useProjectStore()
const { active, wsStatus, activeConvId } = storeToRefs(store)
const ws = useWebSocket()
const { send, stop, regenerate } = useChat()
const map = useMap()

const input = ref('')
const scrollRef = useTemplateRef('scrollRef')
const pendingImages = ref([])
const pendingImagePreviewUrls = ref(new Map())
const pendingLayers = ref([])
const layerPickerVisible = ref(false)
const layerPickerLoading = ref(false)
const layerPickerSearch = ref('')
const layerTree = ref(null)

// 输入框默认高度（发送后复位目标；与 CSS .ai-textarea height 对齐）
const DEFAULT_INPUT_H = 58

// 快捷问题（对齐原生 .quick-questions）
const quickQuestions = [
  { label: 'GeoServer 服务', prompt: '列出 GeoServer 所有服务' },
  { label: '数据库表', prompt: '列出数据库所有表' },
  { label: '查看影像', prompt: '显示图层示例影像' },
  { label: '我的记忆', prompt: '你记住了关于我的哪些信息？' },
]

// 是否展示 welcome（无激活对话或无消息）
const showWelcome = computed(() => !active.value || active.value.messages.length === 0)
const rasterLayerOptions = computed(() => {
  const workspaces = layerTree.value?.workspaces || []
  const kw = layerPickerSearch.value.trim().toLowerCase()
  return workspaces
    .flatMap((ws) => {
      const layers = ws.layers_typed || ws.layers || []
      return layers.map((l) => {
        const name = typeof l === 'string' ? l : l.name
        const type = typeof l === 'string' ? 'unknown' : l.type || 'unknown'
        return {
          workspace: ws.workspace,
          layer_name: name,
          full_name: `${ws.workspace}:${name}`,
          type,
          source: 'geoserver',
        }
      })
    })
    .filter((l) => l.type === 'raster')
    .filter((l) => !kw || l.full_name.toLowerCase().includes(kw))
})

function onEnter(e) {
  if (e.shiftKey) return
  e.preventDefault()
  onSend()
}

function onSend() {
  // 入参: 无，读取当前输入、附件及待选图层。
  // 方法: 仅在发送成功后清空编辑区，连接抖动时保留用户尚未发出的内容。
  // 出参: 无；发送失败不会改变输入和附件状态。
  const text = input.value.trim()
  if (!text) return
  const images = pendingImages.value.slice()
  const selectedLayers = pendingLayers.value.slice()
  const sent = send({ prompt: text, images, selectedLayers })
  if (!sent) return
  input.value = ''
  releaseAllPendingImagePreviews()
  pendingImages.value = []
  pendingLayers.value = []
  layerPickerVisible.value = false
  resetHeight()
}

function onQuick(prompt) {
  // 入参: prompt 快捷问题文本。
  // 方法: 复用发送编排；断线时不创建本地伪消息。
  // 出参: 无。
  send({ prompt })
}

// 入参: 无
// 方法: 拉取 GeoServer 服务树并打开 @ 图层选择器；只在首次打开或树为空时加载。
// 出参: Promise<void>
async function openLayerPicker() {
  layerPickerVisible.value = true
  if (layerTree.value || layerPickerLoading.value) return
  layerPickerLoading.value = true
  try {
    layerTree.value = await getGeoServerServices()
  } catch (e) {
    ElMessage.error('图层列表加载失败')
  } finally {
    layerPickerLoading.value = false
  }
}

// 入参: layer GeoServer 栅格图层描述对象。
// 方法: 去重写入 pendingLayers，并把输入框中触发用的 @ 替换为可读图层引用。
// 出参: 无
function selectLayer(layer) {
  if (!layer?.full_name) return
  if (!pendingLayers.value.some((l) => l.full_name === layer.full_name)) {
    pendingLayers.value.push(layer)
  }
  const raw = input.value
  const atIndex = raw.lastIndexOf('@')
  if (atIndex >= 0) {
    input.value = `${raw.slice(0, atIndex)}@${layer.full_name} ${raw.slice(atIndex + 1).replace(/^\S*/, '')}`.trimStart()
  } else {
    input.value = `${raw} @${layer.full_name}`.trim()
  }
  layerPickerVisible.value = false
}

// 入参: index 待移除的附件下标。
// 方法: 从待发送上传影像列表移除对应项。
// 出参: 无
function removePendingImage(index) {
  releasePendingImagePreview(pendingImages.value[index])
  pendingImages.value.splice(index, 1)
}

// 入参: image 待发送影像附件。
// 方法: 优先返回与用户所选 File 一一对应的本地对象 URL，未命中时回退服务器预览地址。
// 出参: string，当前附件的缩略图地址。
function getPendingImagePreview(image) {
  return pendingImagePreviewUrls.value.get(image?.path) || image?.preview_url || ''
}

// 入参: image 后端已保存的影像附件，需包含 preview_url。
// 方法: 禁用 HTTP 缓存逐项获取已转码的 PNG，并生成当前附件独立的对象 URL。
// 出参: Promise<string>；预览不可用时返回空字符串，由界面回退服务器地址。
function createPendingImagePreview(image) {
  if (!image?.preview_url) return Promise.resolve('')
  return fetch(image.preview_url, { cache: 'no-store' })
    .then((response) => response.ok ? response.blob() : null)
    .then((blob) => blob ? URL.createObjectURL(blob) : '')
    .catch(() => '')
}

// 入参: image 待移除或已发送的影像附件。
// 方法: 撤销该附件的本地对象 URL，并删除路径到 URL 的对应关系。
// 出参: 无。
function releasePendingImagePreview(image) {
  const previewUrl = pendingImagePreviewUrls.value.get(image?.path)
  if (!previewUrl) return
  URL.revokeObjectURL(previewUrl)
  pendingImagePreviewUrls.value.delete(image.path)
}

// 入参: 无。
// 方法: 批量撤销待发送区的本地对象 URL，防止发送、切换或卸载后持有文件内存。
// 出参: 无。
function releaseAllPendingImagePreviews() {
  pendingImagePreviewUrls.value.forEach((previewUrl) => URL.revokeObjectURL(previewUrl))
  pendingImagePreviewUrls.value.clear()
}

// 入参: index 待移除的图层下标。
// 方法: 从待发送图层列表移除对应项。
// 出参: 无
function removePendingLayer(index) {
  pendingLayers.value.splice(index, 1)
}

// 入参: msg(MessageBubble 派发的 AI 消息对象，含 nodeId)
// 方法: 弹编辑框预填该 AI 上一条 human 的 content → 用户编辑 → regenerate(fork + 重发)
//   预填值：从 active.messages 找 msg 前最近 human 的 content；无则留空
// 出参: 无
async function onRegenerate(msg) {
  const msgs = active.value?.messages || []
  const idx = msgs.findIndex((m) => m.id === msg.id)
  let prefill = ''
  for (let i = idx - 1; i >= 0; i--) {
    if (msgs[i].role === 'human') { prefill = msgs[i].content; break }
  }
  const { value } = await ElMessageBox.prompt('编辑后重新生成（将分叉当前分支）', '重新生成', {
    inputValue: prefill,
    confirmButtonText: '重新生成',
    cancelButtonText: '取消',
    inputType: 'textarea',
  })
  const prompt = (value || '').trim()
  if (!prompt) return
  try {
    await regenerate({ nodeId: msg.nodeId, prompt })
  } catch (e) {
    ElMessage.error('重新生成失败: ' + e.message)
  }
}

// 入参: 无（内部创建 file input）
// 方法: 选文件 → await 上传(等落盘) → 逐张 await 发 GeoServer(持久化) → 刷新图层管理 → WMS 叠加显示
//   每步严格 await，杜绝「影像还没进服务器就渲染」；发布成功才进图层管理且刷新后仍在
// 出参: 无
function onUpload() {
  const fileInput = document.createElement('input')
  fileInput.type = 'file'
  fileInput.multiple = true
  fileInput.onchange = async () => {
    const files = Array.from(fileInput.files || [])
    if (!files.length) return
    try {
      const data = await uploadSamsegImages(files, activeConvId.value)
      const paths = data.paths || []
      const uploadedFiles = Array.isArray(data.files)
        ? data.files
        : paths.map((p) => ({ name: p.split(/[\\/]/).pop(), path: p, preview_url: '', source: 'upload' }))
      const previewUrls = await Promise.all(uploadedFiles.map(createPendingImagePreview))
      previewUrls.forEach((previewUrl, index) => {
        if (previewUrl) pendingImagePreviewUrls.value.set(uploadedFiles[index].path, previewUrl)
      })
      pendingImages.value.push(...uploadedFiles)
      // 逐张发布到 GeoServer（必须 await 成功，否则图层管理无该图层）
      for (const p of paths) {
        const pub = await publishGeoServerLayer({ file_path: p, layer_type: 'raster' })
        if (pub.status !== 'success') {
          ElMessage.warning(`发布失败: ${pub.msg || '未知原因'}`)
          continue
        }
        // 取 bbox 定位 + WMS 叠加显示（zIndex 10，在矢量之下、底图之上）
        let bbox = null
        try {
          const bb = await getLayerBbox(pub.workspace, pub.layer_name)
          if (bb.status === 'success') bbox = bb.bbox
        } catch {}
        map.addWmsLayer({ name: pub.layer_name, workspace: pub.workspace, bbox, layerType: 'raster' })
      }
      // 触发图层管理面板自动刷新（新图层立刻可见）
      projectStore.bumpLayerVersion()
      ElMessage.success(`已上传 ${data.count} 张影像`)
    } catch (e) {
      ElMessage.error('上传失败: ' + e.message)
    }
  }
  fileInput.click()
}

// 入参: e textarea input 事件
// 方法: 自适应高度；仅当输入末尾处于 @ 触发态时打开图层选择器，删除 @ 后自动关闭。
// 出参: 无
function onInput(e) {
  const el = e.target
  el.style.height = 'auto'
  el.style.height = Math.min(el.scrollHeight, 220) + 'px'
  const text = input.value
  const shouldOpenPicker = /(^|\s)@$/.test(text)
  if (shouldOpenPicker) {
    openLayerPicker()
  } else {
    layerPickerVisible.value = false
  }
}

// 入参: msg 用户消息对象，包含 content、nodeId 及附件上下文。
// 方法: 校验任务状态，弹出预填编辑框；确认后从该用户节点之前分叉并重新发送。
// 出参: Promise<void>；取消编辑或输入为空时不修改对话。
async function onEdit(msg) {
  if (active.value?.isSending) {
    ElMessage.warning('当前任务正在执行，请先停止后再编辑')
    return
  }
  let value
  try {
    const result = await ElMessageBox.prompt('修改消息后，将重新生成此处之后的对话', '编辑消息', {
      inputValue: msg.content || '',
      confirmButtonText: '重新发送',
      cancelButtonText: '取消',
      inputType: 'textarea',
    })
    value = result.value
  } catch (action) {
    if (action === 'cancel' || action === 'close') return
    throw action
  }
  const prompt = (value || '').trim()
  if (!prompt) return
  try {
    const sent = await regenerate({
      nodeId: msg.nodeId,
      prompt,
      replace: true,
      images: Array.isArray(msg.images) ? msg.images : [],
      selectedLayers: Array.isArray(msg.selectedLayers) ? msg.selectedLayers : [],
    })
    if (!sent) ElMessage.warning('消息未发送，请检查连接状态')
  } catch (e) {
    ElMessage.error('编辑消息失败: ' + e.message)
  }
}
// 发送后复位到默认高度
function resetHeight() {
  nextTick(() => {
    const el = scrollRef.value?.parentElement?.querySelector('.ai-textarea')
    if (el) el.style.height = DEFAULT_INPUT_H + 'px'
  })
}

watch(
  () => [active.value?.messages.length, active.value?.streamTick],
  () => nextTick(scrollToBottom),
)

function scrollToBottom() {
  const el = scrollRef.value
  if (el) el.scrollTop = el.scrollHeight
}

onBeforeUnmount(releaseAllPendingImagePreviews)
</script>

<template>
  <div class="right-sidebar shell-fade-in">
    <!-- AI 头部（对齐原生 .ai-header） -->
    <div class="ai-header">
      <div class="ai-title">
        <div class="ai-avatar">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <rect x="3" y="11" width="18" height="10" rx="2" />
            <circle cx="12" cy="5" r="2" />
            <path d="M12 7v4" />
            <line x1="8" y1="16" x2="8" y2="16.01" />
            <line x1="16" y1="16" x2="16" y2="16.01" />
          </svg>
        </div>
        <span>GeoAI Copilot</span>
      </div>
      <!-- 头部操作区（对齐原生 .ai-actions）：任务日志监控弹窗 -->
      <div class="ai-actions">
        <button class="ai-action-btn" title="任务日志监控" @click="taskDialogVisible = true">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <rect x="3" y="4" width="18" height="16" rx="2" />
            <line x1="8" y1="10" x2="8" y2="10" />
            <line x1="8" y1="14" x2="8" y2="14" />
            <line x1="12" y1="10" x2="16" y2="10" />
            <line x1="12" y1="14" x2="16" y2="14" />
          </svg>
        </button>
      </div>
    </div>

    <!-- 消息滚动区（对齐原生 .ai-chat） -->
    <div ref="scrollRef" class="ai-chat">
      <!-- 欢迎页 -->
      <div v-if="showWelcome" class="welcome-area">
        <div class="welcome-title">我是国土智察 AI 助手<br>可以帮你查看影像、分析数据、管理图层</div>
        <div class="quick-questions">
          <button
            v-for="q in quickQuestions"
            :key="q.label"
            class="quick-question"
            @click="onQuick(q.prompt)"
          >{{ q.label }}</button>
        </div>
      </div>

      <template v-else>
        <MessageBubble
          v-for="m in active.messages"
          :key="m.id"
          :message="m"
          :streaming="m.status === 'streaming'"
          @regenerate="onRegenerate"
          @edit="onEdit"
        />
        <!-- 重工具执行进度条（tool_call 触发轮询写入 activeTask，done 清除） -->
        <TaskProgressBar v-if="active.activeTask" :task="active.activeTask" />
      </template>
    </div>

    <!-- 输入区（对齐原生 .ai-input-area） -->
    <div class="ai-input-area">
      <div class="input-wrapper vertical">
        <div v-if="pendingImages.length || pendingLayers.length" class="pending-attachments">
          <div
            v-for="(img, idx) in pendingImages"
            :key="`${img.path}-${idx}`"
            class="pending-attachment image"
          >
            <img v-if="getPendingImagePreview(img)" class="pending-thumb" :src="getPendingImagePreview(img)" :alt="img.name" />
            <span v-else class="pending-file-icon">▦</span>
            <span class="pending-name" :title="img.name">{{ img.name }}</span>
            <button class="pending-remove" title="移除影像" @click="removePendingImage(idx)">×</button>
          </div>
          <div
            v-for="(lyr, idx) in pendingLayers"
            :key="lyr.full_name"
            class="pending-attachment layer"
          >
            <span class="pending-name" :title="lyr.full_name">{{ lyr.full_name }}</span>
            <button class="pending-remove" title="移除图层" @click="removePendingLayer(idx)">×</button>
          </div>
        </div>
        <div class="input-box">
          <div v-if="layerPickerVisible" class="layer-mention-popover">
            <input
              v-model="layerPickerSearch"
              class="layer-mention-search"
              placeholder="搜索栅格图层"
            />
            <div v-if="layerPickerLoading" class="layer-mention-empty">加载中...</div>
            <div v-else-if="!rasterLayerOptions.length" class="layer-mention-empty">暂无可选栅格图层</div>
            <template v-else>
              <button
                v-for="lyr in rasterLayerOptions"
                :key="lyr.full_name"
                class="layer-mention-item"
                @click="selectLayer(lyr)"
              >
                <span class="layer-mention-name">{{ lyr.full_name }}</span>
              </button>
            </template>
          </div>
            <textarea
              v-model="input"
              class="ai-textarea"
              rows="1"
              placeholder="输入消息 (Enter 发送, Shift+Enter 换行)"
              @keydown.enter="onEnter"
              @input="onInput"
              style="width: 100%; box-sizing: border-box;"
            ></textarea>
        </div>
        <div class="controls">
          <button class="input-upload-btn small" title="上传资料" @click="onUpload">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
              <polyline points="17 8 12 3 7 8" />
              <line x1="12" y1="3" x2="12" y2="15" />
            </svg>
          </button>
          <div class="spacer"></div>
          <button
            v-if="!active || !active.isSending"
            class="send-btn small"
            :disabled="wsStatus !== 'connected' || !input.trim()"
            @click="onSend"
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <line x1="22" y1="2" x2="11" y2="13" />
              <polygon points="22 2 15 22 11 13 2 9 22 2" />
            </svg>
          </button>
          <button v-else class="stop-btn small" @click="stop">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor">
              <rect x="4" y="4" width="16" height="16" rx="2" />
            </svg>
          </button>
        </div>
      </div>
    </div>
    <!-- 任务日志监控弹窗（纯只读，Teleport 到 body） -->
    <TaskMonitorDialog v-model:visible="taskDialogVisible" :conversation-id="activeConvId" />
  </div>
</template>

<style scoped>
.right-sidebar {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
}

/* ===== AI 头部（对齐原生 .ai-header） ===== */
.ai-header {
  padding: 0 14px;
  height: 44px;
  box-sizing: border-box;
  border-bottom: 1px solid var(--border);
  display: flex;
  align-items: center;
  justify-content: space-between; /* title 靠左 / actions 靠右 */
  background: var(--bg-panel);
  flex-shrink: 0;
}
.ai-actions {
  display: flex;
  align-items: center;
  gap: 4px;
}
.ai-action-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border: none;
  background: transparent;
  color: var(--text-muted);
  border-radius: var(--r-xs);
  cursor: pointer;
  transition: all 0.15s;
}
.ai-action-btn:hover {
  background: var(--bg-hover);
  color: var(--accent);
}
.ai-title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: var(--fs-xl);
  font-weight: 600;
  color: var(--accent);
}
.ai-avatar {
  width: 28px;
  height: 28px;
  background: var(--accent-soft);
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--accent);
  font-size: 12px;
  font-weight: 700;
  font-family: var(--mono);
  box-shadow: 0 2px 6px rgba(15, 35, 58, 0.12);
}

/* ===== 消息区（对齐原生 .ai-chat） ===== */
.ai-chat {
  flex: 1;
  overflow-y: auto;
  padding: 12px 14px 14px;
  display: flex;
  flex-direction: column;
  gap: 10px;
  background:
    linear-gradient(180deg, rgba(241, 247, 252, 0.68), rgba(255, 255, 255, 0) 96px),
    var(--bg-base);
  scroll-behavior: smooth;
}

/* ===== 欢迎页（对齐原生 .welcome-area） ===== */
.welcome-area {
  text-align: center;
  padding: 20px 0 10px;
}
.welcome-title {
  font-size: var(--fs-lg);
  color: var(--text-secondary);
  margin-bottom: 12px;
  line-height: 1.65;
}
.quick-questions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  justify-content: center;
}
.quick-question {
  padding: 6px 12px;
  background: var(--bg-elevated);
  border: 1px solid var(--border);
  border-radius: 999px;
  font-size: var(--fs-md);
  color: var(--primary);
  cursor: pointer;
  transition: all var(--t-base) var(--ease-out);
  font-weight: 500;
  font-family: var(--sans);
}
.quick-question:hover {
  border-color: var(--primary);
  background: #f0f7ff;
  box-shadow: 0 2px 6px rgba(43, 125, 233, 0.1);
}

/* ===== 输入区（对齐原生 .ai-input-area） ===== */
.ai-input-area {
  padding: 10px 12px;
  border-top: 1px solid var(--border);
  background: var(--bg-panel);
  flex-shrink: 0;
}
.input-wrapper {
  display: flex;
  flex-direction: column;
  gap: 6px;
  align-items: stretch;
  background: var(--bg-base);
  padding: 7px;
  border-radius: var(--r-shell);
  border: 1px solid var(--border-strong);
  transition: border-color var(--t-base) var(--ease-out), box-shadow var(--t-base) var(--ease-out);
  box-shadow: 0 1px 0 rgba(255, 255, 255, 0.6) inset;
}
.input-wrapper:focus-within {
  border-color: var(--primary);
  box-shadow: none; /* remove light focus shadow */
}

/* Ensure internal focusable elements do not show global focus shadow */
.input-wrapper :focus-visible {
  box-shadow: none !important;
  outline: none !important;
}
.ai-textarea {
  flex: 1;
  border: none;
  background: transparent;
  padding: 6px 4px;
  font-size: var(--fs-lg);
  resize: none;
  outline: none;
  font-family: inherit;
  height: 58px;
  min-height: 36px;
  max-height: 320px;
  color: var(--text-primary);
  line-height: 1.5;
}
.ai-textarea::placeholder {
  color: var(--text-muted);
}
.input-upload-btn {
  width: 30px;
  height: 30px;
  border-radius: 6px;
  background: transparent;
  color: var(--text-muted);
  border: none;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  transition: all var(--t-base) var(--ease-out);
  flex-shrink: 0;
}
.input-upload-btn:hover {
  background: var(--bg-hover);
  color: var(--primary);
}
.send-btn {
  width: 34px;
  height: 34px;
  border-radius: 8px;
  background: var(--primary);
  color: #fff;
  border: none;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  transition: all var(--t-base) var(--ease-out);
  flex-shrink: 0;
  box-shadow: 0 2px 6px rgba(43, 125, 233, 0.25);
  font-size: 15px;
}
.send-btn:hover {
  background: var(--primary-dark);
  transform: translateY(-1px);
}
.send-btn:disabled {
  opacity: 0.45;
  cursor: not-allowed;
  transform: none;
  box-shadow: none;
}
.stop-btn {
  width: 34px;
  height: 34px;
  background: var(--bg-elevated);
  color: var(--accent-red);
  border: 1px solid var(--border-strong);
  border-radius: 8px;
  cursor: pointer;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 13px;
}
.stop-btn:hover {
  background: rgba(239, 68, 68, 0.1);
  border-color: var(--accent-red);
}

/* Vertical layout: controls row below textarea */
.input-box {
  width: 100%;
  display: flex;
  position: relative;
}
.controls {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
}
.controls .spacer {
  flex: 1;
}
/* small variant for compact buttons */
.small {
  width: 22px;
  height: 22px;
  padding: 0;
}
.input-upload-btn.small,
.send-btn.small,
.stop-btn.small {
  border-radius: 6px;
  font-size: 12px;
}
.send-btn.small {
  box-shadow: 0 1px 3px rgba(43, 125, 233, 0.2);
}
.pending-attachments {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  padding: 2px 0 4px;
}
.pending-attachment {
  max-width: 100%;
  height: 32px;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 3px 6px;
  border: 1px solid var(--border);
  border-radius: var(--r-sm);
  background: var(--bg-elevated);
  color: var(--text-secondary);
  font-size: var(--fs-sm);
}
.pending-thumb {
  width: 26px;
  height: 26px;
  border-radius: var(--r-xs);
  object-fit: cover;
  background: var(--bg-inset);
  flex-shrink: 0;
}
.pending-file-icon {
  width: 22px;
  height: 22px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  color: var(--accent);
  background: var(--accent-soft);
  border-radius: var(--r-xs);
  flex-shrink: 0;
}
.pending-name {
  min-width: 0;
  max-width: 190px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.pending-remove {
  width: 18px;
  height: 18px;
  border: none;
  border-radius: 50%;
  background: transparent;
  color: var(--text-muted);
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}
.pending-remove:hover {
  background: var(--bg-hover);
  color: var(--accent-red);
}
.layer-mention-popover {
  position: absolute;
  left: 0;
  bottom: calc(100% + 8px);
  width: min(360px, 100%);
  max-height: 260px;
  overflow: auto;
  padding: 8px;
  border: 1px solid var(--border-strong);
  border-radius: var(--r-shell);
  background: var(--bg-panel);
  box-shadow: 0 8px 24px rgba(15, 35, 58, 0.16);
  z-index: 20;
}
.layer-mention-search {
  width: 100%;
  box-sizing: border-box;
  height: 28px;
  padding: 4px 8px;
  margin-bottom: 6px;
  border: 1px solid var(--border);
  border-radius: var(--r-sm);
  outline: none;
  color: var(--text-primary);
  background: var(--bg-base);
  font-size: var(--fs-sm);
}
.layer-mention-item {
  width: 100%;
  height: 30px;
  display: flex;
  align-items: center;
  gap: 7px;
  padding: 0 7px;
  border: none;
  border-radius: var(--r-sm);
  background: transparent;
  color: var(--text-primary);
  cursor: pointer;
  text-align: left;
}
.layer-mention-item:hover {
  background: var(--bg-hover);
}
.layer-mention-type {
  color: #f59e0b;
  flex-shrink: 0;
}
.layer-mention-name {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-family: var(--mono);
  font-size: var(--fs-sm);
}
.layer-mention-empty {
  padding: 12px 8px;
  color: var(--text-muted);
  text-align: center;
  font-size: var(--fs-sm);
}
</style>
