<script setup>
// ==================== GeoServer 图层管理面板 ====================
// ★ 对应原生 map/layer-panel.js + styles.css 的 .layer-tree 样式
//   全量工作空间树（矢量/影像/解译成果/其他 分组）+ 显隐/定位/删除 + 搜索过滤
//   上传影像/矢量前弹窗选择或新建发布目标工作空间
import { ref, computed, nextTick, onMounted, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { ChatLineRound, Close, Delete, Folder, Location, Refresh, Upload } from '@element-plus/icons-vue'
import { getGeoServerServices, getGeoServerWorkspaces, getLayerBbox, deleteGeoServerLayer, deleteWorkspace, uploadSamsegImages, publishGeoServerLayer, uploadShapefile } from '@/services/api'
import { useMap } from '@/composables/useMap'
import { BASEMAP_OPTIONS, useBasemap } from '@/composables/useBasemap'
import { useProjectStore } from '@/stores/project'
import { debugLog } from '@/utils/debugLog'

const props = defineProps({
  // 悬浮在地图左上角时使用“关闭”而不是“对话管理”
  floating: { type: Boolean, default: false },
})
const { hasLayer, addWmsLayer, addWfsLayer, removeLayer } = useMap()
// 底图（第一层级）：类型 + Esri 历史时相；与卷帘共用同一份 Wayback 版本清单
const {
  kind: basemapKind,
  releaseDate: basemapDate,
  releaseYears: basemapYears,
  loading: basemapLoading,
  error: basemapError,
  fallback: basemapFallback,
  summary: basemapSummary,
  basemapAvailable,
  loadBasemapReleases,
  applyBasemap,
  applyBasemapDate,
} = useBasemap()
const projectStore = useProjectStore()
const emit = defineEmits(['back'])

const tree = ref(null)
const loading = ref(false)
const collapsed = ref(new Set())
const visibleSet = ref(new Set())

onMounted(() => {
  loadTree()
})
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

// ==================== 上传目标工作空间选择 ====================
// 入参: kind 显示用类型名（影像/矢量） | 方法: 弹出"选择现有/新建工作空间"浮层，取消返回 null
// 出参: Promise<string|null>
const wsPick = ref(null)

function chooseWorkspace(kind) {
  return new Promise((resolve) => {
    wsPick.value = { kind, workspaces: [], choice: 'existing', existing: '', newName: '', resolve }
    getGeoServerWorkspaces()
      .then((d) => {
        if (wsPick.value) wsPick.value.workspaces = d.workspaces || []
      })
      .catch(() => {})
  })
}

function confirmWsPick() {
  const p = wsPick.value
  if (!p) return
  let ws = ''
  if (p.choice === 'new') {
    ws = (p.newName || '').trim()
    if (!/^[A-Za-z0-9_]+$/.test(ws)) {
      ElMessage.warning('工作空间名只能包含英文、数字、下划线')
      return
    }
  } else {
    ws = p.existing
    if (!ws) {
      ElMessage.warning('请选择一个工作空间，或选择"新建"')
      return
    }
  }
  wsPick.value = null
  p.resolve(ws)
}

function cancelWsPick() {
  const p = wsPick.value
  wsPick.value = null
  if (p) p.resolve(null)
}

// ==================== 上传影像/矢量（原对话区上传功能迁入） ====================
// 入参: 无（内部创建 file input）
// 方法: 影像(.tif/.tiff/.cog)走 samseg 上传后按 raster 发布；矢量只选 .shp 时经
//   showDirectoryPicker 打开所在文件夹自动匹配同 stem 组件（.shx/.dbf/.prj/...），
//   已选组件集则直接上传；统一走 upload-shapefile 端点落盘后按 vector 发布
// 出参: 无
function onUploadLayer() {
  const fileInput = document.createElement('input')
  fileInput.type = 'file'
  fileInput.multiple = true
  fileInput.accept = '.tif,.tiff,.cog,.shp,.shx,.dbf,.prj,.cpg,.sbn,.sbx'
  fileInput.onchange = async () => {
    const files = Array.from(fileInput.files || [])
    if (!files.length) return
    const tifFiles = files.filter((f) => /\.(tif|tiff|cog)$/i.test(f.name))
    const shpFiles = files.filter((f) => /\.(shp|shx|dbf|prj|cpg|sbn|sbx)$/i.test(f.name))
    let okCount = 0
    // 发布目标工作空间：影像/矢量各确认一次（取消即中止本次上传）
    let wsRaster = null
    let wsVector = null
    if (tifFiles.length) {
      wsRaster = await chooseWorkspace('影像')
      if (!wsRaster) return
    }
    if (shpFiles.length) {
      wsVector = await chooseWorkspace('矢量')
      if (!wsVector) return
    }
    try {
      // ① 影像：samseg 上传落盘 → raster 发布 → WMS 叠加
      if (tifFiles.length) {
        const data = await uploadSamsegImages(tifFiles, null)
        const paths = data.paths || []
        for (const p of paths) {
          const pub = await publishGeoServerLayer({ file_path: p, layer_type: 'raster', workspace: wsRaster })
          if (pub.status !== 'success') {
            ElMessage.warning(`影像发布失败: ${pub.msg || '未知原因'}`)
            continue
          }
          let bbox = null
          try {
            const bb = await getLayerBbox(pub.workspace, pub.layer_name)
            if (bb.status === 'success') bbox = bb.bbox
          } catch {}
          addWmsLayer({ name: pub.layer_name, workspace: pub.workspace, bbox, layerType: 'raster' })
          okCount += 1
        }
        if (paths.length) ElMessage.success(`已上传并发布 ${okCount} 个影像图层`)
      }
      // ② 矢量：只选 .shp → 打开所在目录匹配组件；已选组件集 → 直接上传
      if (shpFiles.length) {
        if (!shpFiles.some((f) => /\.shp$/i.test(f.name))) {
          ElMessage.warning('矢量上传缺少 .shp 主文件')
        } else if (shpFiles.some((f) => /\.(shx|dbf)$/i.test(f.name)) || !window.showDirectoryPicker) {
          // 组件已随选（或浏览器不支持目录选择）→ 按所选文件直接上传
          const data = await uploadShapefile(shpFiles)
          if (await publishVectorShp(data, wsVector)) okCount += 1
        } else {
          // 仅选 .shp → 目录扫描匹配同 stem 组件
          const ok = await ElMessageBox.confirm(
            '将打开 .shp 所在文件夹，自动匹配 .shx/.dbf/.prj 等组件后上传。',
            '匹配矢量组件',
            { confirmButtonText: '选择文件夹', cancelButtonText: '取消', type: 'info' }
          ).catch(() => false)
          if (!ok) return
          const dirHandle = await window.showDirectoryPicker()
          const byStem = new Map()
          for await (const [name, handle] of dirHandle.entries()) {
            if (handle.kind !== 'file') continue
            const m = name.match(/^(.*)\.(shp|shx|dbf|prj|cpg|sbn|sbx)$/i)
            if (!m) continue
            if (!byStem.has(m[1])) byStem.set(m[1], [])
            byStem.get(m[1]).push(await handle.getFile())
          }
          // 优先匹配所选 .shp 的 stem；未命中时上传目录内全部 .shp
          const wanted = new Set(
            shpFiles.filter((f) => /\.shp$/i.test(f.name)).map((f) => f.name.replace(/\.shp$/i, ''))
          )
          let groups = [...byStem.entries()].filter(([stem, comps]) =>
            wanted.has(stem) && comps.some((f) => /\.shp$/i.test(f.name))
          )
          if (!groups.length) {
            groups = [...byStem.entries()].filter(([stem, comps]) => comps.some((f) => /\.shp$/i.test(f.name)))
          }
          if (!groups.length) {
            ElMessage.warning('所选文件夹内未找到 .shp 文件')
            return
          }
          for (const [, comps] of groups) {
            const data = await uploadShapefile(comps)
            if (await publishVectorShp(data, wsVector)) okCount += 1
          }
        }
      }
      if (okCount > 0) {
        await loadTree({ silent: true })
      }
    } catch (e) {
      ElMessage.error('上传失败: ' + e.message)
    }
  }
  fileInput.click()
}

// 入参: data upload-shapefile 端点返回（{status, shp_path, warnings}）
// 方法: .shp 落盘路径 → vector 发布 → 取 bbox → WFS 叠加；失败弹提示
// 出参: boolean 是否发布成功
async function publishVectorShp(data, workspace) {
  if (data?.status !== 'success' || !data.shp_path) {
    ElMessage.warning(`矢量上传失败: ${(data && data.warnings && data.warnings.join('; ')) || '缺少必要组件'}`)
    return false
  }
  const pub = await publishGeoServerLayer({ file_path: data.shp_path, layer_type: 'vector', workspace })
  if (pub.status !== 'success') {
    ElMessage.warning(`矢量发布失败: ${pub.msg || '未知原因'}`)
    return false
  }
  let bbox = null
  try {
    const bb = await getLayerBbox(pub.workspace, pub.layer_name)
    if (bb.status === 'success') bbox = bb.bbox
  } catch {}
  addWfsLayer({ name: pub.layer_name, workspace: pub.workspace, bbox })
  ElMessage.success(`已上传并发布矢量图层 ${pub.workspace}:${pub.layer_name}`)
  return true
}

// 全量工作空间 → 图层列表归一化（服务层已过滤 qh 系列）
const filteredWorkspaces = computed(() => {
  if (!tree.value?.workspaces) return []
  return tree.value.workspaces
    .map((ws) => {
      const layers = (ws.layers_typed || ws.layers || []).map((l) => ({
        name: typeof l === 'string' ? l : l.name,
        type: typeof l === 'string' ? 'unknown' : l.type || 'unknown',
        full_name: `${ws.workspace}:${typeof l === 'string' ? l : l.name}`,
      }))
      return { ...ws, layers }
    })
})

// 工作空间显示名：samseg 存放解译后保存的掩码/矢量成果，面板显示为「解译成果」
const WS_DISPLAY_NAMES = { samseg: '解译成果' }
function wsDisplayName(name) {
  return WS_DISPLAY_NAMES[(name || '').toLowerCase()] || name
}

// 分组展示：矢量 / 影像 / 解译成果（底图为在线底图，不经 GeoServer，见第一层级）
const groupedSections = computed(() => {
  const defs = [
    { title: '矢量', match: (n) => n.startsWith('cd_shp') || n.startsWith('jzg_shp') },
    { title: '影像', match: (n) => ['cd_raster', 'jzg_raster'].includes(n) },
    { title: '解译成果', match: (n) => n.startsWith('samseg') },
    { title: '其他', match: (n) => !['cd_shp', 'jzg_shp', 'cd_raster', 'jzg_raster', 'samseg'].includes(n) },
  ]
  return defs
    .map((d) => ({
      title: d.title,
      workspaces: filteredWorkspaces.value.filter((ws) => d.match((ws.workspace || '').toLowerCase())),
    }))
    .filter((sec) => sec.workspaces.length)
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

// ==================== 底图（第一层级） ====================
// 入参: value 底图类型 | 方法: 立即切换底图；选中 Esri 时顺带加载历史版本清单 | 出参: 无
function chooseBasemap(value) {
  if (!basemapAvailable(value)) {
    ElMessage.warning('天地图底图需要先配置 VITE_TDT_KEY')
    return
  }
  if (!applyBasemap(value)) {
    ElMessage.error(basemapError.value || '底图切换失败')
    return
  }
  if (value === 'esri' && !basemapYears.value.length) loadBasemapReleases()
}

// 入参: date Wayback 发布日期（'' = 最新影像） | 方法: 切换 Esri 历史底图并提示结果 | 出参: 无
async function changeBasemapDate(date) {
  const ok = await applyBasemapDate(date)
  if (!ok) {
    ElMessage.error(basemapError.value || '历史底图切换失败')
    return
  }
  ElMessage.success(date ? `底图已切换为 ${date} 影像` : '底图已切换为最新影像')
}

// 入参: open 下拉是否展开 | 方法: 首次展开时懒加载历史版本，避免打开图层面板就发请求 | 出参: 无
function onBasemapDropdown(open) {
  if (open && !basemapYears.value.length) loadBasemapReleases()
}
</script>

<template>
  <div class="layer-panel">
    <div class="layer-actions">
      <button type="button" class="layer-action" @click="loadTree({ silent: true })">
        <el-icon><Refresh /></el-icon>
        <span>刷新图层</span>
      </button>
      <button type="button" class="layer-action" @click="onUploadLayer">
        <el-icon><Upload /></el-icon>
        <span>上传影像/矢量</span>
      </button>
      <button type="button" class="layer-action" @click="goBack">
        <el-icon><Close v-if="props.floating" /><ChatLineRound v-else /></el-icon>
        <span>{{ props.floating ? '关闭' : '对话管理' }}</span>
      </button>
    </div>

    <!-- 图层树（对齐原生 .layer-tree） -->
    <div class="layer-tree">
      <!-- 第一个层级：底图（类型 + Esri 历史时相） -->
      <div class="layer-section-header">
        底图
        <span class="layer-section-meta" :title="basemapSummary">{{ basemapSummary }}</span>
      </div>
      <div class="basemap-list">
        <div
          v-for="opt in BASEMAP_OPTIONS"
          :key="opt.value"
          class="basemap-item"
          :class="{ active: basemapKind === opt.value, disabled: !basemapAvailable(opt.value) }"
        >
          <button
            type="button"
            class="basemap-row"
            :disabled="!basemapAvailable(opt.value)"
            :title="basemapAvailable(opt.value) ? opt.hint : '未配置 VITE_TDT_KEY'"
            @click="chooseBasemap(opt.value)"
          >
            <span class="basemap-radio" :class="{ checked: basemapKind === opt.value }"></span>
            <span class="basemap-name">{{ opt.label }}</span>
            <span class="basemap-hint">{{ opt.hint }}</span>
          </button>

          <!-- Esri 时相：默认最新影像镶嵌，可切到 Wayback 历史版本 -->
          <div v-if="opt.value === 'esri' && basemapKind === 'esri'" class="basemap-date">
            <el-select
              :model-value="basemapDate"
              size="small"
              filterable
              class="basemap-date-select"
              popper-class="basemap-date-popper"
              :loading="basemapLoading"
              placeholder="最新影像（无单一日期）"
              @change="changeBasemapDate"
              @visible-change="onBasemapDropdown"
            >
              <el-option label="最新影像（无单一日期）" value="" />
              <el-option
                v-for="release in basemapYears"
                :key="release.date"
                :label="release.date"
                :value="release.date"
              />
            </el-select>
          </div>
        </div>
      </div>

      <!-- 第二个层级：矢量 / 影像 / 解译成果 分组（底图为在线底图，见第一层级） -->
      <div v-if="loading && !tree" class="layer-tree-empty">加载中…</div>
      <div v-else-if="!filteredWorkspaces.length" class="layer-tree-empty">
        GeoServer 中暂无工作空间/图层
      </div>

      <template v-for="sec in groupedSections" :key="sec.title">
      <div class="layer-section-header">{{ sec.title }}</div>
      <div
        v-for="ws in sec.workspaces"
        :key="ws.workspace"
        class="layer-ws-group"
        :class="{ collapsed: collapsed.has(ws.workspace) }"
      >
        <!-- 工作空间头（对齐原生 .layer-ws-header）；段内仅一个工作空间时隐藏组头，避免与段标题重复 -->
        <div v-if="sec.workspaces.length > 1" class="layer-ws-header" @click="toggleCollapse(ws.workspace)">
          <span class="layer-ws-toggle">▼</span>
          <el-icon class="layer-ws-icon"><Folder /></el-icon>
          <span class="layer-ws-name">{{ wsDisplayName(ws.workspace) }}</span>
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
      </template>
    </div>

    <!-- 上传目标工作空间选择浮层（影像/矢量各弹一次，取消即中止该类上传） -->
    <Teleport to="body">
      <div v-if="wsPick" class="layer-search-overlay" @click.self="cancelWsPick">
        <div class="layer-search-modal" role="dialog" aria-modal="true" aria-label="选择工作空间">
          <div class="ws-pick-title">上传{{ wsPick.kind }} — 选择目标工作空间</div>
          <div class="ws-pick-body">
            <label class="ws-pick-row">
              <input v-model="wsPick.choice" type="radio" value="existing" />
              <span>使用现有工作空间</span>
            </label>
            <select v-if="wsPick.choice === 'existing'" v-model="wsPick.existing" class="ws-pick-select">
              <option value="" disabled>请选择…</option>
              <option v-for="w in wsPick.workspaces" :key="w" :value="w">{{ w }}</option>
            </select>
            <label class="ws-pick-row">
              <input v-model="wsPick.choice" type="radio" value="new" />
              <span>新建工作空间</span>
            </label>
            <input
              v-if="wsPick.choice === 'new'"
              v-model="wsPick.newName"
              class="ws-pick-input"
              placeholder="输入新工作空间名（英文/数字/下划线）"
              @keydown.enter="confirmWsPick"
            />
          </div>
          <div class="ws-pick-actions">
            <button type="button" class="ws-pick-btn" @click="cancelWsPick">取消</button>
            <button type="button" class="ws-pick-btn primary" @click="confirmWsPick">确定</button>
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
  white-space: nowrap;
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
  padding: 8px 8px 5px;
  font-size: 13px;
  color: #8f98a6;
  font-weight: 500;
  letter-spacing: 0;
  display: flex;
  align-items: baseline;
  gap: 8px;
}
.layer-section-meta {
  margin-left: auto;
  max-width: 58%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 12px;
  font-weight: 400;
  color: var(--text-muted);
}

/* ===== 底基层级（类型 + Esri 历史时相） ===== */
.basemap-list {
  padding: 0 8px 2px;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.basemap-item {
  border: 1px solid transparent;
  border-radius: var(--r-sm);
}
.basemap-item.active {
  background: transparent;
  border-color: transparent;
}
.basemap-item.disabled {
  opacity: 0.55;
}
.basemap-row {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  min-height: 30px;
  padding: 0 8px;
  border: none;
  background: transparent;
  color: var(--text-primary);
  font-size: 13px;
  text-align: left;
  cursor: pointer;
}
.basemap-row:disabled {
  cursor: not-allowed;
}
.basemap-radio {
  flex: none;
  width: 12px;
  height: 12px;
  border-radius: 50%;
  border: 1.5px solid var(--border-strong);
}
.basemap-radio.checked {
  border-color: var(--accent);
  box-shadow: inset 0 0 0 3px var(--accent);
}
.basemap-name {
  flex: none;
  font-weight: 500;
}
.basemap-hint {
  margin-left: auto;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 12px;
  color: var(--text-muted);
}
.basemap-date {
  padding: 0 8px 8px 28px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.basemap-date-select {
  width: 100%;
}
.basemap-date-select :deep(.el-select__wrapper),
.basemap-date-select :deep(.el-select__wrapper.is-focused) {
  background: transparent !important;
}
.basemap-note {
  font-size: 12px;
  line-height: 1.5;
  color: var(--text-muted);
}
.basemap-note.error {
  color: var(--accent-red, #dc4c4c);
}
.basemap-note.warn {
  color: #b0741a;
}
.basemap-retry {
  margin-left: 4px;
  padding: 0;
  border: none;
  background: transparent;
  color: var(--accent);

  font-size: 12px;
  cursor: pointer;
}

.layer-tree-empty {
  padding: 24px 12px;
  text-align: center;
  color: var(--text-muted);
  font-size: 12px;
}

.layer-ws-group {
  border-bottom: none;
  padding: 0 0 2px;
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
  font-size: 12px;
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
  accent-color: #1a1a18;
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
  background: var(--bg-hover);
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
  font-size: 12px;
  line-height: 18px;
}

.layer-search-result-empty {
  padding: 8px 8px 0;
  color: var(--text-muted);
  font-size: 12px;
  text-align: center;
}

.ws-pick-title {
  padding: 2px 4px 8px;
  font-size: 13px;
  color: var(--text-primary);
}

.ws-pick-body {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 0 4px 4px;
}

.ws-pick-row {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  color: var(--text-primary);
}

.ws-pick-select,
.ws-pick-input {
  margin-left: 22px;
  height: 30px;
  padding: 0 8px;
  border: 1px solid var(--glass-border);
  border-radius: var(--r-md);
  background: var(--glass-surface-strong);
  color: var(--text-primary);
  font-size: 12px;
  outline: none;
}

.ws-pick-input {
  width: calc(100% - 22px);
}

.ws-pick-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  padding: 10px 4px 2px;
}

.ws-pick-btn {
  height: 28px;
  padding: 0 14px;
  border: 1px solid var(--glass-border);
  border-radius: var(--r-md);
  background: transparent;
  color: var(--text-primary);
  cursor: pointer;
  font-size: 12px;
}

.ws-pick-btn.primary {
  background: var(--accent);
  border-color: var(--accent);
  color: #fff;
}
</style>
