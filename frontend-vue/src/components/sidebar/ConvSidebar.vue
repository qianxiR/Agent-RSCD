<script setup>
// ==================== 侧栏：对话管理 ====================
// ★ 对应原生 project/sidebar.js 的对话列表 + 分组渲染
//   样式一比一对齐原生 .conv-sidebar / .conversation-card / .project-row
import { computed, nextTick, onMounted, ref } from 'vue'
import { storeToRefs } from 'pinia'
import { ElMessageBox, ElMessage } from 'element-plus'
import { ChatLineRound, Search, Delete, EditPen, Folder, Connection } from '@element-plus/icons-vue'
import { useProjectStore } from '@/stores/project'
import { useConversationStore } from '@/stores/conversation'
import { useChat } from '@/composables/useChat'
import { deleteConversation, patchConversation, createProject } from '@/services/api'

const projectStore = useProjectStore()
const convStore = useConversationStore()
const { projects, grouped, activeProjectId } = storeToRefs(projectStore)
const { activeConvId } = storeToRefs(convStore)
const { newConversation, switchTo } = useChat()
const emit = defineEmits(['open-layer'])
const searchOpen = ref(false)
const searchInputRef = ref(null)
const searchQuery = ref('')
const searchResults = computed(() => {
  const kw = searchQuery.value.trim().toLowerCase()
  const list = projectStore.convList || []
  if (!kw) return list.slice(0, 12)
  return list
    .filter((c) => (c.title || '').toLowerCase().includes(kw) || String(c.id || '').toLowerCase().includes(kw))
    .slice(0, 20)
})

onMounted(() => {
  projectStore.setSearch('')
  projectStore.loadAll()
})

async function onNewConv() {
  newConversation()
}

function openSearch() {
  // 入参: 无
  // 方法: 打开全局搜索浮层，并在 DOM 更新后聚焦输入框
  // 出参: 无
  searchOpen.value = true
  nextTick(() => searchInputRef.value?.focus())
}

function closeSearch() {
  // 入参: 无
  // 方法: 关闭搜索浮层；搜索词只属于浮层，不影响左侧列表
  // 出参: 无
  searchOpen.value = false
}

function onSearchInput(e) {
  // 入参: e 原生 input 事件
  // 方法: 只更新搜索浮层内的本地关键词，左侧列表不实时过滤
  // 出参: 无
  searchQuery.value = e.target.value
}

function clearSearch() {
  // 入参: 无
  // 方法: 清空搜索浮层关键词，并保持输入框聚焦
  // 出参: 无
  searchQuery.value = ''
  nextTick(() => searchInputRef.value?.focus())
}

function selectSearchResult(conv) {
  // 入参: conv 会话摘要对象
  // 方法: 切换到用户在搜索浮层中点击的会话，并关闭浮层
  // 出参: 无
  if (!conv?.id) return
  switchTo(conv.id)
  closeSearch()
}

function selectFirstSearchResult() {
  // 入参: 无
  // 方法: 回车时选择当前结果列表第一项，提升键盘检索效率
  // 出参: 无
  const first = searchResults.value[0]
  if (first) selectSearchResult(first)
}

async function onNewProject() {
  const { value } = await ElMessageBox.prompt('请输入新项目名称', '新建项目', {
    confirmButtonText: '创建',
    cancelButtonText: '取消',
    inputPattern: /.+/,
    inputErrorMessage: '名称不能为空',
  })
  const data = await createProject({ name: value.trim() })
  if (data.status === 'success') {
    await projectStore.loadAll()
    ElMessage.success('项目已创建')
  }
}

function onOpen(conv) {
  switchTo(conv.id)
}

function onOpenLayer() {
  // 入参: 无
  // 方法: 通知左栏容器切换到图层管理面板
  // 出参: 无
  emit('open-layer')
}

async function onDelete(conv) {
  await ElMessageBox.confirm(`确定删除「${conv.title || '该对话'}」？`, '删除对话', {
    type: 'warning',
    confirmButtonText: '删除',
    cancelButtonText: '取消',
  })
  try {
    await deleteConversation(conv.id)
    projectStore.removeConv(conv.id)
    convStore.removeConv(conv.id)
    ElMessage.success('已删除')
  } catch (e) {
    ElMessage.error('删除失败: ' + e.message)
  }
}

async function onRename(conv) {
  const { value } = await ElMessageBox.prompt('请输入新名称', '重命名', {
    inputValue: conv.title || '',
    confirmButtonText: '保存',
    cancelButtonText: '取消',
  })
  const name = value.trim()
  if (!name || name === conv.title) return
  try {
    await patchConversation(conv.id, { title: name })
    projectStore.upsertConv({ id: conv.id, title: name })
    ElMessage.success('已重命名')
  } catch (e) {
    ElMessage.error('重命名失败: ' + e.message)
  }
}

function fmtTime(t) {
  // 入参: t ISO 时间字符串
  // 方法: 按相对时间显示会话更新时间；无时间时返回空字符串
  // 出参: string
  if (!t) return ''
  const ts = new Date(t).getTime()
  if (!Number.isFinite(ts)) return ''
  const diff = Date.now() - ts
  const minute = 60 * 1000
  const hour = 60 * minute
  const day = 24 * hour
  if (diff < minute) return '刚刚'
  if (diff < hour) return `${Math.floor(diff / minute)} 分`
  if (diff < day) return `${Math.floor(diff / hour)} 小时`
  if (diff < 7 * day) return `${Math.floor(diff / day)} 天`
  return (t || '').slice(5, 10)
}
</script>

<template>
  <div class="conv-sidebar shell-fade-in">
    <div class="sidebar-actions">
      <button type="button" class="sidebar-action" @click="onNewConv">
        <el-icon><ChatLineRound /></el-icon>
        <span>新对话</span>
      </button>
      <button type="button" class="sidebar-action search-action" @click="openSearch">
        <el-icon><Search /></el-icon>
        <span>搜索</span>
      </button>
      <button type="button" class="sidebar-action" @click="onOpenLayer">
        <el-icon><Connection /></el-icon>
        <span>图层管理</span>
      </button>
    </div>

    <div class="conv-list">
      <div class="conv-section-header">项目</div>
      <div v-for="g in projects" :key="g.id" class="conv-group">
        <div
          class="project-row"
          :class="{ collapsed: projectStore.isGroupCollapsed(g.id), active: activeProjectId === g.id }"
          @click="projectStore.toggleGroup(g.id)"
        >
          <el-icon class="project-row-icon"><Folder /></el-icon>
          <span class="project-row-title">{{ g.name }}</span>
        </div>
        <div v-show="!projectStore.isGroupCollapsed(g.id)" class="conv-group-items">
          <div
            v-for="c in (grouped.byProject[g.id] || [])"
            :key="c.id"
            class="conversation-card"
            :class="{ active: activeConvId === c.id }"
            @click="onOpen(c)"
          >
            <span class="conversation-card-title" :title="c.title">{{ c.title || '(无标题)' }}</span>
            <span class="conversation-card-time">{{ fmtTime(c.updated_at) }}</span>
            <span class="conv-ops">
              <el-icon @click.stop="onRename(c)"><EditPen /></el-icon>
              <el-icon @click.stop="onDelete(c)"><Delete /></el-icon>
            </span>
          </div>
          <div v-if="!(grouped.byProject[g.id] || []).length" class="conv-empty">暂无会话</div>
        </div>
      </div>

      <!-- 未分组 -->
      <template v-if="grouped.ungrouped.length">
        <div class="conv-section-header">对话</div>
        <div
          v-for="c in grouped.ungrouped"
          :key="c.id"
          class="conversation-card"
          :class="{ active: activeConvId === c.id }"
          @click="onOpen(c)"
        >
          <span class="conversation-card-title" :title="c.title">{{ c.title || '(无标题)' }}</span>
          <span class="conversation-card-time">{{ fmtTime(c.updated_at) }}</span>
          <span class="conv-ops">
            <el-icon @click.stop="onRename(c)"><EditPen /></el-icon>
            <el-icon @click.stop="onDelete(c)"><Delete /></el-icon>
          </span>
        </div>
      </template>

      <div v-if="!projects.length && !grouped.ungrouped.length" class="conv-empty center">
        暂无会话，点击上方新建
      </div>

      <button type="button" class="new-project-inline" @click="onNewProject">
        <el-icon><Folder /></el-icon>
        <span>新建项目</span>
      </button>
    </div>

    <Teleport to="body">
      <div v-if="searchOpen" class="search-overlay" @click.self="closeSearch" @keydown.esc="closeSearch">
        <div class="search-modal" role="dialog" aria-modal="true" aria-label="搜索会话">
          <div class="search-modal-head">
            <el-icon><Search /></el-icon>
            <input
              ref="searchInputRef"
              class="search-modal-input"
              :value="searchQuery"
              placeholder="搜索会话"
              @input="onSearchInput"
              @keydown.enter="selectFirstSearchResult"
            />
            <button v-if="searchQuery" type="button" class="search-clear-btn" @click="clearSearch">清除</button>
          </div>
          <div class="search-results">
            <button
              v-for="c in searchResults"
              :key="c.id"
              type="button"
              class="search-result-row"
              @click="selectSearchResult(c)"
            >
              <span class="search-result-title">{{ c.title || '(无标题)' }}</span>
              <span class="search-result-time">{{ fmtTime(c.updated_at) }}</span>
            </button>
            <div v-if="!searchResults.length" class="search-result-empty">没有匹配的对话</div>
          </div>
        </div>
      </div>
    </Teleport>
  </div>
</template>

<style scoped>
.conv-sidebar {
  display: flex;
  flex-direction: column;
  height: 100%;
  background: var(--bg-panel);
  overflow: hidden;
  color: var(--text-primary);
}

.sidebar-actions {
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 14px 8px 12px;
}

.sidebar-action {
  width: 100%;
  height: 32px;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 0 8px;
  border: none;
  border-radius: var(--r-md);
  background: transparent;
  color: #2f3b49;
  cursor: pointer;
  font-size: 13px;
  text-align: left;
}

.sidebar-action:hover {
  background: var(--bg-hover);
}

.sidebar-action .el-icon {
  width: 16px;
  font-size: 15px;
  color: #303946;
}

.search-action {
  cursor: pointer;
}

.search-overlay {
  position: fixed;
  inset: 0;
  z-index: 3000;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--glass-overlay);
  backdrop-filter: blur(2px);
}

.search-modal {
  width: min(520px, calc(100vw - 40px));
  padding: 14px;
  border: 1px solid var(--glass-border);
  border-radius: 10px;
  background: var(--glass-surface);
  box-shadow: var(--glass-shadow);
  backdrop-filter: var(--glass-blur);
}

.search-modal-head {
  height: 42px;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 0 12px;
  border: none;
  border-radius: 8px;
  background: var(--glass-surface-strong);
}

.search-modal-head .el-icon {
  flex-shrink: 0;
  color: var(--text-secondary);
  font-size: 13px;
}

.search-modal-head:focus-within {
  outline: none;
  box-shadow: none;
}

.search-modal-input {
  flex: 1;
  min-width: 0;
  border: none;
  outline: none;
  background: transparent;
  color: var(--text-primary);
  font-size: 14px;
}

.search-modal-input:focus {
  outline: none;
  box-shadow: none;
}

.search-modal-input::placeholder {
  color: var(--text-muted);
}

.search-clear-btn {
  flex-shrink: 0;
  height: 24px;
  padding: 0 8px;
  border: 1px solid var(--glass-border-strong);
  border-radius: var(--r-sm);
  background: var(--glass-surface);
  color: var(--text-secondary);
  cursor: pointer;
  font-size: 12px;
}

.search-clear-btn:hover {
  color: var(--accent);
  border-color: var(--glass-accent-border);
}

.search-results {
  margin-top: 10px;
  max-height: min(360px, 52vh);
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.search-result-row {
  width: 100%;
  min-height: 34px;
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 0 10px;
  border: none;
  border-radius: var(--r-md);
  background: transparent;
  color: var(--text-primary);
  cursor: pointer;
  text-align: left;
}

.search-result-row:hover {
  background: var(--accent-soft);
}

.search-result-title {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
  font-size: 13px;
}

.search-result-time {
  flex-shrink: 0;
  color: var(--text-muted);
  font-size: 12px;
}

.search-result-empty {
  padding: 12px 10px 2px;
  color: var(--text-muted);
  font-size: 12px;
  text-align: center;
}

.conv-list {
  flex: 1;
  overflow-y: auto;
  padding: 4px 0 14px;
}

.conv-section-header {
  padding: 13px 8px 7px;
  font-size: 13px;
  color: #8f98a6;
  font-weight: 500;
  letter-spacing: 0;
  border-top: none;
  margin-top: 0;
}

.project-row {
  display: flex;
  align-items: center;
  gap: 7px;
  min-height: 28px;
  padding: 0 8px;
  border-radius: var(--r-md);
  cursor: pointer;
  color: #5e6876;
  margin: 0 0 2px;
}
.project-row:hover {
  background: var(--bg-hover);
}
.project-row.active {
  background: var(--accent-soft);
}

.project-row.collapsed {
  opacity: 0.74;
}

.project-row-icon {
  width: 16px;
  font-size: 15px;
  color: #6b7480;
}

.project-row-title {
  flex: 1;
  min-width: 0;
  font-size: 13px;
  font-weight: 500;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.conv-group-items {
  padding: 1px 0 7px 24px;
}

.conversation-card {
  position: relative;
  display: flex;
  align-items: center;
  gap: 10px;
  min-height: 31px;
  padding: 0 8px;
  margin: 0;
  border: none;
  border-radius: var(--r-md);
  background: transparent;
  cursor: pointer;
  min-width: 0;
}
.conversation-card:hover {
  background: var(--bg-hover);
}
.conversation-card.active {
  background: var(--bg-hover);
}
.conversation-card-title {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
  font-size: 13px;
  color: #2f3845;
  font-weight: 400;
  line-height: 18px;
}
.conversation-card-time {
  flex: 0 0 auto;
  margin-left: auto;
  font-size: 12px;
  color: #8d97a5;
  font-family: var(--sans);
  text-align: right;
  line-height: 18px;
}
.conv-ops {
  display: none;
  gap: 4px;
  flex-shrink: 0;
}
.conversation-card:hover .conv-ops {
  display: flex;
}
.conv-ops .el-icon {
  font-size: 13px;
  color: #8d97a5;
  cursor: pointer;
}
.conv-ops .el-icon:hover {
  color: var(--accent-red);
}

.conv-empty {
  text-align: center;
  font-size: 12px;
  color: #8d97a5;
  padding: 8px;
}
.conv-empty.center {
  padding-top: 40px;
}

.new-project-inline {
  width: calc(100% - 16px);
  height: 30px;
  margin: 8px;
  display: inline-flex;
  align-items: center;
  gap: 8px;
  justify-content: center;
  border: 1px dashed rgba(107, 116, 128, 0.45);
  border-radius: var(--r-md);
  background: transparent;
  color: #6b7480;
  cursor: pointer;
  font-size: 12px;
}

.new-project-inline:hover {
  background: var(--bg-hover);
  border-style: solid;
}
</style>
