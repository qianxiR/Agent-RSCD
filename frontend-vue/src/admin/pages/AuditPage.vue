<!-- 管理端 · 审计日志（只读；before/after diff） -->
<script setup>
import { computed, onMounted, ref } from 'vue'
import Icon from '@/shared/ui/Icon.vue'
import PageHeader from '@/shared/ui/PageHeader.vue'
import StatCard from '@/shared/ui/StatCard.vue'
import EmptyState from '@/shared/ui/EmptyState.vue'
import { api } from '@/shared/api/client'
import { formatDateTime } from '@/shared/utils/format'

const logs = ref([])
const loading = ref(false)
const error = ref('')
const actor = ref('')
const action = ref('')
const limit = ref(100)
const expanded = ref(null)

const results = computed(() => ({
  total: logs.value.length,
  success: logs.value.filter((l) => l.result === 'success').length,
  denied: logs.value.filter((l) => l.result !== 'success').length,
  actors: new Set(logs.value.map((l) => l.actor_id).filter(Boolean)).size,
}))

async function load() {
  loading.value = true
  error.value = ''
  try {
    const qs = new URLSearchParams()
    if (actor.value.trim()) qs.set('actor', actor.value.trim())
    if (action.value.trim()) qs.set('action', action.value.trim())
    qs.set('limit', limit.value)
    const data = await api.get(`/api/v1/admin/audit?${qs}`)
    logs.value = data?.logs || []
  } catch (e) {
    error.value = e.message || '审计日志加载失败'
  } finally {
    loading.value = false
  }
}

function pretty(json) {
  if (!json) return ''
  try {
    return JSON.stringify(typeof json === 'string' ? JSON.parse(json) : json, null, 2)
  } catch {
    return String(json)
  }
}

function toggle(row) {
  expanded.value = expanded.value === row.id ? null : row.id
}

onMounted(load)
</script>

<template>
  <div class="page">
    <PageHeader title="审计日志" subtitle="管理动作全量留痕，只读不可修改；按 request_id 可追踪单次请求。">
      <template #actions>
        <button class="btn" :disabled="loading" @click="load">
          <Icon name="refresh" :size="14" />
          刷新
        </button>
        <button class="btn" disabled title="导出功能 P1 接入">导出 CSV</button>
      </template>
    </PageHeader>

    <p v-if="error" class="alert alert-error" style="margin-bottom: 12px">
      <Icon name="alert" :size="14" />
      <span>{{ error }}</span>
    </p>

    <div class="kpi-grid">
      <StatCard label="本次结果" :value="results.total" :foot="`最多 ${limit} 条`" />
      <StatCard label="成功" :value="results.success" foot="result=success" />
      <StatCard label="失败/拒绝" :value="results.denied" :tone="results.denied ? 'warn' : 'normal'" foot="含权限拒绝" />
      <StatCard label="操作者" :value="results.actors" foot="去重统计" />
    </div>

    <div class="toolbar">
      <div class="topbar-search" style="flex: 0 1 220px">
        <span class="search-icon"><Icon name="search" :size="14" /></span>
        <input v-model="actor" placeholder="操作者 ID / 名称" @keyup.enter="load" />
      </div>
      <div class="topbar-search" style="flex: 0 1 240px">
        <span class="search-icon"><Icon name="search" :size="14" /></span>
        <input v-model="action" placeholder="动作，如 user.create" @keyup.enter="load" />
      </div>
      <select v-model.number="limit" class="select" style="width: 110px" @change="load">
        <option :value="50">最近 50</option>
        <option :value="100">最近 100</option>
        <option :value="200">最近 200</option>
      </select>
      <button class="btn" @click="load">查询</button>
      <span class="grow"></span>
      <span class="small muted">共 {{ logs.length }} 条</span>
    </div>

    <section class="card">
      <div class="card-bd flush">
        <div v-if="logs.length" class="table-wrap">
          <table class="table">
            <thead>
              <tr>
                <th style="width: 150px">时间</th>
                <th style="width: 120px">操作者</th>
                <th style="width: 170px">动作</th>
                <th style="width: 160px">对象</th>
                <th style="width: 110px">IP</th>
                <th style="width: 70px">结果</th>
                <th style="width: 60px"></th>
              </tr>
            </thead>
            <tbody>
              <template v-for="l in logs" :key="l.id">
                <tr class="log-row" @click="toggle(l)">
                  <td class="small secondary nowrap mono">{{ formatDateTime(l.created_at) }}</td>
                  <td class="small">{{ l.actor_name || l.actor_id || '系统' }}</td>
                  <td><span class="mono small">{{ l.action }}</span></td>
                  <td class="small muted">
                    {{ l.resource_type || '—' }}
                    <span v-if="l.resource_id" class="mono">#{{ String(l.resource_id).slice(0, 8) }}</span>
                  </td>
                  <td class="mono small muted">{{ l.ip || '—' }}</td>
                  <td>
                    <span class="badge" :class="l.result === 'success' ? 'badge-green' : 'badge-red'">
                      {{ l.result === 'success' ? '成功' : '失败' }}
                    </span>
                  </td>
                  <td class="col-actions">
                    <Icon :name="expanded === l.id ? 'chevronDown' : 'arrowRight'" :size="14" />
                  </td>
                </tr>
                <tr v-if="expanded === l.id" :key="`${l.id}-detail`" class="log-detail">
                  <td colspan="7">
                    <div class="detail-grid">
                      <div class="detail-col">
                        <p class="detail-label">变更前</p>
                        <pre class="detail-json">{{ pretty(l.before_json) || '（无）' }}</pre>
                      </div>
                      <div class="detail-col">
                        <p class="detail-label">变更后</p>
                        <pre class="detail-json">{{ pretty(l.after_json) || '（无）' }}</pre>
                      </div>
                      <div class="detail-meta">
                        <p class="detail-label">上下文</p>
                        <dl class="kv">
                          <dt>请求 ID</dt>
                          <dd class="mono small">{{ l.request_id || '—' }}</dd>
                          <dt>User-Agent</dt>
                          <dd class="small muted" style="word-break: break-all">
                            {{ l.user_agent || '—' }}
                          </dd>
                          <dt>记录 ID</dt>
                          <dd class="mono small">{{ l.id }}</dd>
                        </dl>
                      </div>
                    </div>
                  </td>
                </tr>
              </template>
            </tbody>
          </table>
        </div>
        <EmptyState v-else icon="audit" title="暂无审计记录" description="登录、用户、角色与权限相关动作会自动写入。" />
      </div>
    </section>
  </div>
</template>

<style scoped>
.log-row {
  cursor: pointer;
}
.log-detail td {
  background: var(--bg-inset);
  padding: 12px 14px;
}
.detail-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 12px;
}
.detail-label {
  font-size: var(--fs-xs);
  color: var(--text-muted);
  margin-bottom: 4px;
  letter-spacing: 0.3px;
}
.detail-json {
  font-family: var(--mono);
  font-size: var(--fs-xs);
  line-height: 1.6;
  background: var(--bg-panel);
  border: 1px solid var(--border);
  border-radius: var(--r-sm);
  padding: 8px;
  max-height: 180px;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-all;
}
</style>
