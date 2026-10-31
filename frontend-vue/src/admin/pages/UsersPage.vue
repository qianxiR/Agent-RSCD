<!-- 管理端 · 用户与组织（P0 已接通真实 API） -->
<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import Icon from '@/shared/ui/Icon.vue'
import PageHeader from '@/shared/ui/PageHeader.vue'
import StatCard from '@/shared/ui/StatCard.vue'
import EmptyState from '@/shared/ui/EmptyState.vue'
import AppModal from '@/shared/ui/AppModal.vue'
import DangerConfirm from '@/shared/ui/DangerConfirm.vue'
import { api } from '@/shared/api/client'
import { fromNow, roleLabel, userStatus } from '@/shared/utils/format'

const users = ref([])
const roles = ref([])
const loading = ref(false)
const error = ref('')
const notice = ref('')
const keyword = ref('')
const statusFilter = ref('all')

// 弹窗状态
const createOpen = ref(false)
const editOpen = ref(false)
const disableOpen = ref(false)
const saving = ref(false)
const formError = ref('')
const target = ref(null)

const form = reactive({ username: '', email: '', password: '', roles: [] })
const editForm = reactive({ email: '', status: 'active', password: '', roles: [] })

const counts = computed(() => ({
  all: users.value.length,
  active: users.value.filter((u) => u.status === 'active').length,
  disabled: users.value.filter((u) => u.status === 'disabled').length,
  mfa: users.value.filter((u) => u.mfa_enabled).length,
}))

const filtered = computed(() => {
  let list = users.value
  if (statusFilter.value !== 'all') list = list.filter((u) => u.status === statusFilter.value)
  const q = keyword.value.trim().toLowerCase()
  if (q) {
    list = list.filter(
      (u) =>
        (u.username || '').toLowerCase().includes(q) ||
        (u.email || '').toLowerCase().includes(q) ||
        (u.id || '').toLowerCase().includes(q),
    )
  }
  return list
})

function flash(message) {
  notice.value = message
  setTimeout(() => {
    if (notice.value === message) notice.value = ''
  }, 3200)
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const [userRes, roleRes] = await Promise.allSettled([
      api.get('/api/v1/admin/users?limit=200'),
      api.get('/api/v1/admin/roles'),
    ])
    if (userRes.status === 'fulfilled') users.value = userRes.value?.users || []
    else error.value = userRes.reason?.message || '用户列表加载失败'

    if (roleRes.status === 'fulfilled' && roleRes.value?.roles) {
      roles.value = roleRes.value.roles
    } else {
      // 只有 user:manage 权限时也能用内置角色兜底
      roles.value = ['system_admin', 'project_admin', 'analyst', 'viewer'].map((code) => ({
        code,
        name: roleLabel(code),
        builtin: true,
      }))
    }
  } finally {
    loading.value = false
  }
}

function openCreate() {
  form.username = ''
  form.email = ''
  form.password = ''
  form.roles = ['analyst']
  formError.value = ''
  createOpen.value = true
}

async function submitCreate() {
  formError.value = ''
  if (!form.username.trim()) {
    formError.value = '用户名不能为空'
    return
  }
  if (form.password.length < 12) {
    formError.value = '口令至少 12 位（后端强校验）'
    return
  }
  if (!form.roles.length) {
    formError.value = '至少选择一个角色'
    return
  }
  saving.value = true
  try {
    await api.post('/api/v1/admin/users', {
      username: form.username.trim(),
      email: form.email.trim() || null,
      password: form.password,
      roleCodes: form.roles,
    })
    createOpen.value = false
    flash(`已创建用户 ${form.username}，请通过安全渠道告知初始口令`)
    await load()
  } catch (e) {
    formError.value = e.message || '创建失败'
  } finally {
    saving.value = false
  }
}

function openEdit(user) {
  target.value = user
  editForm.email = user.email || ''
  editForm.status = user.status || 'active'
  editForm.password = ''
  editForm.roles = [...(user.roles || [])]
  formError.value = ''
  editOpen.value = true
}

async function submitEdit() {
  formError.value = ''
  if (editForm.password && editForm.password.length < 12) {
    formError.value = '新口令至少 12 位'
    return
  }
  saving.value = true
  try {
    await api.patch(`/api/v1/admin/users/${target.value.id}`, {
      email: editForm.email.trim() || null,
      status: editForm.status,
      password: editForm.password || null,
      roleCodes: editForm.roles,
    })
    editOpen.value = false
    flash('用户信息已更新（已写入审计）')
    await load()
  } catch (e) {
    formError.value = e.message || '保存失败'
  } finally {
    saving.value = false
  }
}

function askDisable(user) {
  target.value = user
  disableOpen.value = true
}

async function confirmDisable() {
  saving.value = true
  try {
    await api.del(`/api/v1/admin/users/${target.value.id}`)
    disableOpen.value = false
    flash(`已禁用 ${target.value.username}，其 refresh token 已全部撤销`)
    await load()
  } catch (e) {
    disableOpen.value = false
    error.value = e.message || '禁用失败'
  } finally {
    saving.value = false
  }
}

async function enable(user) {
  saving.value = true
  try {
    await api.patch(`/api/v1/admin/users/${user.id}`, { status: 'active' })
    flash(`已启用 ${user.username}`)
    await load()
  } catch (e) {
    error.value = e.message || '启用失败'
  } finally {
    saving.value = false
  }
}

async function revokeSessions(user) {
  try {
    await api.post(`/api/v1/admin/users/${user.id}/revoke-sessions`)
    flash(`已撤销 ${user.username} 的全部会话`)
  } catch (e) {
    error.value = e.message || '撤销会话失败'
  }
}

function toggleRole(list, code) {
  const idx = list.indexOf(code)
  if (idx >= 0) list.splice(idx, 1)
  else list.push(code)
}

onMounted(load)
</script>

<template>
  <div class="page">
    <PageHeader title="用户与组织" subtitle="账号、角色、状态与会话管理；禁用后立即失效。">
      <template #actions>
        <button class="btn" :disabled="loading" @click="load">
          <Icon name="refresh" :size="14" />
          刷新
        </button>
        <button class="btn btn-primary" @click="openCreate">
          <Icon name="plus" :size="14" />
          新建用户
        </button>
      </template>
    </PageHeader>

    <p v-if="notice" class="alert alert-info" style="margin-bottom: 12px">
      <Icon name="check" :size="14" />
      <span>{{ notice }}</span>
    </p>
    <p v-if="error" class="alert alert-error" style="margin-bottom: 12px">
      <Icon name="alert" :size="14" />
      <span>{{ error }}</span>
    </p>

    <div class="kpi-grid">
      <StatCard label="用户总数" :value="counts.all" foot="本组织全部账号" />
      <StatCard label="启用" :value="counts.active" foot="可正常登录" />
      <StatCard
        label="禁用"
        :value="counts.disabled"
        :tone="counts.disabled ? 'warn' : 'normal'"
        :foot="counts.disabled ? '会话已撤销' : '无禁用账号'"
      />
      <StatCard label="已绑定 MFA" :value="counts.mfa" foot="管理员强制 TOTP" />
    </div>

    <div class="toolbar">
      <div class="segmented">
        <button class="seg" :class="{ 'is-active': statusFilter === 'all' }" @click="statusFilter = 'all'">
          全部 {{ counts.all }}
        </button>
        <button class="seg" :class="{ 'is-active': statusFilter === 'active' }" @click="statusFilter = 'active'">
          启用 {{ counts.active }}
        </button>
        <button
          class="seg"
          :class="{ 'is-active': statusFilter === 'disabled' }"
          @click="statusFilter = 'disabled'"
        >
          禁用 {{ counts.disabled }}
        </button>
      </div>
      <div class="topbar-search" style="flex: 0 1 280px">
        <span class="search-icon"><Icon name="search" :size="14" /></span>
        <input v-model="keyword" placeholder="搜索用户名 / 邮箱…" />
      </div>
      <span class="grow"></span>
      <span class="small muted">{{ filtered.length }} 条</span>
    </div>

    <section class="card">
      <div class="card-bd flush">
        <div v-if="loading && !users.length" class="card-bd">
          <span class="skel" style="display: block; width: 100%; height: 12px"></span>
          <span class="skel" style="display: block; width: 84%; height: 12px; margin-top: 10px"></span>
        </div>
        <div v-else-if="filtered.length" class="table-wrap">
          <table class="table">
            <thead>
              <tr>
                <th style="width: 22%">用户名</th>
                <th style="width: 22%">邮箱</th>
                <th style="width: 20%">角色</th>
                <th style="width: 10%">状态</th>
                <th style="width: 8%">MFA</th>
                <th style="width: 12%">最近登录</th>
                <th class="col-actions">操作</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="u in filtered" :key="u.id" :class="{ 'is-selected': u.status === 'disabled' }">
                <td>
                  <div class="cell-main">
                    <span class="cell-strong">{{ u.username }}</span>
                    <span class="cell-sub mono">{{ u.id.slice(0, 8) }}</span>
                  </div>
                </td>
                <td class="small">{{ u.email || '—' }}</td>
                <td>
                  <span v-for="r in u.roles" :key="r" class="tag" style="margin-right: 3px">
                    {{ roleLabel(r) }}
                  </span>
                  <span v-if="!u.roles?.length" class="muted small">未分配</span>
                </td>
                <td>
                  <span class="badge" :class="`badge-${userStatus(u.status).tone}`">
                    {{ userStatus(u.status).label }}
                  </span>
                </td>
                <td>
                  <span class="badge" :class="u.mfa_enabled ? 'badge-green' : 'badge-gray'">
                    {{ u.mfa_enabled ? '已绑定' : '未绑定' }}
                  </span>
                </td>
                <td class="small secondary nowrap">{{ fromNow(u.last_login_at, '从未登录') }}</td>
                <td class="col-actions">
                  <button class="btn btn-sm btn-ghost" @click="openEdit(u)">编辑</button>
                  <button class="btn btn-sm btn-ghost" title="撤销全部 refresh token" @click="revokeSessions(u)">
                    撤销会话
                  </button>
                  <button v-if="u.status === 'active'" class="btn btn-sm btn-outline-danger" @click="askDisable(u)">
                    禁用
                  </button>
                  <button v-else class="btn btn-sm" @click="enable(u)">启用</button>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <EmptyState v-else icon="users" title="没有匹配的用户" description="调整筛选条件或新建用户。" />
      </div>
    </section>

    <!-- 新建用户 -->
    <AppModal :open="createOpen" title="新建用户" @close="createOpen = false">
      <div class="field">
        <label>用户名<span class="required">*</span></label>
        <input v-model="form.username" class="input" placeholder="登录名，创建后不可修改" maxlength="40" />
      </div>
      <div class="field">
        <label>邮箱</label>
        <input v-model="form.email" class="input" type="email" placeholder="用于通知与找回" maxlength="120" />
      </div>
      <div class="field">
        <label>初始口令<span class="required">*</span></label>
        <input v-model="form.password" class="input" type="password" placeholder="至少 12 位" />
        <p class="hint">口令不会明文存储（BCrypt）；请通过安全渠道告知本人并提示首次登录修改。</p>
      </div>
      <div class="field">
        <label>角色<span class="required">*</span></label>
        <div class="role-picker">
          <label v-for="r in roles" :key="r.code" class="checkbox">
            <input
              type="checkbox"
              :checked="form.roles.includes(r.code)"
              @change="toggleRole(form.roles, r.code)"
            />
            <span>
              {{ r.name || roleLabel(r.code) }}
              <span class="mono small muted">{{ r.code }}</span>
            </span>
          </label>
        </div>
      </div>
      <p v-if="formError" class="alert alert-error">{{ formError }}</p>
      <template #footer>
        <button class="btn" :disabled="saving" @click="createOpen = false">取消</button>
        <button class="btn btn-primary" :disabled="saving" @click="submitCreate">
          {{ saving ? '创建中…' : '创建用户' }}
        </button>
      </template>
    </AppModal>

    <!-- 编辑用户 -->
    <AppModal :open="editOpen" title="编辑用户" @close="editOpen = false">
      <p class="hint" style="margin-bottom: 10px">
        用户：<strong>{{ target?.username }}</strong>
        <span class="mono muted">（{{ target?.id?.slice(0, 8) }}）</span>
      </p>
      <div class="field">
        <label>邮箱</label>
        <input v-model="editForm.email" class="input" type="email" maxlength="120" />
      </div>
      <div class="field">
        <label>状态</label>
        <select v-model="editForm.status" class="select">
          <option value="active">启用</option>
          <option value="disabled">禁用（立即撤销会话）</option>
        </select>
      </div>
      <div class="field">
        <label>重置口令</label>
        <input v-model="editForm.password" class="input" type="password" placeholder="留空表示不修改；新口令至少 12 位" />
      </div>
      <div class="field">
        <label>角色</label>
        <div class="role-picker">
          <label v-for="r in roles" :key="r.code" class="checkbox">
            <input
              type="checkbox"
              :checked="editForm.roles.includes(r.code)"
              @change="toggleRole(editForm.roles, r.code)"
            />
            <span>
              {{ r.name || roleLabel(r.code) }}
              <span class="mono small muted">{{ r.code }}</span>
            </span>
          </label>
        </div>
      </div>
      <p v-if="formError" class="alert alert-error">{{ formError }}</p>
      <template #footer>
        <button class="btn" :disabled="saving" @click="editOpen = false">取消</button>
        <button class="btn btn-primary" :disabled="saving" @click="submitEdit">
          {{ saving ? '保存中…' : '保存修改' }}
        </button>
      </template>
    </AppModal>

    <!-- 禁用确认 -->
    <DangerConfirm
      :open="disableOpen"
      title="禁用该用户？"
      :description="`禁用后 ${target?.username || ''} 的 refresh token 会被立即撤销，无法再登录；账号数据保留，可随时重新启用。`"
      confirm-text="确认禁用"
      :require-reason="false"
      :busy="saving"
      @close="disableOpen = false"
      @confirm="confirmDisable"
    />
  </div>
</template>

<style scoped>
.role-picker {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 16px;
  padding: 8px 10px;
  border: 1px solid var(--border);
  border-radius: var(--r-md);
  background: var(--bg-inset);
}
</style>
