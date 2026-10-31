<script setup>
// ==================== 开源遥感影像检索弹窗 ====================
// 能力: 按「区域 + 时段 + 云量 + 分辨率」筛选多源开放影像；地图瓦片预览；
//       AOI 裁剪 GeoTIFF 下载 → 加载到地图 / 发布为 GeoServer 图层。
// 数据源: Planetary Computer / Earth Search / Copernicus Data Space / GEE(兜底)。
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import Icon from '@/shared/ui/Icon.vue'
import { getImageryProviders, searchImagery, downloadImagery } from '@/services/imagery'
import { useImagerySearch, REGION_PRESETS } from '@/composables/useImagerySearch'
import { useMap } from '@/composables/useMap'
import { publishGeoServerLayer } from '@/services/api'

const {
  open, bbox, bboxLabel, overlaid,
  requestDraw, setRegion, markOverlaid, clearOverlaid,
} = useImagerySearch()
const {
  addXyzLayer, addImageUrlLayer, addFootprintLayer, addStaticImageLayer, addWmsLayer,
  getViewBbox, clearImageryLayers,
} = useMap()

// ==================== 筛选条件 ====================
const providers = ref([])
const maxArea = ref(0.5)
const providerKeys = ref(['pc'])
const collectionKeys = ref([])
const dateRange = ref([])
const region = ref('chengdu')
const cloudRange = ref([0, 30])
const platform = ref('')
const orbitDirection = ref('')
const processingLevel = ref('')
const sunMin = ref(null)
const advancedOpen = ref(false)
const maxGsd = ref(null)
const sort = ref('date_desc')

const platformOptions = [
  { value: '', label: '全部平台' },
  { value: 'sentinel-2a', label: 'Sentinel-2A' },
  { value: 'sentinel-2b', label: 'Sentinel-2B' },
  { value: 'sentinel-2c', label: 'Sentinel-2C' },
  { value: 'landsat-8', label: 'Landsat 8' },
  { value: 'landsat-9', label: 'Landsat 9' },
  { value: 'sentinel-1a', label: 'Sentinel-1A' },
  { value: 'sentinel-1b', label: 'Sentinel-1B' },
]
const orbitOptions = [
  { value: '', label: '全部轨道' },
  { value: 'ascending', label: '升轨' },
  { value: 'descending', label: '降轨' },
]
const levelOptions = [
  { value: '', label: '全部级别' },
  { value: 'L1C', label: 'L1C' },
  { value: 'L2A', label: 'L2A' },
]
const sunOptions = [
  { value: null, label: '不限太阳高度角' },
  { value: 15, label: '太阳高度角 ≥ 15°' },
  { value: 30, label: '太阳高度角 ≥ 30°' },
  { value: 45, label: '太阳高度角 ≥ 45°' },
]

// ==================== 检索结果 ====================
const items = ref([])
const total = ref(0)
const errors = ref({})
const loading = ref(false)
const searched = ref(false)
const limit = ref(24)
const thumbErrors = ref({})
const downloadingId = ref('')
const downloadResult = ref(null)
const publishing = ref(false)
const providersLoaded = ref(false)
const providerLoading = ref(false)
const lastSearchKey = ref('')

const regionOptions = [
  { value: 'chengdu', label: '成都市' },
  { value: 'sichuan', label: '四川省' },
  { value: 'map', label: '当前地图范围' },
  { value: 'draw', label: '框选范围' },
]

const quickRanges = [
  { label: '近30天', days: 30 },
  { label: '近90天', days: 90 },
  { label: '今年', days: null },
  { label: '近一年', days: 365 },
]

const selectedProviders = computed(() => (
  providers.value.filter((p) => providerKeys.value.includes(p.key))
))

const collectionOptions = computed(() => {
  const map = new Map()
  selectedProviders.value.forEach((provider) => {
    (provider.collections || []).forEach((collection) => {
      if (!map.has(collection.key)) map.set(collection.key, collection)
    })
  })
  return [...map.values()]
})

const errorEntries = computed(() => Object.entries(errors.value).map(([key, msg]) => ({
  key,
  label: providers.value.find((p) => p.key === key)?.label || key,
  msg,
})))

const bboxText = computed(() => (
  bbox.value ? bbox.value.map((v) => Number(v).toFixed(4)).join(', ') : '未选择'
))

const areaDeg2 = computed(() => {
  if (!bbox.value?.length) return 0
  const [minx, miny, maxx, maxy] = bbox.value
  return Math.max(0, (maxx - minx) * (maxy - miny))
})
const areaTooLarge = computed(() => areaDeg2.value > maxArea.value + 1e-9)

// ==================== 初始化 ====================
function isoDay(offsetDays) {
  const d = new Date()
  d.setDate(d.getDate() + offsetDays)
  return d.toISOString().slice(0, 10)
}

// 入参: days 回看天数 | 方法: 设定默认时间范围 | 出参: 无
function setRangeDays(days) {
  if (days == null) {
    const now = new Date()
    dateRange.value = [`${now.getFullYear()}-01-01`, isoDay(0)]
  } else {
    dateRange.value = [isoDay(-days), isoDay(0)]
  }
}

// 入参: 无 | 方法: 拉取数据源清单（含可用状态与集合） | 出参: 无
async function loadProviders() {
  if (providersLoaded.value || providerLoading.value) return
  providerLoading.value = true
  try {
    const res = await getImageryProviders()
    providers.value = res.providers || []
    maxArea.value = Number(res.max_area_deg2) || 0.5
    providersLoaded.value = true
    syncCollections()
  } catch (e) {
    ElMessage.error(`数据源加载失败: ${e.message}`)
  } finally {
    providerLoading.value = false
  }
}

// 入参: 无 | 方法: 保证已选产品集合在当前数据源中存在；为空时默认全选（去航拍） | 出参: 无
function syncCollections() {
  const available = collectionOptions.value.map((c) => c.key)
  const kept = collectionKeys.value.filter((key) => available.includes(key))
  if (kept.length) {
    collectionKeys.value = kept
    return
  }
  collectionKeys.value = collectionOptions.value
    .filter((c) => c.kind !== 'aerial')
    .map((c) => c.key)
}

// ==================== 检索 ====================
// 入参: 无 | 方法: 生成当前筛选条件指纹（用于判断重开弹窗是否需要重检） | 出参: string
function searchKey() {
  return [
    bbox.value?.join(','),
    dateRange.value?.join(','),
    providerKeys.value.join(','),
    collectionKeys.value.join(','),
    cloudRange.value.join('-'),
    platform.value,
    orbitDirection.value,
    processingLevel.value,
    sunMin.value,
    maxGsd.value,
    sort.value,
  ].join('|')
}

// 入参: reset 是否重置分页 | 方法: 调后端多源检索并渲染结果 | 出参: 无
async function runSearch(reset = true) {
  if (loading.value) return
  if (!providerKeys.value.length) {
    ElMessage.warning('请至少选择一个数据源')
    return
  }
  if (!bbox.value?.length) {
    ElMessage.warning('请先选择区域范围')
    return
  }
  if (!dateRange.value?.[0] || !dateRange.value?.[1]) {
    ElMessage.warning('请选择时间范围')
    return
  }
  if (reset) limit.value = 24
  loading.value = true
  try {
    const res = await searchImagery({
      providers: providerKeys.value,
      collections: collectionKeys.value,
      bbox: bbox.value,
      start: dateRange.value[0],
      end: dateRange.value[1],
      cloud_min: cloudRange.value[0],
      cloud_max: cloudRange.value[1],
      platform: platform.value || null,
      orbit_direction: orbitDirection.value || null,
      processing_level: processingLevel.value || null,
      sun_elevation_min: sunMin.value,
      max_gsd: maxGsd.value,
      limit: limit.value,
      sort: sort.value,
    })
    if (res.status !== 'success') throw new Error(res.msg || '检索失败')
    items.value = res.items || []
    total.value = res.total || 0
    errors.value = res.errors || {}
    searched.value = true
    lastSearchKey.value = searchKey()
  } catch (e) {
    ElMessage.error(e.message || '检索失败')
  } finally {
    loading.value = false
  }
}

// 入参: 无 | 方法: 追加一页结果 | 出参: 无
function loadMore() {
  if (loading.value || items.value.length >= total.value) return
  limit.value += 24
  runSearch(false)
}

// 入参: 无 | 方法: 展开高级筛选并滚动到可见区域 | 出参: 无
function toggleAdvanced() {
  advancedOpen.value = !advancedOpen.value
  if (advancedOpen.value) {
    nextTick(() => {
      const adv = document.querySelector('.im-advanced')
      const side = adv?.closest('.im-side')
      if (adv && side) {
        side.scrollTop = adv.offsetTop - side.offsetTop - 6
      }
    })
  }
}

// 入参: 无 | 方法: 恢复默认筛选条件并检索 | 出参: 无
function resetFilters() {
  providerKeys.value = ['pc']
  syncCollections()
  region.value = 'chengdu'
  setRegion(REGION_PRESETS.chengdu.label, [...REGION_PRESETS.chengdu.bbox])
  setRangeDays(90)
  cloudRange.value = [0, 30]
  platform.value = ''
  orbitDirection.value = ''
  processingLevel.value = ''
  sunMin.value = null
  maxGsd.value = null
  advancedOpen.value = false
  sort.value = 'date_desc'
  runSearch()
}

// ==================== 区域 ====================
// 入参: value 区域枚举 | 方法: 预设区域直接生效；地图范围取当前视野；框选则收起弹窗 | 出参: 无
function onRegionChange(value) {
  if (value === 'draw') {
    requestDraw()
    return
  }
  if (value === 'map') {
    const current = getViewBbox()
    if (!current) {
      ElMessage.warning('地图尚未就绪')
      return
    }
    setRegion('当前地图范围', current)
    return
  }
  const preset = REGION_PRESETS[value]
  if (preset) setRegion(preset.label, [...preset.bbox])
}

// 入参: event 复选框变化 | 方法: 数据源增减后同步产品集合 | 出参: 无
function onProvidersChange() {
  syncCollections()
}

// ==================== 预览 / 下载 ====================
// 入参: bbox [minx,miny,maxx,maxy] | 方法: 构造矩形 GeoJSON，作为无 footprint 数据的景轮廓兜底 | 出参: Polygon
function bboxPolygon(bbox) {
  if (!Array.isArray(bbox) || bbox.length !== 4) return null
  const [minx, miny, maxx, maxy] = bbox
  return {
    type: 'Polygon',
    coordinates: [[
      [minx, miny], [maxx, miny], [maxx, maxy], [minx, maxy], [minx, miny],
    ]],
  }
}

// 入参: item 检索结果 | 方法: 提取该景元信息（景号/日期/云量/数据源），供点击影像弹窗 | 出参: 属性对象
function sceneProperties(item) {
  return {
    景号: item.id,
    日期: fmtDate(item.datetime),
    云量: item.cloud_cover != null ? `${Number(item.cloud_cover).toFixed(1)}%` : '—',
    数据源: item.provider_label || item.provider || '—',
  }
}

// 入参: item 检索结果 | 方法: 快视图兜底 + XYZ 瓦片 + 景轮廓，收起弹窗看地图；
//   轮廓登记场景元信息，点击影像即可弹出该景信息 | 出参: 无
function preview(item) {
  if (!item.tile_url && !item.thumbnail) {
    ElMessage.warning('该数据源没有可用预览，请前往源站查看')
    return
  }
  const label = `${item.collection_label} · ${fmtDate(item.datetime)}`
  const sceneBbox = item.bbox || bbox.value
  // ★ 快视图先垫底：即使瓦片源不可达/部分 404，地图上也能看到影像，不会只剩空轮廓
  if (item.thumbnail) {
    addImageUrlLayer({
      url: item.thumbnail,
      name: `thumb:${item.id}`,
      bbox: item.bbox,
      label,
    })
  }
  if (item.tile_url) {
    addXyzLayer({ url: item.tile_url, name: item.id, bbox: item.bbox, label })
  }
  // 没有 footprint 时用检索范围兜底，保证点击影像能命中场景轮廓
  const geometry = item.footprint || bboxPolygon(sceneBbox)
  if (geometry) {
    addFootprintLayer({
      name: item.id,
      geometry,
      bbox: sceneBbox,
      properties: sceneProperties(item),
      title: '影像元信息',
    })
  }
  markOverlaid({ id: item.id, label })
  open.value = false
  ElMessage.success('已叠加到地图，点击影像可查看元信息')
}

// 入参: item 检索结果 | 方法: 范围合规时下载 AOI 裁剪 GeoTIFF；超限则引导框选 | 出参: 无
async function onDownload(item) {
  if (!item.downloadable) {
    ElMessage.info('该数据源仅提供检索与快视图，暂不支持服务端下载')
    return
  }
  if (areaTooLarge.value) {
    try {
      await ElMessageBox.confirm(
        `当前范围 ${areaDeg2.value.toFixed(2)}°²，超过单次下载上限 ${maxArea.value}°²。`
        + '请在地图上框选更小的范围后再下载。',
        '下载范围过大',
        { confirmButtonText: '去框选', cancelButtonText: '取消', type: 'warning' },
      )
      requestDraw()
    } catch {
      /* 用户取消 */
    }
    return
  }
  downloadingId.value = item.id
  try {
    const res = await downloadImagery({
      provider: item.provider,
      collection: item.collection,
      item: item.id,
      bbox: bbox.value,
    })
    if (res.status !== 'success') throw new Error(res.msg || '下载失败')
    downloadResult.value = res
    ElMessage.success('AOI 裁剪影像已下载')
  } catch (e) {
    ElMessage.error(e.message || '下载失败')
  } finally {
    downloadingId.value = ''
  }
}

// 入参: 无 | 方法: 把下载的 GeoTIFF 叠加到地图 | 出参: 无
async function loadDownloaded() {
  if (!downloadResult.value) return
  const res = await addStaticImageLayer({ path: downloadResult.value.file_path })
  if (res.loaded) {
    open.value = false
    ElMessage.success('影像已加载到地图')
  } else {
    ElMessage.error(res.error || '影像加载失败')
  }
}

// 入参: 无 | 方法: 发布下载影像为 GeoServer 栅格图层并叠加 | 出参: 无
async function publishDownloaded() {
  if (!downloadResult.value || publishing.value) return
  publishing.value = true
  try {
    const data = await publishGeoServerLayer({
      file_path: downloadResult.value.file_path,
      layer_name: downloadResult.value.layer_name,
      layer_type: 'raster',
    })
    if (data.status !== 'success') throw new Error(data.msg || '发布失败')
    addWmsLayer({
      name: data.layer_name,
      workspace: data.workspace,
      bbox: downloadResult.value.bbox,
      layerType: 'raster',
    })
    open.value = false
    ElMessage.success('已发布为图层')
  } catch (e) {
    ElMessage.error(e.message || '发布失败')
  } finally {
    publishing.value = false
  }
}

// 入参: 无 | 方法: 清除全部检索影像图层与轮廓 | 出参: 无
function clearOverlays() {
  clearImageryLayers()
  clearOverlaid()
  ElMessage.success('已清除影像图层')
}

// ==================== 格式化 ====================
function parseDate(value) {
  if (!value) return null
  const text = String(value)
  const normalized = /[zZ]|[+-]\d{2}:?\d{2}$/.test(text) ? text : `${text}Z`
  const date = new Date(normalized)
  return Number.isNaN(date.getTime()) ? null : date
}

function fmtDate(value) {
  const date = parseDate(value)
  return date ? date.toISOString().slice(0, 10) : '—'
}

function gsdText(value) {
  if (value == null) return '—'
  return value < 1 ? `${value}m` : `${Math.round(value)}m`
}

function sizeText(bytes) {
  if (!bytes) return '—'
  const mb = bytes / 1024 / 1024
  return mb >= 1 ? `${mb.toFixed(1)} MB` : `${Math.round(bytes / 1024)} KB`
}

// 入参: crs 后端返回的坐标系字符串 | 方法: WKT 太长时压成 EPSG/短名 | 出参: 展示文本
function crsText(value) {
  if (!value) return '—'
  if (/WGS\s*84|EPSG:4326/i.test(value)) return 'WGS 84'
  const match = String(value).match(/EPSG"?\s*:\s*"?(\d+)/i)
  return match ? `EPSG:${match[1]}` : String(value).slice(0, 24)
}

function markThumbError(id) {
  thumbErrors.value = { ...thumbErrors.value, [id]: true }
}

// ==================== 生命周期 ====================
watch(open, async (value) => {
  if (!value) return
  if (!providersLoaded.value) await loadProviders()
  if (!dateRange.value?.length) setRangeDays(90)
  // 首次打开、或筛选条件变化（含框选后回来）时自动检索
  if (!loading.value && (!searched.value || searchKey() !== lastSearchKey.value)) {
    runSearch()
  }
})

watch(bboxLabel, (value) => {
  if (value === '框选范围') region.value = 'draw'
  else if (value === '当前地图范围') region.value = 'map'
  else if (value === '四川省') region.value = 'sichuan'
  else if (value === '成都市') region.value = 'chengdu'
})

// 区域改变后自动按新范围重检（仅弹窗已打开且已检索过）
watch(bbox, () => {
  if (open.value && searched.value && !loading.value) runSearch()
})

onMounted(() => {
  loadProviders()
  setRangeDays(90)
})
</script>

<template>
  <el-dialog
    v-model="open"
    width="1080px"
    top="4vh"
    class="imagery-dialog"
    :show-close="false"
    :close-on-click-modal="false"
    append-to-body
  >
    <template #header>
      <div class="im-head">
        <div class="im-head-title">
          <Icon name="image" :size="17" />
          <span>影像检索</span>
        </div>
        <div class="im-head-right">
          <span v-if="searched" class="im-head-count">共 {{ total }} 景</span>
          <button type="button" class="im-icon-btn" title="关闭" @click="open = false">
            <Icon name="close" :size="16" />
          </button>
        </div>
      </div>
    </template>

    <div class="im-body">
      <!-- 左侧筛选 -->
      <aside class="im-side">
        <div class="im-field">
          <div class="im-label">区域</div>
          <el-select
            v-model="region"
            size="small"
            class="im-control"
            @change="onRegionChange"
          >
            <el-option
              v-for="item in regionOptions"
              :key="item.value"
              :label="item.label"
              :value="item.value"
            />
          </el-select>
          <div class="im-bbox mono">{{ bboxText }}</div>
        </div>

        <div class="im-field">
          <div class="im-label">时间</div>
          <el-date-picker
            v-model="dateRange"
            type="daterange"
            size="small"
            value-format="YYYY-MM-DD"
            range-separator="至"
            start-placeholder="开始日期"
            end-placeholder="结束日期"
            class="im-control"
          />
          <div class="im-quick">
            <button
              v-for="quick in quickRanges"
              :key="quick.label"
              type="button"
              class="im-quick-btn"
              @click="setRangeDays(quick.days)"
            >{{ quick.label }}</button>
          </div>
        </div>

        <div class="im-field">
          <div class="im-label">数据源</div>
          <div class="im-sources">
            <label
              v-for="provider in providers"
              :key="provider.key"
              class="im-source"
              :class="{ active: providerKeys.includes(provider.key), off: !provider.configured }"
              :title="provider.note"
            >
              <input
                v-model="providerKeys"
                type="checkbox"
                :value="provider.key"
                :disabled="!provider.configured"
                @change="onProvidersChange"
              />
              <span class="im-source-name">{{ provider.label }}</span>
              <span class="im-source-state" :class="provider.configured ? 'ok' : 'off'">
                {{ provider.configured ? '可用' : '未配置' }}
              </span>
            </label>
          </div>
        </div>

        <div class="im-field">
          <div class="im-label">卫星 / 产品</div>
          <div class="im-chips">
            <label
              v-for="collection in collectionOptions"
              :key="collection.key"
              class="im-chip"
              :class="{ active: collectionKeys.includes(collection.key) }"
              :title="collection.note"
            >
              <input v-model="collectionKeys" type="checkbox" :value="collection.key" />
              <span>{{ collection.label }}</span>
              <em v-if="collection.gsd">{{ gsdText(collection.gsd) }}</em>
            </label>
          </div>
        </div>

        <div class="im-field">
          <div class="im-label">
            云量
            <span class="im-label-value">{{ cloudRange[0] }}–{{ cloudRange[1] }}%</span>
          </div>
          <el-slider v-model="cloudRange" range :min="0" :max="100" :step="5" size="small" />
        </div>

        <button type="button" class="im-advanced-toggle" @click="toggleAdvanced">
          {{ advancedOpen ? '收起高级筛选' : '高级筛选：平台 / 级别 / 轨道 / 太阳高度角' }}
        </button>

        <div v-show="advancedOpen" class="im-advanced">
          <div class="im-field im-field-row">
          <el-select v-model="platform" size="small" class="im-half" placeholder="全部平台">
            <el-option
              v-for="item in platformOptions"
              :key="item.value"
              :label="item.label"
              :value="item.value"
            />
          </el-select>
          <el-select v-model="processingLevel" size="small" class="im-half" placeholder="全部级别">
            <el-option
              v-for="item in levelOptions"
              :key="item.value"
              :label="item.label"
              :value="item.value"
            />
          </el-select>
        </div>

        <div class="im-field im-field-row">
          <el-select v-model="orbitDirection" size="small" class="im-half" placeholder="全部轨道">
            <el-option
              v-for="item in orbitOptions"
              :key="item.value"
              :label="item.label"
              :value="item.value"
            />
          </el-select>
          <el-select v-model="sunMin" size="small" class="im-half" placeholder="不限太阳高度角">
            <el-option
              v-for="item in sunOptions"
              :key="String(item.value)"
              :label="item.label"
              :value="item.value"
            />
          </el-select>
        </div>
        </div>

        <div class="im-field im-field-row">
          <el-select v-model="maxGsd" size="small" class="im-half" placeholder="全部分辨率">
            <el-option label="全部分辨率" :value="null" />
            <el-option label="≤ 1m" :value="1" />
            <el-option label="≤ 10m" :value="10" />
            <el-option label="≤ 30m" :value="30" />
          </el-select>
          <el-select v-model="sort" size="small" class="im-half">
            <el-option label="最新优先" value="date_desc" />
            <el-option label="云量最少" value="cloud_asc" />
            <el-option label="分辨率最高" value="gsd_asc" />
          </el-select>
        </div>

        <div class="im-field im-download-hint" :class="{ warn: areaTooLarge }">
          <Icon name="alert" :size="12" />
          <span>
            下载上限 {{ maxArea }}°²，当前范围 {{ areaDeg2.toFixed(2) }}°²
            <template v-if="areaTooLarge">，请框选更小范围</template>
          </span>
        </div>

        <div class="im-side-actions">
          <button type="button" class="im-primary" :disabled="loading" @click="runSearch()">
            {{ loading ? '检索中…' : '检索' }}
          </button>
          <button type="button" class="im-ghost" @click="resetFilters">重置</button>
        </div>

        <div class="im-side-note">
          免费开放影像以 10m 级为主；0.6m 级仅美国 NAIP。国内亚米级需商业或政务账号。
          GEE 兜底需服务账号与科学上网，默认未启用。
        </div>
      </aside>

      <!-- 右侧结果 -->
      <main class="im-main">
        <div class="im-toolbar">
          <div class="im-toolbar-left">
            <span class="im-toolbar-title">{{ bboxLabel }}</span>
            <span v-if="overlaid.length" class="im-overlaid">
              已叠加 {{ overlaid.length }} 景
              <button type="button" class="im-link" @click="clearOverlays">清除</button>
            </span>
          </div>
          <span class="im-toolbar-right">
            <template v-if="items.length">显示 {{ items.length }} / {{ total }}</template>
          </span>
        </div>

        <div v-if="errorEntries.length" class="im-alert">
          <Icon name="alert" :size="13" />
          <span>
            {{ errorEntries.map((entry) => entry.label).join('、') }} 暂不可用
          </span>
        </div>

        <div class="im-results">
          <div v-if="loading && !items.length" class="im-grid">
            <div v-for="index in 4" :key="index" class="im-card im-skeleton">
              <div class="im-thumb"></div>
              <div class="im-card-body">
                <div class="im-skeleton-line"></div>
                <div class="im-skeleton-line short"></div>
              </div>
            </div>
          </div>

          <div v-else-if="!searched" class="im-empty">
            <Icon name="image" :size="30" />
            <div class="im-empty-title">选择时间与区域后开始检索</div>
            <div class="im-empty-text">支持 Sentinel-2 / Landsat / Sentinel-1 等开放影像</div>
          </div>

          <div v-else-if="!items.length" class="im-empty">
            <Icon name="search" :size="30" />
            <div class="im-empty-title">没有符合条件的影像</div>
            <div class="im-empty-text">可尝试放宽云量、延长时间范围或切换数据源</div>
          </div>

          <div v-else class="im-grid">
            <article
              v-for="item in items"
              :key="`${item.provider}:${item.id}`"
              class="im-card"
            >
              <div class="im-thumb">
                <img
                  v-if="item.thumbnail && !thumbErrors[item.id]"
                  :src="item.thumbnail"
                  :alt="item.id"
                  loading="lazy"
                  @error="markThumbError(item.id)"
                />
                <div v-else class="im-thumb-empty">
                  <Icon name="image" :size="22" />
                  <span>无快视图</span>
                </div>
                <span class="im-badge left">{{ item.collection_label }}</span>
                <span v-if="item.gsd" class="im-badge right">{{ gsdText(item.gsd) }}</span>
              </div>

              <div class="im-card-body">
                <div class="im-card-title">
                  {{ item.satellite }} · {{ fmtDate(item.datetime) }}
                </div>
                <div class="im-card-meta">
                  <span v-if="item.cloud_cover != null">云量 {{ item.cloud_cover.toFixed(1) }}%</span>
                  <span v-else>云量 —</span>
                  <span class="im-dot">·</span>
                  <span>{{ item.provider_label }}</span>
                </div>
                <div class="im-card-actions">
                  <button
                    type="button"
                    class="im-link"
                    :disabled="!item.tile_url"
                    :title="item.tile_url ? '叠加到地图' : '该源无瓦片预览'"
                    @click="preview(item)"
                  >预览</button>
                  <button
                    type="button"
                    class="im-link"
                    :disabled="!item.downloadable || downloadingId === item.id"
                    :title="item.downloadable ? '下载 AOI 裁剪 GeoTIFF' : '该源仅检索，不提供下载'"
                    @click="onDownload(item)"
                  >{{ downloadingId === item.id ? '下载中…' : '下载' }}</button>
                  <a
                    v-if="item.source_url"
                    class="im-link"
                    :href="item.source_url"
                    target="_blank"
                    rel="noopener"
                  >源站</a>
                </div>
              </div>
            </article>
          </div>

          <div v-if="items.length < total" class="im-more">
            <button type="button" class="im-ghost" :disabled="loading" @click="loadMore">
              {{ loading ? '加载中…' : '加载更多' }}
            </button>
          </div>
        </div>

        <div v-if="downloadResult" class="im-download-result">
          <div class="im-dr-info">
            <div class="im-dr-name" :title="downloadResult.file_name">{{ downloadResult.file_name }}</div>
            <div class="im-dr-meta">
              {{ sizeText(downloadResult.size_bytes) }}
              · {{ downloadResult.width }}×{{ downloadResult.height }}
              · {{ crsText(downloadResult.crs) }}
            </div>
          </div>
          <div class="im-dr-actions">
            <button type="button" class="im-link" @click="loadDownloaded">加载到地图</button>
            <button type="button" class="im-link" :disabled="publishing" @click="publishDownloaded">
              {{ publishing ? '发布中…' : '发布为图层' }}
            </button>
            <a class="im-link" :href="downloadResult.download_url" download>下载文件</a>
            <button type="button" class="im-icon-btn" title="关闭" @click="downloadResult = null">
              <Icon name="close" :size="14" />
            </button>
          </div>
        </div>
      </main>
    </div>
  </el-dialog>
</template>

<style>
/* 弹窗外壳样式（Teleport 到 body，需全局选择器） */
.imagery-dialog.el-dialog {
  padding: 0;
  border-radius: 16px;
  overflow: hidden;
  box-shadow: 0 24px 64px rgba(0, 0, 0, 0.18);
}
.imagery-dialog .el-dialog__header {
  padding: 0;
  margin: 0;
}
.imagery-dialog .el-dialog__body {
  padding: 0;
}
</style>

<style scoped>
.im-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  height: 52px;
  padding: 0 18px;
  border-bottom: 1px solid var(--border);
  background: var(--bg-panel);
}
.im-head-title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: var(--fs-hero);
  font-weight: 600;
  color: var(--text-primary);
}
.im-head-right {
  display: flex;
  align-items: center;
  gap: 12px;
}
.im-head-count {
  font-size: var(--fs-md);
  color: var(--text-secondary);
}
.im-icon-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  color: var(--text-secondary);
  background: transparent;
  border: none;
  border-radius: var(--r-sm);
  cursor: pointer;
}
.im-icon-btn:hover {
  background: var(--bg-hover);
  color: var(--text-primary);
}

.im-body {
  display: flex;
  height: 640px;
  max-height: 86vh;
  background: var(--bg-panel);
}

/* ===== 左侧筛选 ===== */
.im-side {
  width: 272px;
  flex: 0 0 272px;
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 14px;
  border-right: 1px solid var(--border);
  overflow-y: auto;
  background: #fcfcfc;
}
.im-field {
  display: flex;
  flex-direction: column;
  gap: 7px;
}
.im-advanced-toggle {
  padding: 0;
  border: 0;
  background: transparent;
  color: var(--accent);
  font-size: var(--fs-sm);
  text-align: left;
  cursor: pointer;
}
.im-advanced {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.im-field-row {
  flex-direction: row;
  gap: 8px;
}
.im-label {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: var(--fs-md);
  font-weight: 600;
  color: var(--text-primary);
}
.im-label-value {
  font-weight: 400;
  color: var(--accent);
}
.im-control {
  width: 100%;
}
.im-bbox {
  padding: 5px 8px;
  font-size: var(--fs-sm);
  color: var(--text-secondary);
  background: var(--bg-inset);
  border: 1px solid var(--border);
  border-radius: var(--r-sm);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.im-quick {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}
.im-quick-btn {
  padding: 2px 8px;
  font-size: var(--fs-sm);
  color: var(--text-secondary);
  background: var(--bg-panel);
  border: 1px solid var(--border);
  border-radius: 999px;
  cursor: pointer;
}
.im-quick-btn:hover {
  color: var(--accent);
  border-color: var(--accent);
}
.im-sources {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.im-source {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 7px 9px;
  font-size: var(--fs-md);
  color: var(--text-secondary);
  background: var(--bg-panel);
  border: 1px solid var(--border);
  border-radius: var(--r-md);
  cursor: pointer;
  transition: border-color 0.15s, background 0.15s;
}
.im-source:hover {
  border-color: var(--border-strong);
}
.im-source.active {
  color: var(--text-primary);
  border-color: var(--accent);
  background: var(--accent-soft);
}
.im-source.off {
  opacity: 0.55;
  cursor: not-allowed;
}
.im-source input {
  accent-color: var(--accent);
  margin: 0;
}
.im-source-name {
  flex: 1;
}
.im-source-state {
  font-size: var(--fs-xs);
  color: var(--accent-green);
}
.im-source-state.off {
  color: var(--text-muted);
}
.im-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.im-chip {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 4px 9px;
  font-size: var(--fs-sm);
  color: var(--text-secondary);
  background: var(--bg-panel);
  border: 1px solid var(--border);
  border-radius: 999px;
  cursor: pointer;
}
.im-chip.active {
  color: var(--accent);
  background: var(--accent-soft);
  border-color: var(--accent);
}
.im-chip input {
  display: none;
}
.im-chip em {
  font-style: normal;
  font-size: var(--fs-xs);
  opacity: 0.7;
}
.im-download-hint {
  flex-direction: row;
  align-items: flex-start;
  gap: 6px;
  padding: 7px 9px;
  font-size: var(--fs-sm);
  color: var(--text-secondary);
  background: var(--bg-inset);
  border-radius: var(--r-sm);
}
.im-download-hint.warn {
  color: var(--accent-amber);
  background: #fdf6e7;
}
.im-side-actions {
  position: sticky;
  bottom: 0;
  display: flex;
  gap: 8px;
  margin: 0 -14px -14px;
  padding: 10px 14px 12px;
  background: #fcfcfc;
  border-top: 1px solid var(--border);
  z-index: 2;
}
.im-side-actions .im-primary {
  flex: 1;
  min-width: 0;
}
.im-side-actions .im-ghost {
  flex: 0 0 auto;
}

.im-primary {
  flex: 1;
  height: 34px;
  font-size: var(--fs-lg);
  font-weight: 600;
  color: #fff;
  background: var(--accent);
  border: none;
  border-radius: var(--r-md);
  cursor: pointer;
}
.im-primary:hover {
  background: var(--accent-hover);
}
.im-primary:disabled {
  opacity: 0.6;
  cursor: default;
}
.im-ghost {
  height: 34px;
  padding: 0 14px;
  font-size: var(--fs-lg);
  color: var(--text-secondary);
  background: var(--bg-panel);
  border: 1px solid var(--border);
  border-radius: var(--r-md);
  cursor: pointer;
}
.im-ghost:hover {
  color: var(--text-primary);
  border-color: var(--border-strong);
}
.im-side-note {
  font-size: var(--fs-xs);
  line-height: 1.6;
  color: var(--text-muted);
}

/* ===== 右侧结果 ===== */
.im-main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  background: var(--bg-panel);
}
.im-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 16px 8px;
  font-size: var(--fs-md);
  color: var(--text-secondary);
}
.im-toolbar-left {
  display: flex;
  align-items: center;
  gap: 12px;
}
.im-toolbar-title {
  font-weight: 600;
  color: var(--text-primary);
}
.im-overlaid {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: var(--accent);
}
.im-alert {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 0 16px 8px;
  padding: 7px 10px;
  font-size: var(--fs-sm);
  color: var(--accent-amber);
  background: #fdf6e7;
  border-radius: var(--r-sm);
}
.im-results {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 4px 16px 16px;
}
.im-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}
.im-card {
  display: flex;
  flex-direction: column;
  overflow: hidden;
  background: var(--bg-panel);
  border: 1px solid var(--border);
  border-radius: var(--r-lg);
  transition: border-color 0.15s, box-shadow 0.15s;
}
.im-card:hover {
  border-color: var(--border-strong);
  box-shadow: var(--shadow-shell);
}
.im-thumb {
  position: relative;
  height: 132px;
  background: #f1f2f4;
}
.im-thumb img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}
.im-thumb-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 6px;
  height: 100%;
  font-size: var(--fs-sm);
  color: var(--text-muted);
}
.im-badge {
  position: absolute;
  top: 8px;
  padding: 2px 7px;
  font-size: var(--fs-xs);
  color: #fff;
  background: rgba(20, 20, 20, 0.55);
  border-radius: 999px;
  backdrop-filter: blur(4px);
}
.im-badge.left {
  left: 8px;
}
.im-badge.right {
  right: 8px;
}
.im-card-body {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 9px 10px 8px;
}
.im-card-title {
  font-size: var(--fs-lg);
  font-weight: 600;
  color: var(--text-primary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.im-card-meta {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: var(--fs-sm);
  color: var(--text-secondary);
}
.im-dot {
  color: var(--text-muted);
}
.im-card-actions {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-top: 2px;
}
.im-link {
  padding: 0;
  font-size: var(--fs-sm);
  color: var(--accent);
  background: none;
  border: none;
  cursor: pointer;
  text-decoration: none;
}
.im-link:hover {
  text-decoration: underline;
}
.im-link:disabled {
  color: var(--text-muted);
  cursor: not-allowed;
  text-decoration: none;
}
.im-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  height: 100%;
  min-height: 320px;
  color: var(--text-muted);
}
.im-empty-title {
  font-size: var(--fs-xl);
  color: var(--text-secondary);
}
.im-empty-text {
  font-size: var(--fs-md);
}
.im-more {
  display: flex;
  justify-content: center;
  padding: 14px 0 4px;
}
.im-skeleton .im-thumb,
.im-skeleton-line {
  background: linear-gradient(90deg, #f2f2f2 25%, #eaeaea 37%, #f2f2f2 63%);
  background-size: 400% 100%;
  animation: im-shimmer 1.3s ease infinite;
}
.im-skeleton-line {
  height: 10px;
  border-radius: 4px;
  margin-bottom: 7px;
}
.im-skeleton-line.short {
  width: 55%;
}
@keyframes im-shimmer {
  0% { background-position: 100% 50%; }
  100% { background-position: 0 50%; }
}

.im-download-result {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 10px 16px;
  border-top: 1px solid var(--border);
  background: #fafbff;
}
.im-dr-info {
  min-width: 0;
}
.im-dr-name {
  font-size: var(--fs-md);
  font-weight: 600;
  color: var(--text-primary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.im-dr-meta {
  font-size: var(--fs-xs);
  color: var(--text-secondary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.im-dr-actions {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-shrink: 0;
}

/* Element Plus 控件在弹窗内的紧凑化 */
.im-side :deep(.el-select__wrapper),
.im-side :deep(.el-range-editor.el-input__wrapper) {
  min-height: 30px;
  font-size: var(--fs-md);
}
.im-side :deep(.el-date-editor.el-input__wrapper) {
  width: 100%;
  padding: 0 8px;
}
.im-side :deep(.el-range-input) {
  font-size: var(--fs-md);
}
.im-side :deep(.el-slider) {
  padding: 0 4px;
}
.im-side :deep(.el-slider__button) {
  width: 12px;
  height: 12px;
}
</style>
