<script setup>
// ==================== GeoServer 图层管理面板 ====================
// ★ 对应原生 map/layer-panel.js + styles.css 的 .layer-tree 样式
//   工作空间树（白名单 cd*/samseg*）+ 显隐/定位/删除 + 搜索过滤
import { ref, computed, nextTick, onMounted, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { ChatLineRound, Delete, Folder, Location, Refresh, Search } from '@element-plus/icons-vue'
import { getGeoServerServices, getLayerBbox, deleteGeoServerLayer, deleteWorkspace } from '@/services/api'
import { useMap } from '@/composables/useMap'
import { useProjectStore } from '@/stores/project'
import { debugLog } from '@/utils/debugLog'

const { hasLayer, addWmsLayer, addWfsLayer, removeLayer } = useMap()
const projectStore = useProjectStore()
const emit = defineEmits(['back'])

const tree = ref(null)
const loading = ref(false)
const layerSearchOpen = ref(false)
const layerSearchInputRef = ref(null)
const layerSearchQuery = ref('')
const collapsed = ref(new Set())
const visibleSet = ref(new Set())

onMounted(() => loadTree())
// 监听图层列表版本号：发布/删除图层后自动刷新（跨组件通信，ChatPanel 发布后 bump）
watch(() => projectStore.layerListVersion, () => loadTree({ silent: true }))

async function loadTree({ silent = false } = {}) {
  // 入参: silent 是否静默刷新；首次加载显示 loading，手动/联动刷新不扰动列表布局
  // 方法: 拉取 GeoServer 服务树并同步图层可见状态；静默刷新时保留现有工作空间渲染
  // 出参: Promise<void>
  if (!silent) loading.value = true
  try {
    tree.value = await getGeoServerServices()
    syncVisibility()
  } catch (e) {
    ElMessage.error('图层加载失败')
  } finally {
    if (!silent) loading.value = false
  }
}

// 入参: 无
// 方法: 通知左侧容器返回对话管理面板，保持图层管理内部只负责图层状态
// 出参: 无（副作用：触发 back 事件）
function goBack() {
  emit('back')
}

// 入参: 无
// 方法: 打开图层搜索浮层，并在 DOM 更新后聚焦输入框
// 出参: 无
function openLayerSearch() {
  layerSearchOpen.value = true
  nextTick(() => layerSearchInputRef.value?.focus())
}

// 入参: 无
// 方法: 关闭图层搜索浮层；搜索词只影响浮层结果，不过滤左侧图层树
// 出参: 无
function closeLayerSearch() {
  layerSearchOpen.value = false
}

// 入参: e 原生 input 事件
// 方法: 更新图层搜索浮层内的本地关键词
// 出参: 无
function onLayerSearchInput(e) {
  layerSearchQuery.value = e.target.value
}

// 入参: 无
// 方法: 清空图层搜索关键词，并保持搜索输入框聚焦
// 出参: 无
function clearSearch() {
  layerSearchQuery.value = ''
  nextTick(() => layerSearchInputRef.value?.focus())
}

// 白名单过滤（cd*/samseg*）
const filteredWorkspaces = computed(() => {
  if (!tree.value?.workspaces) return []
  return tree.value.workspaces
    .filter((ws) => {
      const name = (ws.workspace || '').toLowerCase()
      return name.startsWith('cd') || name.startsWith('samseg')
    })
    .map((ws) => {
      const layers = (ws.layers_typed || ws.layers || []).map((l) => ({
        name: typeof l === 'string' ? l : l.name,
        type: typeof l === 'string' ? 'unknown' : l.type || 'unknown',
        full_name: `${ws.workspace}:${typeof l === 'string' ? l : l.name}`,
      }))
      return { ...ws, layers }
    })
})

const layerSearchResults = computed(() => {
  const kw = layerSearchQuery.value.trim().toLowerCase()
  const rows = []
  filteredWorkspaces.value.forEach((ws) => {
    ws.layers.forEach((layer) => {
      const haystack = `${ws.workspace}:${layer.name}`.toLowerCase()
      if (!kw || haystack.includes(kw)) rows.push({ workspace: ws.workspace, ...layer })
    })
  })
  return rows.slice(0, kw ? 30 : 12)
})

function layerKey(ws, name) {
  return `${ws}|${name}`
}
function isVisible(ws, name) {
  return visibleSet.value.has(layerKey(ws, name))
}

// 入参: ws, name, type, checked
// 方法: 勾选→先清理残留→拉 bbox→矢量走 WFS, 栅格走 WMS; 取消→移除
//   ★ 矢量用 WFS (VectorLayer 客户端 _vectorStyle 渲染, 与原生 showWfsInPanel 统一),
//     避免 TileWMS/ImageWMS 在 OL 10.x 中对极小范围图层 tileUrlFunction 返回 undefined 的问题
async function onToggleVisible(ws, name, type, checked) {
  debugLog(`onToggleVisible ws=${ws} name=${name} type=${type} checked=${checked}`)
  if (checked) {
    // ★ 先移除同名图层 (防止残留)
    removeLayer(name, ws)
    try {
      const data = await getLayerBbox(ws, name)
      const b = data.status === 'success' && data.bbox ? [data.bbox.minx, data.bbox.miny, data.bbox.maxx, data.bbox.maxy] : null
      // ★ 矢量走 WFS (VectorLayer 客户端渲染, 与原生版 showWfsInPanel 一致), 栅格走 WMS
      if (type === 'vector') {
        addWfsLayer({ name, workspace: ws, bbox: b, forceFit: true })
      } else {
        addWmsLayer({ name, workspace: ws, bbox: b, layerType: type })
      }
    } catch (e) {
      if (type === 'vector') {
        addWfsLayer({ name, workspace: ws, forceFit: false })
      } else {
        addWmsLayer({ name, workspace: ws, layerType: type })
      }
    }
    visibleSet.value.add(layerKey(ws, name))
  } else {
    removeLayer(name, ws)
    visibleSet.value.delete(layerKey(ws, name))
  }
}

// 入参: ws, name, type
// 方法: 拉取 bbox → 叠加并定位; 矢量走 WFS (客户端渲染), 栅格走 WMS
async function onLocate(ws, name, type = 'unknown') {
  try {
    const data = await getLayerBbox(ws, name)
    const b = data.status === 'success' && data.bbox ? [data.bbox.minx, data.bbox.miny, data.bbox.maxx, data.bbox.maxy] : null
    if (type === 'vector') {
      addWfsLayer({ name, workspace: ws, bbox: b, forceFit: true })
    } else {
      addWmsLayer({ name, workspace: ws, bbox: b, layerType: type })
    }
    visibleSet.value.add(layerKey(ws, name))
  } catch (e) {
    ElMessage.error('定位失败')
  }
}

// 入参: result 图层搜索结果对象（workspace/name/type）
// 方法: 定位并加载用户从搜索浮层中选择的图层，然后关闭浮层
// 出参: Promise<void>
async function selectLayerSearchResult(result) {
  if (!result?.workspace || !result?.name) return
  await onLocate(result.workspace, result.name, result.type)
  closeLayerSearch()
}

// 入参: 无
// 方法: 回车时选择当前图层搜索结果的第一项
// 出参: Promise<void>
async function selectFirstLayerSearchResult() {
  const first = layerSearchResults.value[0]
  if (first) await selectLayerSearchResult(first)
}

async function onDelete(ws, name) {
  await ElMessageBox.confirm(`确定删除 ${ws}:${name}？此操作不可恢复`, '删除图层', {
    type: 'warning',
    confirmButtonText: '删除',
    cancelButtonText: '取消',
  })
  try {
    await deleteGeoServerLayer(ws, name)
    removeLayer(name, ws)
    visibleSet.value.delete(layerKey(ws, name))
    await loadTree()
    ElMessage.success('已删除')
  } catch (e) {
    ElMessage.error('删除失败')
  }
}

// 入参: wsName 工作空间名
// 方法: 确认后删除整个工作空间（含其下所有图层），同步移除地图图层 + 刷新树
async function onDeleteWorkspace(wsName) {
  await ElMessageBox.confirm(`确定删除工作空间「${wsName}」及其下所有图层？此操作不可恢复`, '删除工作空间', {
    type: 'warning',
    confirmButtonText: '删除',
    cancelButtonText: '取消',
  })
  try {
    await deleteWorkspace(wsName)
    const wsData = tree.value?.workspaces?.find((w) => w.workspace === wsName)
    if (wsData) {
      ;(wsData.layers_typed || wsData.layers || []).forEach((l) => {
        const lname = typeof l === 'string' ? l : l.name
        removeLayer(lname, wsName)
        visibleSet.value.delete(layerKey(wsName, lname))
      })
    }
    await loadTree()
    ElMessage.success('工作空间已删除')
  } catch (e) {
    ElMessage.error('删除失败')
  }
}

function toggleCollapse(wsName) {
  const next = new Set(collapsed.value)
  if (next.has(wsName)) next.delete(wsName)
  else next.add(wsName)
  collapsed.value = next
}

// 入参: 无
// 方法: 根据地图已加载图层同步 visibleSet（切回面板时反映状态）
function syncVisibility() {
  if (!tree.value?.workspaces) return
  const next = new Set()
  tree.value.workspaces.forEach((ws) => {
    ;(ws.layers_typed || ws.layers || []).forEach((l) => {
      const name = typeof l === 'string' ? l : l.name
      if (hasLayer(name, ws.workspace)) next.add(layerKey(ws.workspace, name))
    })
  })
  visibleSet.value = next
}
</script>

<template>
  <div class="layer-panel">
    <div class="layer-actions">
      <button type="button" class="layer-action" @click="loadTree({ silent: true })">
        <el-icon><Refresh /></el-icon>
        <span>刷新图层</span>
      </button>
      <button type="button" class="layer-action" @click="openLayerSearch">
        <el-icon><Search /></el-icon>
        <span>搜索</span>
      </button>
      <button type="button" class="layer-action" @click="goBack">
        <el-icon><ChatLineRound /></el-icon>
        <span>对话管理</span>
      </button>
    </div>

    <!-- 图层树（对齐原生 .layer-tree） -->
    <div class="layer-tree">
      <div class="layer-section-header">工作空间</div>
      <div v-if="loading && !tree" class="layer-tree-empty">加载中…</div>
      <div v-else-if="!filteredWorkspaces.length" class="layer-tree-empty">
        GeoServer 中暂无工作空间/图层
      </div>

      <div
        v-for="ws in filteredWorkspaces"
        :key="ws.workspace"
        class="layer-ws-group"
        :class="{ collapsed: collapsed.has(ws.workspace) }"
      >
        <!-- 工作空间头（对齐原生 .layer-ws-header） -->
        <div class="layer-ws-header" @click="toggleCollapse(ws.workspace)">
          <span class="layer-ws-toggle">▼</span>
          <el-icon class="layer-ws-icon"><Folder /></el-icon>
          <span class="layer-ws-name">{{ ws.workspace }}</span>
          <span class="layer-ws-count">{{ ws.layers.length }} 个图层</span>
          <button class="layer-icon-btn danger" title="删除工作空间" @click.stop="onDeleteWorkspace(ws.workspace)">
            <el-icon><Delete /></el-icon>
          </button>
        </div>

        <!-- 图层行（对齐原生 .layer-item） -->
        <div v-show="!collapsed.has(ws.workspace)" class="layer-ws-items">
          <div
            v-for="lyr in ws.layers"
            :key="lyr.full_name"
            class="layer-item"
          >
            <div class="layer-item-main">
              <input
                type="checkbox"
                class="layer-vis-checkbox"
                :checked="isVisible(ws.workspace, lyr.name)"
                @change="onToggleVisible(ws.workspace, lyr.name, lyr.type, $event.target.checked)"
              />
              <span class="layer-type-icon" :class="lyr.type" :title="lyr.type">
                {{ lyr.type === 'raster' ? '▦' : lyr.type === 'vector' ? '◈' : '○' }}
              </span>
              <span class="layer-name" :title="'点击定位: ' + lyr.full_name" @click="onLocate(ws.workspace, lyr.name, lyr.type)">
                {{ lyr.name }}
              </span>
              <span class="layer-item-actions">
                <button class="layer-icon-btn" title="定位到图层范围" @click.stop="onLocate(ws.workspace, lyr.name, lyr.type)">
                  <el-icon><Location /></el-icon>
                </button>
                <button class="layer-icon-btn danger" title="删除图层" @click.stop="onDelete(ws.workspace, lyr.name)">
                  <el-icon><Delete /></el-icon>
                </button>
              </span>
            </div>
          </div>
          <div v-if="!ws.layers.length" class="layer-item empty"><span class="layer-name" style="color:var(--text-muted)">(空)</span></div>
        </div>
      </div>
    </div>

    <Teleport to="body">
      <div v-if="layerSearchOpen" class="layer-search-overlay" @click.self="closeLayerSearch" @keydown.esc="closeLayerSearch">
        <div class="layer-search-modal" role="dialog" aria-modal="true" aria-label="搜索图层">
          <div class="layer-search-modal-head">
            <el-icon><Search /></el-icon>
            <input
              ref="layerSearchInputRef"
              class="layer-search-modal-input"
              :value="layerSearchQuery"
              placeholder="搜索图层或工作空间"
              @input="onLayerSearchInput"
              @keydown.enter="selectFirstLayerSearchResult"
            />
            <button v-if="layerSearchQuery" type="button" class="layer-search-clear-btn" @click="clearSearch">清除</button>
          </div>
          <div class="layer-search-results">
            <button
              v-for="item in layerSearchResults"
              :key="item.full_name"
              type="button"
              class="layer-search-result-row"
              @click="selectLayerSearchResult(item)"
            >
              <span class="layer-search-result-title">{{ item.name }}</span>
              <span class="layer-search-result-meta">{{ item.workspace }} · {{ item.type }}</span>
            </button>
            <div v-if="!layerSearchResults.length" class="layer-search-result-empty">没有匹配的图层</div>
          </div>
        </div>
      </div>
    </Teleport>
  </div>
</template>

<style scoped>
.layer-panel {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  background: var(--bg-panel);
  overflow: hidden;
}

.layer-actions {
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 14px 8px 8px;
}

.layer-action {
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

.layer-action:hover {
  background: var(--bg-hover);
}

.layer-action .el-icon {
  width: 16px;
  font-size: 15px;
  color: #303946;
}

.layer-tree {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  font-family: var(--sans);
  font-size: 13px;
  background: var(--bg-panel);
  border-top: none;
  padding: 4px 0 14px;
}
.layer-section-header {
  padding: 13px 8px 7px;
  font-size: 13px;
  color: #8f98a6;
  font-weight: 500;
  letter-spacing: 0;
}
.layer-tree-empty {
  padding: 24px 12px;
  text-align: center;
  color: var(--text-muted);
  font-size: 12px;
}

.layer-ws-group {
  border-bottom: none;
  padding: 0 0 6px;
}
.layer-ws-header {
  display: flex;
  align-items: center;
  gap: 7px;
  min-height: 30px;
  padding: 0 8px;
  cursor: pointer;
  color: #5e6876;
  font-weight: 500;
  background: transparent;
  border-radius: var(--r-md);
  user-select: none;
}
.layer-ws-header:hover {
  background: var(--bg-hover);
}
.layer-ws-toggle {
  display: inline-block;
  width: 12px;
  color: #6b7480;
  transition: transform 0.15s;
  font-size: 10px;
}
.layer-ws-icon {
  width: 16px;
  font-size: 15px;
  color: #6b7480;
}
.layer-ws-group.collapsed .layer-ws-toggle {
  transform: rotate(-90deg);
}
.layer-ws-name {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 13px;
}
.layer-ws-count {
  color: #8d97a5;
  font-size: 12px;
  font-weight: 400;
}
.layer-ws-header .layer-icon-btn {
  opacity: 0;
  transition: opacity 0.12s;
}
.layer-ws-header:hover .layer-icon-btn {
  opacity: 1;
}
.layer-ws-items {
  display: flex;
  flex-direction: column;
  padding-left: 24px;
}
.layer-ws-group.collapsed .layer-ws-items {
  display: none;
}

.layer-item {
  display: flex;
  align-items: center;
  min-height: 31px;
  padding: 0 8px;
  border-bottom: none;
  border-radius: var(--r-md);
}
.layer-item:hover {
  background: var(--bg-hover);
}
.layer-item.empty {
  display: flex;
  padding-top: 8px;
  padding-bottom: 8px;
}
.layer-item-main {
  width: 100%;
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 8px;
}
.layer-vis-checkbox {
  width: 14px;
  height: 14px;
  cursor: pointer;
  accent-color: var(--accent);
  flex-shrink: 0;
}
.layer-type-icon {
  width: 16px;
  text-align: center;
  font-size: 12px;
  flex-shrink: 0;
}
.layer-type-icon.raster {
  color: #f59e0b;
}
.layer-type-icon.vector {
  color: #10b981;
}
.layer-type-icon.unknown {
  color: var(--text-muted);
}
.layer-name {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: #2f3845;
  cursor: pointer;
  font-size: 13px;
}
.layer-name:hover {
  color: var(--accent);
  text-decoration: none;
}
.layer-item-actions {
  margin-left: auto;
  display: flex;
  gap: 4px;
  opacity: 0;
  transition: opacity 0.12s;
}
.layer-item:hover .layer-item-actions {
  opacity: 1;
}
.layer-icon-btn {
  width: 22px;
  height: 22px;
  border: none;
  background: transparent;
  cursor: pointer;
  color: var(--text-muted);
  border-radius: var(--r-xs);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 0;
  font-size: 13px;
  line-height: 1;
}
.layer-icon-btn .el-icon {
  font-size: 13px;
}
.layer-icon-btn:hover {
  background: var(--bg-elevated);
  color: var(--text-primary);
}
.layer-icon-btn.danger:hover {
  color: var(--accent-red);
}

.layer-search-overlay {
  position: fixed;
  inset: 0;
  z-index: 3000;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--glass-overlay);
  backdrop-filter: blur(2px);
}

.layer-search-modal {
  width: min(520px, calc(100vw - 40px));
  padding: 10px;
  border: 1px solid var(--glass-border);
  border-radius: 10px;
  background: var(--glass-surface);
  box-shadow: var(--glass-shadow);
  backdrop-filter: var(--glass-blur);
}

.layer-search-modal-head {
  height: 32px;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 0 10px;
  border: none;
  border-radius: var(--r-md);
  background: var(--glass-surface-strong);
}

.layer-search-modal-head .el-icon {
  flex-shrink: 0;
  color: var(--text-secondary);
  font-size: 12px;
}

.layer-search-modal-head:focus-within {
  outline: none;
  box-shadow: none;
}

.layer-search-modal-input {
  flex: 1;
  min-width: 0;
  border: none;
  outline: none;
  background: transparent;
  color: var(--text-primary);
  font-size: 12px;
  line-height: 18px;
}

.layer-search-modal-input:focus {
  outline: none;
  box-shadow: none;
}

.layer-search-modal-input::placeholder {
  color: var(--text-muted);
}

.layer-search-clear-btn {
  flex-shrink: 0;
  height: 22px;
  padding: 0 7px;
  border: 1px solid var(--glass-border-strong);
  border-radius: var(--r-sm);
  background: var(--glass-surface);
  color: var(--text-secondary);
  cursor: pointer;
  font-size: 12px;
}

.layer-search-clear-btn:hover {
  color: var(--accent);
  border-color: var(--glass-accent-border);
}

.layer-search-results {
  margin-top: 6px;
  max-height: min(360px, 52vh);
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.layer-search-result-row {
  width: 100%;
  min-height: 28px;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 0 8px;
  border: none;
  border-radius: var(--r-md);
  background: transparent;
  color: var(--text-primary);
  cursor: pointer;
  text-align: left;
}

.layer-search-result-row:hover {
  background: var(--accent-soft);
}

.layer-search-result-title {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
  font-size: 12px;
  line-height: 18px;
}

.layer-search-result-meta {
  flex-shrink: 0;
  color: var(--text-muted);
  font-size: 11px;
  line-height: 18px;
}

.layer-search-result-empty {
  padding: 8px 8px 0;
  color: var(--text-muted);
  font-size: 12px;
  text-align: center;
}
</style>
