<!-- 管理端 · 角色与权限（P0 已接通真实 API） -->
<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import Icon from '@/shared/ui/Icon.vue'
import PageHeader from '@/shared/ui/PageHeader.vue'
import EmptyState from '@/shared/ui/EmptyState.vue'
import AppModal from '@/shared/ui/AppModal.vue'
import { api } from '@/shared/api/client'
import { PERMISSION_ORDER, permissionModule } from '@/shared/utils/format'

const roles = ref([])
const permissions = ref([])
const selectedCode = ref('')
const draft = ref([])
const loading = ref(false)
const saving = ref(false)
const error = ref('')
const notice = ref('')

const createOpen = ref(false)
const createError = ref('')
const createForm = reactive({ code: '', name: '', scope: 'project', permissions: [] })

const selected = computed(() => roles.value.find((r) => r.code === selectedCode.value) || null)
const editable = computed(() => !!selected.value && !selected.value.builtin)

// 按模块分组，供权限矩阵渲染
const modules = computed(() => {
  const map = new Map()
  for (const p of permissions.value) {
    const key = permissionModule(p.code, p.module)
    if (!map.has(key)) map.set(key, [])
    map.get(key).push(p)
  }
  return Array.from(map, ([label, items]) => ({ label, items })).sort(
    (a, b) => orderOf(a.label) - orderOf(b.label),
  )
})

function orderOf(label) {
  const idx = PERMISSION_ORDER.indexOf(label)
  return idx < 0 ? PERMISSION_ORDER.length : idx
}

const dirty = computed(() => {
  if (!selected.value) return false
  const current = new Set(selected.value.permissions || [])
  if (current.size !== draft.value.length) return true
  return draft.value.some((c) => !current.has(c))
})

function flash(message) {
  notice.value = message
  setTimeout(() => {
    if (notice.value === message) notice.value = ''
  }, 3200)
}

async function load(keepSelection = false) {
  loading.value = true
  error.value = ''
  try {
    const [roleRes, permRes] = await Promise.all([
      api.get('/api/v1/admin/roles'),
      api.get('/api/v1/admin/roles/permissions'),
    ])
    roles.value = roleRes?.roles || []
    permissions.value = permRes?.permissions || []
    if (!keepSelection || !roles.value.some((r) => r.code === selectedCode.value)) {
      selectedCode.value = roles.value[0]?.code || ''
    }
    selectRole(selectedCode.value)
  } catch (e) {
    error.value = e.message || '角色数据加载失败'
  } finally {
    loading.value = false
  }
}

function selectRole(code) {
  selectedCode.value = code
  const role = roles.value.find((r) => r.code === code)
  draft.value = [...(role?.permissions || [])]
}

function toggle(code) {
  if (!editable.value) return
  const idx = draft.value.indexOf(code)
  if (idx >= 0) draft.value.splice(idx, 1)
  else draft.value.push(code)
}

function toggleModule(items) {
  if (!editable.value) return
  const codes = items.map((i) => i.code)
  const allOn = codes.every((c) => draft.value.includes(c))
  if (allOn) draft.value = draft.value.filter((c) => !codes.includes(c))
  else draft.value = Array.from(new Set([...draft.value, ...codes]))
}

function moduleState(items) {
  const on = items.filter((i) => draft.value.includes(i.code)).length
  if (on === 0) return 'none'
  if (on === items.length) return 'all'
  return 'part'
}

async function save() {
  if (!editable.value) return
  saving.value = true
  error.value = ''
  try {
    await api.put(`/api/v1/admin/roles/${selectedCode.value}/permissions`, {
      permissionCodes: draft.value,
    })
    flash(`已保存「${selected.value.name}」的权限，用户下次登录生效`)
    await load(true)
  } catch (e) {
    error.value = e.message || '保存失败'
  } finally {
    saving.value = false
  }
}

function openCreate(copyFrom = null) {
  createError.value = ''
  createForm.code = copyFrom ? `${copyFrom.code}_copy` : ''
  createForm.name = copyFrom ? `${copyFrom.name}（副本）` : ''
  createForm.scope = copyFrom?.scope || 'project'
  createForm.permissions = copyFrom ? [...(copyFrom.permissions || [])] : []
  createOpen.value = true
}

async function submitCreate() {
  createError.value = ''
  if (!/^[a-z][a-z0-9_]{2,40}$/.test(createForm.code)) {
    createError.value = '角色 code 只能是小写字母开头，含小写字母/数字/下划线，3–41 位'
    return
  }
  if (!createForm.name.trim()) {
    createError.value = '角色名称不能为空'
    return
  }
  saving.value = true
  try {
    await api.post('/api/v1/admin/roles', {
      code: createForm.code,
      name: createForm.name.trim(),
      scope: createForm.scope,
      permissionCodes: createForm.permissions,
    })
    createOpen.value = false
    flash(`已创建角色 ${createForm.code}`)
    selectedCode.value = createForm.code
    await load(true)
    selectRole(createForm.code)
  } catch (e) {
    createError.value = e.message || '创建失败'
  } finally {
    saving.value = false
  }
}

function toggleCreatePerm(code) {
  const idx = createForm.permissions.indexOf(code)
  if (idx >= 0) createForm.permissions.splice(idx, 1)
  else createForm.permissions.push(code)
}

onMounted(() => load())
</script>

<template>
  <div class="page">
    <PageHeader title="角色与权限" subtitle="内置角色只读；自定义角色可自由组合权限，保存后写审计。">
      <template #actions>
        <button class="btn" :disabled="loading" @click="load(true)">
          <Icon name="refresh" :size="14" />
          刷新
        </button>
        <button class="btn btn-primary" @click="openCreate()">
          <Icon name="plus" :size="14" />
          新建角色
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

    <div class="role-layout">
      <!-- 角色列表 -->
      <section class="card">
        <div class="card-hd"><span class="card-title">角色</span><span class="badge badge-gray">{{ roles.length }}</span></div>
        <div class="card-bd flush">
          <div v-if="roles.length" class="role-list">
            <button
              v-for="r in roles"
              :key="r.code"
              class="role-item"
              :class="{ 'is-active': r.code === selectedCode }"
              @click="selectRole(r.code)"
            >
              <span class="role-item-main">
                <span class="role-name">
                  {{ r.name }}
                  <span v-if="r.builtin" class="badge badge-gray">内置</span>
                </span>
                <span class="role-code mono">{{ r.code }}</span>
              </span>
              <span class="role-count">{{ (r.permissions || []).length }}</span>
            </button>
          </div>
          <EmptyState v-else icon="shield" title="暂无角色" />
        </div>
      </section>

      <!-- 权限矩阵 -->
      <section class="card">
        <div class="card-hd">
          <span class="card-title">
            {{ selected?.name || '权限矩阵' }}
            <span v-if="selected" class="badge badge-gray mono">{{ selected.code }}</span>
            <span v-if="selected && !editable" class="badge badge-amber">只读</span>
          </span>
          <div class="card-actions">
            <button v-if="selected" class="btn btn-sm" @click="openCreate(selected)">
              复制为自定义角色
            </button>
            <button
              v-if="editable"
              class="btn btn-sm btn-primary"
              :disabled="saving || !dirty"
              @click="save"
            >
              {{ saving ? '保存中…' : dirty ? '保存权限' : '已保存' }}
            </button>
          </div>
        </div>
        <div class="card-bd">
          <p v-if="selected && !editable" class="alert alert-warn" style="margin-bottom: 12px">
            <Icon name="lock" :size="14" />
            <span>内置角色由系统维护，不可直接修改；如需调整，请「复制为自定义角色」后修改副本。</span>
          </p>
          <div v-if="!selected" class="empty">
            <p class="empty-title">请选择左侧角色</p>
          </div>
          <div v-else class="matrix">
            <div v-for="m in modules" :key="m.label" class="matrix-module">
              <div class="matrix-module-hd">
                <span class="matrix-module-name">{{ m.label }}</span>
                <button
                  v-if="editable"
                  class="link-btn small"
                  @click="toggleModule(m.items)"
                >
                  {{ moduleState(m.items) === 'all' ? '全不选' : '全选' }}
                </button>
                <span v-else class="small muted">
                  {{ m.items.filter((i) => draft.includes(i.code)).length }}/{{ m.items.length }}
                </span>
              </div>
              <div class="matrix-items">
                <label
                  v-for="p in m.items"
                  :key="p.code"
                  class="perm-check"
                  :class="{ 'is-off': !draft.includes(p.code) }"
                >
                  <input
                    type="checkbox"
                    :checked="draft.includes(p.code)"
                    :disabled="!editable"
                    @change="toggle(p.code)"
                  />
                  <span class="perm-name">{{ p.name }}</span>
                  <span class="perm-code mono">{{ p.code }}</span>
                </label>
              </div>
            </div>
          </div>
        </div>
      </section>
    </div>

    <!-- 新建角色 -->
    <AppModal :open="createOpen" title="新建角色" wide @close="createOpen = false">
      <div class="field-row">
        <div class="field">
          <label>角色 code<span class="required">*</span></label>
          <input v-model="createForm.code" class="input mono" placeholder="例如 regional_analyst" />
          <p class="hint">小写字母开头，创建后不可修改。</p>
        </div>
        <div class="field">
          <label>角色名称<span class="required">*</span></label>
          <input v-model="createForm.name" class="input" placeholder="例如 区域分析员" />
        </div>
        <div class="field">
          <label>作用域</label>
          <select v-model="createForm.scope" class="select">
            <option value="project">项目级</option>
            <option value="org">组织级</option>
            <option value="system">系统级</option>
          </select>
        </div>
      </div>
      <div class="field">
        <label>权限（已选 {{ createForm.permissions.length }} / {{ permissions.length }}）</label>
        <div class="create-perms">
          <div v-for="m in modules" :key="m.label" class="create-perm-group">
            <p class="small secondary" style="margin-bottom: 4px">{{ m.label }}</p>
            <label v-for="p in m.items" :key="p.code" class="checkbox">
              <input
                type="checkbox"
                :checked="createForm.permissions.includes(p.code)"
                @change="toggleCreatePerm(p.code)"
              />
              <span class="small">{{ p.name }}</span>
            </label>
          </div>
        </div>
      </div>
      <p v-if="createError" class="alert alert-error">{{ createError }}</p>
      <template #footer>
        <button class="btn" :disabled="saving" @click="createOpen = false">取消</button>
        <button class="btn btn-primary" :disabled="saving" @click="submitCreate">
          {{ saving ? '创建中…' : '创建角色' }}
        </button>
      </template>
    </AppModal>
  </div>
</template>

<style scoped>
.role-layout {
  display: grid;
  grid-template-columns: 268px minmax(0, 1fr);
  gap: 12px;
  align-items: start;
}
.role-list {
  display: flex;
  flex-direction: column;
  padding: 6px;
}
.role-item {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 8px 9px;
  border: 0;
  border-radius: var(--r-md);
  background: transparent;
  text-align: left;
  cursor: pointer;
}
.role-item:hover {
  background: var(--bg-hover);
}
.role-item.is-active {
  background: var(--accent-soft);
}
.role-item-main {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
  flex: 1;
}
.role-name {
  font-size: var(--fs-md);
  font-weight: 600;
  display: flex;
  align-items: center;
  gap: 5px;
  color: var(--text-primary);
}
.role-item.is-active .role-name {
  color: var(--accent);
}
.role-code {
  font-size: var(--fs-xs);
  color: var(--text-muted);
}
.role-count {
  font-family: var(--mono);
  font-size: var(--fs-sm);
  color: var(--text-muted);
}

.matrix {
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.matrix-module-hd {
  display: flex;
  align-items: center;
  gap: 8px;
  padding-bottom: 6px;
  border-bottom: 1px solid var(--border);
  margin-bottom: 8px;
}
.matrix-module-name {
  font-size: var(--fs-md);
  font-weight: 650;
}
.matrix-module-hd .link-btn {
  margin-left: auto;
}
.matrix-items {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(210px, 1fr));
  gap: 6px 14px;
}
.perm-check {
  display: flex;
  align-items: center;
  gap: 7px;
  font-size: var(--fs-md);
  cursor: pointer;
  padding: 3px 0;
}
.perm-check input {
  accent-color: var(--accent);
}
.perm-check.is-off .perm-name {
  color: var(--text-muted);
}
.perm-name {
  color: var(--text-primary);
}
.perm-code {
  margin-left: auto;
  font-size: var(--fs-xs);
  color: var(--text-muted);
}
.perm-check input:disabled {
  cursor: not-allowed;
}

.create-perms {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
  gap: 10px;
  max-height: 260px;
  overflow-y: auto;
  padding: 10px;
  border: 1px solid var(--border);
  border-radius: var(--r-md);
  background: var(--bg-inset);
}
.create-perm-group .checkbox {
  display: flex;
}

@media (max-width: 1080px) {
  .role-layout {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
