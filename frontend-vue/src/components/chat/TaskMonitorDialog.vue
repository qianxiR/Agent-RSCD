<script setup>
// ==================== 任务日志监控弹窗 ====================
// ★ 复刻原生 map/task-monitor.js 的 Modal（纯只读快照查看器，不轮询、不回写对话）
//   对话状态由 WS frontend_action 链路独立驱动，本弹窗仅展示任务列表 + 详情 + 执行日志
import { ref, watch, computed } from 'vue'
import { getTasks, getTaskDetail, getTaskLogs } from '@/services/api'

const props = defineProps({
  visible: { type: Boolean, default: false }, // v-model 弹窗显隐
  conversationId: { type: String, default: '' }, // 默认按当前会话过滤
})
const emit = defineEmits(['update:visible'])

const tasks = ref([])
const loading = ref(false)
const filterStatus = ref('') // 空=全部
const filterConv = ref('')
const selectedId = ref(null)
const detail = ref(null) // 任务详情
const logs = ref([]) // 执行日志

const STATUS_OPTS = [
  { v: '', label: '全部' }, { v: 'pending', label: '等待中' },
  { v: 'running', label: '执行中' }, { v: 'done', label: '已完成' },
  { v: 'failed', label: '失败' }, { v: 'cancelled', label: '已取消' },
]
const STATUS_LABEL = { pending: '等待中', running: '执行中', done: '已完成', failed: '失败', cancelled: '已取消' }

// 入参: 无
// 方法: 拉任务列表（按筛选条件 GET /tasks），弹窗打开/点刷新/改筛选时调用
// 出参: 无（写入 tasks ref）
async function refresh() {
  loading.value = true
  try {
    const data = await getTasks({
      conversation_id: filterConv.value || null,
      status: filterStatus.value || null,
      limit: 50,
    })
    tasks.value = data.tasks || []
  } finally {
    loading.value = false
  }
}

// 入参: taskId
// 方法: 并发拉详情 + 日志（点击任务卡片时调用）
// 出参: 无（写入 detail/logs ref）
async function loadDetail(taskId) {
  selectedId.value = taskId
  detail.value = null
  logs.value = []
  const [d, l] = await Promise.all([getTaskDetail(taskId), getTaskLogs(taskId)])
  detail.value = d.task || null
  logs.value = l.logs || []
}

// 入参: 无
// 方法: 关闭任务日志弹窗，通过 v-model 同步父组件 visible 状态
// 出参: 无（副作用：触发 update:visible=false）
function close() {
  emit('update:visible', false)
}

// 入参: ISO 时间字符串或 null
// 方法: 优先按 zh-CN 本地时间格式化，解析失败时保留原始值
// 出参: 格式化后的本地时间（无值返回 '—'）
function fmtTime(t) {
  if (!t) return '—'
  try {
    return new Date(t).toLocaleString('zh-CN', { hour12: false })
  } catch {
    return t
  }
}

// 入参: 任务对象（含 started_at/finished_at）
// 方法: 使用开始/完成时间差计算任务耗时，并按毫秒、秒、分钟分段展示
// 出参: 耗时描述字符串
function fmtDuration(t) {
  if (!t || !t.started_at || !t.finished_at) return '—'
  const ms = new Date(t.finished_at).getTime() - new Date(t.started_at).getTime()
  if (!Number.isFinite(ms) || ms < 0) return '—'
  if (ms < 1000) return ms + 'ms'
  if (ms < 60000) return (ms / 1000).toFixed(1) + 's'
  return Math.floor(ms / 60000) + '分' + Math.floor((ms % 60000) / 1000) + '秒'
}

// 弹窗打开：预填会话筛选 + 拉一次
watch(() => props.visible, (v) => {
  if (!v) return
  filterConv.value = props.conversationId || ''
  selectedId.value = null
  detail.value = null
  logs.value = []
  refresh()
})
</script>

<template>
  <Teleport to="body">
    <div v-if="visible" class="task-monitor-mask" @click.self="close">
      <div class="task-monitor-modal">
        <!-- 头部：标题 + 筛选/刷新/关闭（Element Plus 控件，对齐 LayerPanel 风格） -->
        <div class="task-monitor-header">
          <h3>📋 任务日志监控</h3>
          <div class="task-monitor-actions">
            <el-select v-model="filterStatus" size="small" style="width: 110px" @change="refresh">
              <el-option v-for="o in STATUS_OPTS" :key="o.v" :label="o.label" :value="o.v" />
            </el-select>
            <el-input
              v-model="filterConv"
              size="small"
              style="width: 180px"
              placeholder="会话 ID（空=全部）"
              @keyup.enter="refresh"
            />
            <el-button size="small" :loading="loading" @click="refresh">刷新</el-button>
            <el-button size="small" type="danger" plain @click="close">关闭</el-button>
          </div>
        </div>
        <!-- 双栏：左列表 + 右详情/日志 -->
        <div class="task-monitor-body">
          <div class="task-list-pane">
            <div v-if="!tasks.length && !loading" class="task-empty">暂无任务</div>
            <div
              v-for="t in tasks"
              :key="t.id"
              class="task-row"
              :class="{ active: selectedId === t.id }"
              @click="loadDetail(t.id)"
            >
              <div class="task-row-head">
                <span class="task-status" :class="`task-status-${t.status}`">{{ STATUS_LABEL[t.status] || t.status }}</span>
                <span class="task-row-type">#{{ t.id }} {{ t.tool_name || t.task_type }}</span>
              </div>
              <div class="task-row-meta">⏱ {{ fmtDuration(t) }} · 📅 {{ fmtTime(t.created_at) }}</div>
              <div v-if="t.error" class="task-row-error">{{ t.error.slice(0, 80) }}</div>
            </div>
          </div>
          <div class="task-detail-pane">
            <div v-if="!detail" class="task-empty">点击左侧任务查看详情</div>
            <template v-else>
              <h4>#{{ detail.id }} {{ detail.tool_name || detail.task_type }}</h4>
              <table class="task-attr-table">
                <tbody>
                  <tr><td>类型</td><td>{{ detail.task_type }}</td></tr>
                  <tr><td>工具</td><td>{{ detail.tool_name || '—' }}</td></tr>
                  <tr><td>状态</td><td><span class="task-status" :class="`task-status-${detail.status}`">{{ STATUS_LABEL[detail.status] }}</span></td></tr>
                  <tr><td>进度</td><td>{{ detail.progress || 0 }}%</td></tr>
                  <tr><td>开始</td><td>{{ fmtTime(detail.started_at) }}</td></tr>
                  <tr><td>完成</td><td>{{ fmtTime(detail.finished_at) }}</td></tr>
                  <tr><td>耗时</td><td>{{ fmtDuration(detail) }}</td></tr>
                  <tr v-if="detail.error"><td>错误</td><td class="err-text">{{ detail.error }}</td></tr>
                </tbody>
              </table>
              <div v-if="detail.output" class="task-output">
                <div class="section-title">输出摘要</div>
                <pre>{{ JSON.stringify(detail.output, null, 2).slice(0, 800) }}</pre>
              </div>
              <div class="task-logs">
                <div class="section-title">执行日志（{{ logs.length }}）</div>
                <ul class="log-list">
                  <li v-for="lg in logs" :key="lg.id" :class="`log-level-${lg.level}`">
                    <span class="log-time">{{ fmtTime(lg.created_at) }}</span>
                    <span class="log-level">[{{ lg.level }}]</span>
                    <span class="log-msg">{{ lg.message }}</span>
                    <span v-if="lg.elapsed_ms != null" class="log-elapsed">({{ lg.elapsed_ms }}ms)</span>
                  </li>
                </ul>
              </div>
            </template>
          </div>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
/* 遮罩 + Modal（对齐原生 88vw×78vh） */
.task-monitor-mask {
  position: fixed; inset: 0; z-index: 9999;
  background: var(--glass-overlay);
  backdrop-filter: blur(2px);
  display: flex; align-items: center; justify-content: center;
}
.task-monitor-modal {
  width: 88vw; max-width: 1100px; height: 78vh;
  background: var(--glass-surface);
  border: 1px solid var(--glass-border);
  border-radius: 10px;
  box-shadow: var(--glass-shadow);
  backdrop-filter: var(--glass-blur);
  display: flex; flex-direction: column; overflow: hidden;
}
.task-monitor-header {
  display: flex; align-items: center; justify-content: space-between;
  padding: 12px 16px; border-bottom: 1px solid var(--glass-border-soft);
}
.task-monitor-header h3 { margin: 0; font-size: var(--fs-md); }
.task-monitor-actions { display: flex; gap: 8px; align-items: center; }
.task-monitor-modal :deep(.el-input__wrapper),
.task-monitor-modal :deep(.el-select__wrapper) {
  background: var(--glass-surface-strong);
  border: 1px solid var(--glass-border-strong);
  border-radius: var(--r-sm);
  box-shadow: none;
}
.task-monitor-modal :deep(.el-button) {
  background: var(--glass-surface-strong);
  border-color: var(--glass-border-strong);
  border-radius: var(--r-sm);
  color: var(--text-secondary);
}
.task-monitor-modal :deep(.el-button:hover) {
  border-color: var(--glass-accent-border);
  color: var(--accent);
}
.task-monitor-modal :deep(.el-button--danger.is-plain) {
  background: var(--glass-danger-surface);
  border-color: var(--glass-danger-border);
  color: var(--accent-red);
}

/* 双栏 */
.task-monitor-body { flex: 1; display: flex; min-height: 0; }
.task-list-pane { width: 42%; overflow-y: auto; border-right: 1px solid var(--glass-border-soft); }
.task-detail-pane { flex: 1; overflow-y: auto; padding: 12px 16px; }
.task-empty { padding: 24px; text-align: center; color: var(--text-muted); font-size: var(--fs-sm); }

/* 任务卡片 */
.task-row { padding: 10px 14px; border-bottom: 1px solid var(--glass-border-soft); cursor: pointer; }
.task-row:hover { background: var(--bg-hover); }
.task-row.active { background: color-mix(in srgb, var(--bg-hover) 72%, transparent); border-left: 3px solid var(--accent); }
.task-row-head { display: flex; align-items: center; gap: 8px; margin-bottom: 4px; }
.task-row-type { font-family: var(--mono); font-size: var(--fs-sm); }
.task-row-meta { font-size: var(--fs-xs); color: var(--text-muted); }
.task-row-error { margin-top: 4px; font-size: var(--fs-xs); color: var(--accent-red); }

/* 状态徽标 5 态配色（对齐原生） */
.task-status { font-size: var(--fs-xs); padding: 1px 6px; border-radius: var(--r-xs); color: #fff; }
.task-status-pending { background: #999; }
.task-status-running { background: #f39c12; }
.task-status-done { background: #27ae60; }
.task-status-failed { background: #e74c3c; }
.task-status-cancelled { background: #7f8c8d; }

/* 详情 */
.task-detail-pane h4 { margin: 0 0 8px; font-size: var(--fs-md); }
.task-attr-table { width: 100%; font-size: var(--fs-sm); border-collapse: collapse; margin-bottom: 12px; }
.task-attr-table td { padding: 4px 8px; border-bottom: 1px solid var(--glass-border-soft); }
.task-attr-table td:first-child { width: 80px; color: var(--text-muted); }
.err-text { color: var(--accent-red); word-break: break-all; }
.section-title { font-size: var(--fs-xs); color: var(--text-muted); margin: 8px 0 4px; }
.task-output pre {
  background: var(--bg-inset); padding: 8px; border-radius: var(--r-xs);
  font-size: var(--fs-xs); font-family: var(--mono); max-height: 180px; overflow: auto;
}

/* 日志 */
.log-list { list-style: none; margin: 0; padding: 0; font-size: var(--fs-xs); }
.log-list li { padding: 3px 6px; border-radius: var(--r-xs); margin-bottom: 2px; font-family: var(--mono); }
.log-level-info { background: var(--bg-inset); }
.log-level-warning { background: color-mix(in srgb, var(--accent-amber) 12%, transparent); }
.log-level-error { background: color-mix(in srgb, var(--accent-red) 12%, transparent); }
.log-time { color: var(--text-muted); margin-right: 6px; }
.log-level { font-weight: 600; margin-right: 4px; }
.log-level-error .log-level { color: var(--accent-red); }
.log-level-warning .log-level { color: var(--accent-amber); }
.log-elapsed { color: var(--text-muted); margin-left: 4px; }
</style>
