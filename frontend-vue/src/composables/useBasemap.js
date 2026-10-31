// ==================== 底图状态（底图类型 + Esri 历史时相）====================
// 背景: 底图原先写死 Esri World Imagery，并在挂载后静默替换成「最新 Wayback 版本」：
//   界面上既不能换底图，也不能指定历史时相；Wayback 不可达时整张底图会变空白且没有任何提示。
// 方法: 把「底图类型」与「Esri 时相」抽成模块级状态，图层注入仍由 useMap 完成；
//   选择结果写入 localStorage，刷新/重进工作台后保持；Wayback 清单不可达时给出可读错误。
// 说明: 时相只对 Esri 影像有效，取 Wayback 发布版本（每年一版），日期是版本发布日期，不是单景成像日期。
import { computed, ref } from 'vue'
import { setBasemap, setWaybackBasemap } from './useMap'
import { getEsriWaybackReleases } from '@/services/imagery'

const HAS_TDT_KEY = Boolean(import.meta.env.VITE_TDT_KEY)
const STORAGE_KEY = 'rsiis:basemap:v1'
const DATE_RE = /^\d{4}-\d{2}-\d{2}$/

// 底图候选项：value 与 useMap.setBasemap 的类型一一对应
export const BASEMAP_OPTIONS = [
  { value: 'esri', label: 'Esri 影像', hint: '最新高分影像镶嵌，无单一日期' },
  { value: 'img', label: '天地图影像', hint: '卫星影像 + 影像注记' },
  { value: 'vec', label: '天地图矢量', hint: '电子地图 + 矢量注记' },
  { value: 'ter', label: '天地图地形', hint: '地形晕渲 + 地形注记' },
  { value: 'none', label: '无底图', hint: '只显示业务图层' },
]

// 底图类型：'esri' | 'img' | 'vec' | 'ter' | 'none'
const kind = ref('esri')
// Esri 时相：'' 表示最新影像镶嵌；否则为 Wayback 发布版本日期（YYYY-MM-DD）
const releaseDate = ref('')
const releases = ref([])
const loading = ref(false)
const error = ref('')
// 清单接口不可达、使用内置年度快照时置 true（仍可对比，但日期可能滞后）
const fallback = ref(false)
let loadPromise = null

// 入参: value 底图类型 | 方法: 判断该底图当前是否可用（天地图需要 VITE_TDT_KEY） | 出参: boolean
export function basemapAvailable(value) {
  if (value === 'img' || value === 'vec' || value === 'ter') return HAS_TDT_KEY
  return true
}

// 入参: 无 | 方法: 按年收敛 Esri 历史版本（每年取该年最后一个发布版），年份从新到旧 | 出参: release 数组
const releaseYears = computed(() => {
  const seen = new Set()
  return releases.value.filter((item) => {
    const year = String(item.date || '').slice(0, 4)
    if (!DATE_RE.test(String(item.date || '')) || seen.has(year)) return false
    seen.add(year)
    return true
  })
})

// 入参: 无 | 方法: 底图状态摘要（供图层面板标题展示） | 出参: 文本
const summary = computed(() => {
  const option = BASEMAP_OPTIONS.find((item) => item.value === kind.value)
  const name = option?.label || kind.value
  if (kind.value === 'esri' && releaseDate.value) return `${name} · ${releaseDate.value}`
  return name
})

// 入参: 无 | 方法: 读取上次选择的底图 | 出参: { kind, date } | null
function readPersisted() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return null
    const parsed = JSON.parse(raw)
    if (!parsed || !BASEMAP_OPTIONS.some((item) => item.value === parsed.kind)) return null
    return {
      kind: parsed.kind,
      date: DATE_RE.test(String(parsed.date || '')) ? parsed.date : '',
    }
  } catch {
    return null
  }
}

// 入参: 无 | 方法: 记住当前底图选择（隐私模式下写入失败时忽略） | 出参: 无
function persist() {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ kind: kind.value, date: releaseDate.value }))
  } catch {
    /* 隐私模式/配额不足时忽略 */
  }
}

// 入参: { force } 是否跳过缓存
// 方法: 拉取 Wayback 版本清单并做字段校验（防止旧版本缓存形状不一致导致面板渲染报错）
// 出参: Promise<release[]>；失败时 releases 置空并写 error，不抛异常
export function loadBasemapReleases({ force = false } = {}) {
  if (loadPromise) return loadPromise
  if (!force && releases.value.length) return Promise.resolve(releases.value)
  loading.value = true
  error.value = ''
  loadPromise = getEsriWaybackReleases({ force })
    .then((result) => {
      releases.value = (result.releases || []).filter((item) => (
        item && DATE_RE.test(String(item.date || '')) && item.m && item.tileUrl
      ))
      fallback.value = result.source === 'fallback'
      if (!releases.value.length) error.value = 'Esri 历史版本清单为空'
      return releases.value
    })
    .catch((e) => {
      releases.value = []
      error.value = e?.message || 'Esri 历史版本清单加载失败'
      return []
    })
    .finally(() => {
      loading.value = false
      loadPromise = null
    })
  return loadPromise
}

// 入参: value 底图类型 | 方法: 立即切换底图（清空时相）并持久化 | 出参: boolean 是否生效
export function applyBasemap(value) {
  if (!BASEMAP_OPTIONS.some((item) => item.value === value)) return false
  if (!basemapAvailable(value)) {
    error.value = '天地图需要配置 VITE_TDT_KEY'
    return false
  }
  setBasemap(value)
  kind.value = value
  releaseDate.value = ''
  error.value = ''
  persist()
  return true
}

// 入参: date Wayback 发布日期；'' 表示回到最新影像
// 方法: 按日期取版本 → 调用 setWaybackBasemap 替换底图；失败时保留当前底图并写 error
// 出参: Promise<boolean>
export async function applyBasemapDate(date) {
  const value = DATE_RE.test(String(date || '')) ? String(date) : ''
  if (!value) return applyBasemap('esri')
  if (!releases.value.length) {
    await loadBasemapReleases()
    // 清单加载失败时保留 loadBasemapReleases 写入的可读错误，不再覆盖成「未找到版本」
    if (!releases.value.length) return false
  }
  const release = releases.value.find((item) => item.date === value)
  if (!release) {
    error.value = `未找到 ${value} 的历史版本`
    return false
  }
  // setWaybackBasemap 会先探活一张瓦片：不可达时保持当前底图，返回 false
  const ok = await setWaybackBasemap(release)
  if (!ok) {
    // 保留当前底图与时相；不写入失败日期，避免下次刷新继续用不可达版本
    error.value = '当前网络无法访问 Esri 历史影像，已保持当前底图'
    return false
  }
  kind.value = 'esri'
  releaseDate.value = value
  error.value = ''
  persist()
  return true
}

// 入参: 无
// 方法: 地图挂载后恢复上次底图；默认沿用 loadDefaultLayers 注入的「Esri 最新影像」，
//   不再静默升级到 Wayback —— 避免历史影像不可达时底图整片空白
// 出参: Promise<void>
export async function restoreBasemap() {
  const saved = readPersisted()
  const savedKind = saved?.kind || 'esri'
  if (savedKind !== 'esri') {
    applyBasemap(savedKind)
    return
  }
  kind.value = 'esri'
  releaseDate.value = ''
  if (!saved?.date) {
    persist()
    return
  }
  await loadBasemapReleases()
  const ok = await applyBasemapDate(saved.date)
  if (!ok) {
    // 历史底图不可用：显式回到最新 Esri 影像，绝不让地图停在空白状
    const message = error.value
    setBasemap('esri')
    kind.value = 'esri'
    releaseDate.value = ''
    persist()
    error.value = message
  }
}

// 入参: 无 | 方法: 供组件消费的只读状态与操作 | 出参: 对象
export function useBasemap() {
  return {
    kind,
    releaseDate,
    releases,
    releaseYears,
    loading,
    error,
    fallback,
    summary,
    basemapAvailable,
    loadBasemapReleases,
    applyBasemap,
    applyBasemapDate,
    restoreBasemap,
  }
}
