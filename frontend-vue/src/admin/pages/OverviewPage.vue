<!-- 管理端 · 运营总览 -->
<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import Icon from '@/shared/ui/Icon.vue'
import PageHeader from '@/shared/ui/PageHeader.vue'
import StatCard from '@/shared/ui/StatCard.vue'
import EmptyState from '@/shared/ui/EmptyState.vue'
import { api } from '@/shared/api/client'
import { formatBytes, fromNow, roleLabel, userStatus } from '@/shared/utils/format'

const router = useRouter()
const overview = ref(null)
const logs = ref([])
const users = ref([])
const loading = ref(false)
const error = ref('')

// 管理端模块接入状态（P0 已接入的在前）
const MODULES = [
  { label: '用户与组织', to: '/admin/users', ready: true },
  { label: '角色与权限', to: '/admin/roles', ready: true },
  { label: '审计日志', to: '/admin/audit', ready: true },
  { label: '项目管理', to: '/admin/projects', ready: false },
  { label: '数据治理', to: '/admin/datasets', ready: false },
  { label: '图层服务', to: '/admin/layers', ready: false },
  { label: '任务与模型', to: '/admin/jobs', ready: false },
  { label: '存储与配额', to: '/admin/storage', ready: false },
]

const activeRate = computed(() => {
  if (!overview.value?.users) return '—'
  return `${Math.round((overview.value.active_users / overview.value.users) * 100)}% 启用`
})

async function load() {
  loading.value = true
  error.value = ''
  const results = await Promise.allSettled([
    api.get('/api/v1/admin/overview'),
    api.get('/api/v1/admin/audit?limit=8'),
    api.get('/api/v1/admin/users?limit=5'),
  ])
  if (results[0].status === 'fulfilled') overview.value = results[0].value
  else error.value = results[0].reason?.message || '总览数据加载失败'
  logs.value = results[1].status === 'fulfilled' ? results[1].value?.logs || [] : []
  users.value = results[2].status === 'fulfilled' ? results[2].value?.users || [] : []
  loading.value = false
}

onMounted(load)
</script>

<template>
  <div class="page">
    <PageHeader title="运营总览" subtitle="全系统账号、项目与治理动作一览。">
      <template #actions>
        <button class="btn" :disabled="loading" @click="load">
          <Icon name="refresh" :size="14" />
          刷新
        </button>
        <button class="btn btn-primary" @click="router.push('/admin/users')">
          <Icon name="users" :size="14" />
          用户管理
        </button>
      </template>
    </PageHeader>

    <p v-if="error" class="alert alert-error" style="margin-bottom: 12px">
      <Icon name="alert" :size="14" />
      <span>{{ error }}</span>
    </p>

    <div class="kpi-grid">
      <StatCard
        label="用户总数"
        :value="overview ? overview.users : '—'"
        :foot="activeRate"
        @click="router.push('/admin/users')"
      />
      <StatCard
        label="启用用户"
        :value="overview ? overview.active_users : '—'"
        foot="可正常登录"
      />
      <StatCard
        label="项目"
        :value="overview ? overview.projects : '—'"
        foot="全系统项目数"
      />
      <StatCard
        label="运行任务"
        :value="overview ? overview.running_jobs : '—'"
        foot="任务队列接入后实时统计"
        tone="warn"
      />
      <StatCard
        label="存储占用"
        :value="formatBytes(overview?.storage_used_bytes, '—')"
        foot="OSS 接入后统计"
      />
    </div>

    <p v-if="overview?.note" class="alert alert-info" style="margin-bottom: 12px">
      <Icon name="alert" :size="14" />
      <span>{{ overview.note }}；P0 已打通账号、角色、权限、MFA 与审计，任务/存储指标随 P1 监控接入补齐。</span>
    </p>

    <div class="grid-2">
      <section class="card">
        <div class="card-hd">
          <span class="card-title">最近管理动作</span>
          <div class="card-actions">
            <button class="link-btn" @click="router.push('/admin/audit')">全部审计 →</button>
          </div>
        </div>
        <div class="card-bd flush">
          <div v-if="logs.length" class="table-wrap">
            <table class="table">
              <thead>
                <tr>
                  <th style="width: 110px">时间</th>
                  <th style="width: 110px">操作者</th>
                  <th>动作</th>
                  <th style="width: 130px">对象</th>
                  <th style="width: 70px">结果</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="l in logs" :key="l.id">
                  <td class="small secondary nowrap">{{ fromNow(l.created_at) }}</td>
                  <td class="small">{{ l.actor_name || l.actor_id || '—' }}</td>
                  <td><span class="mono small">{{ l.action }}</span></td>
                  <td class="small muted">
                    {{ l.resource_type || '—' }}
                    <span v-if="l.resource_id" class="mono">#{{ String(l.resource_id).slice(0, 8) }}</span>
                  </td>
                  <td>
                    <span class="badge" :class="l.result === 'success' ? 'badge-green' : 'badge-red'">
                      {{ l.result === 'success' ? '成功' : l.result || '—' }}
                    </span>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
          <EmptyState v-else icon="audit" title="暂无审计记录" description="登录、改权限等管理动作会自动写入审计日志。" />
        </div>
      </section>

      <div class="stack">
        <section class="card">
          <div class="card-hd">
            <span class="card-title">用户概览</span>
            <div class="card-actions">
              <button class="link-btn" @click="router.push('/admin/users')">管理用户 →</button>
            </div>
          </div>
          <div class="card-bd flush">
            <ul v-if="users.length" class="mini-users">
              <li v-for="u in users" :key="u.id">
                <span class="avatar">{{ (u.username || '?').slice(0, 1).toUpperCase() }}</span>
                <div class="mini-user-main">
                  <span class="mini-user-name">{{ u.username }}</span>
                  <span class="small muted">{{ u.email || '未填写邮箱' }}</span>
                </div>
                <span class="tag">{{ (u.roles || []).map(roleLabel).join(' / ') || '无角色' }}</span>
                <span class="badge" :class="`badge-${userStatus(u.status).tone}`">
                  {{ userStatus(u.status).label }}
                </span>
              </li>
            </ul>
            <EmptyState v-else icon="users" title="暂无用户" />
          </div>
        </section>

        <section class="card">
          <div class="card-hd"><span class="card-title">治理模块进度</span></div>
          <div class="card-bd">
            <ul class="module-list">
              <li v-for="m in MODULES" :key="m.to">
                <button class="module-row" @click="router.push(m.to)">
                  <span class="module-name">{{ m.label }}</span>
                  <span class="badge" :class="m.ready ? 'badge-green' : 'badge-gray'">
                    {{ m.ready ? '已接入' : 'P1 接入' }}
                  </span>
                </button>
              </li>
            </ul>
          </div>
        </section>
      </div>
    </div>
  </div>
</template>

<style scoped>
.mini-users {
  list-style: none;
}
.mini-users li {
  display: flex;
  align-items: center;
  gap: 9px;
  padding: 9px 14px;
  border-bottom: 1px solid var(--border);
}
.mini-users li:last-child {
  border-bottom: 0;
}
.mini-user-main {
  display: flex;
  flex-direction: column;
  min-width: 0;
  flex: 1;
}
.mini-user-name {
  font-size: var(--fs-md);
  font-weight: 600;
}
.stack {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.stack .card + .card {
  margin-top: 0;
}
.module-list {
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.module-row {
  display: flex;
  align-items: center;
  width: 100%;
  padding: 6px 8px;
  border: 0;
  border-radius: var(--r-sm);
  background: transparent;
  cursor: pointer;
  text-align: left;
}
.module-row:hover {
  background: var(--bg-hover);
}
.module-name {
  flex: 1;
  font-size: var(--fs-md);
  color: var(--text-primary);
}
</style>
