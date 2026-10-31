<!-- ==================== 管理端外壳 ====================
     布局: 56px 顶栏（品牌 + 生产环境标识 + 账号） + 212px 白色菜单栏 + 内容区。
     边界: 菜单按权限过滤（体验层），后端 /api/v1/admin/** 仍做方法级校验。
-->
<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { RouterView, useRoute, useRouter } from 'vue-router'
import Icon from '@/shared/ui/Icon.vue'
import { useAdminAuthStore } from '@/shared/auth/authStore'
import { roleLabel } from '@/shared/utils/format'

const auth = useAdminAuthStore()
const route = useRoute()
const router = useRouter()

const menu = ref('')

const GROUPS = [
  {
    label: '运营',
    items: [{ to: '/admin/overview', label: '运营总览', icon: 'overview', permission: 'user:manage' }],
  },
  {
    label: '身份与权限',
    items: [
      { to: '/admin/users', label: '用户与组织', icon: 'users', permission: 'user:manage' },
      { to: '/admin/roles', label: '角色与权限', icon: 'shield', permission: 'role:manage' },
    ],
  },
  {
    label: '业务治理',
    items: [
      { to: '/admin/projects', label: '项目管理', icon: 'folder', permission: 'project:read' },
      { to: '/admin/datasets', label: '数据治理', icon: 'data', permission: 'dataset:read' },
      { to: '/admin/layers', label: '图层服务', icon: 'layers', permission: 'layer:read' },
      { to: '/admin/jobs', label: '任务与模型', icon: 'jobs', permission: 'job:read' },
      { to: '/admin/storage', label: '存储与配额', icon: 'storage', permission: 'storage:manage' },
    ],
  },
  {
    label: '安全与系统',
    items: [
      { to: '/admin/audit', label: '审计日志', icon: 'audit', permission: 'audit:read' },
      { to: '/admin/settings', label: '系统设置', icon: 'settings', permission: 'system:config' },
    ],
  },
]

// 非管理员（例如误用分析员账号进管理端）不展示任何管理菜单，
// 只保留 403 页面本身，避免出现“看得见点不进”的菜单项。
const adminLike = computed(
  () =>
    auth.isAdmin ||
    ['user:manage', 'role:manage', 'audit:read', 'system:config'].some((p) => auth.hasPermission(p)),
)

const groups = computed(() => {
  if (!adminLike.value) return []
  return GROUPS.map((g) => ({
    ...g,
    items: g.items.filter((i) => auth.hasPermission(i.permission)),
  })).filter((g) => g.items.length)
})

const roleText = computed(() => (auth.roles || []).map(roleLabel).join('、') || '未分配角色')

async function logout() {
  menu.value = ''
  await auth.logout()
  router.replace('/admin/login')
}

function onDocumentClick(event) {
  if (!menu.value) return
  if (event.target.closest && event.target.closest('.popover, [data-popover-trigger]')) return
  menu.value = ''
}

onMounted(() => document.addEventListener('click', onDocumentClick))
onBeforeUnmount(() => document.removeEventListener('click', onDocumentClick))
</script>

<template>
  <div class="shell shell--admin">
    <!-- 侧栏 -->
    <aside class="sidebar">
      <nav>
        <div v-for="group in groups" :key="group.label" class="side-group">
          <p class="side-label">{{ group.label }}</p>
          <RouterLink
            v-for="item in group.items"
            :key="item.to"
            :to="item.to"
            class="side-item"
            :class="{ 'is-active': route.path === item.to }"
          >
            <span class="side-icon"><Icon :name="item.icon" :size="16" /></span>
            <span>{{ item.label }}</span>
          </RouterLink>
        </div>
      </nav>
      <div class="sidebar-foot">
        <p>国土智察 · 管理控制台</p>
        <p class="mono" style="font-size: 10px">v0.1 · DEV</p>
      </div>
    </aside>

    <!-- 顶栏 -->
    <header class="topbar">
      <div class="topbar-brand">
        <img src="/image.svg" alt="国土智察" class="brand-logo-img" />
        <span class="brand-text">
          <span class="brand-name">国土智察 · 管理控制台</span>
        </span>
      </div>
      <span class="env-badge">生产环境</span>

      <div class="topbar-spacer"></div>

      <div class="topbar-search" title="全局搜索将在 P1 接入">
        <span class="search-icon"><Icon name="search" :size="14" /></span>
        <input disabled placeholder="搜索用户、项目、任务（P1 接入）" />
      </div>

      <button class="icon-btn" data-popover-trigger aria-label="通知" @click="menu = menu === 'bell' ? '' : 'bell'">
        <Icon name="bell" :size="17" />
        <span class="dot"></span>
      </button>

      <span class="admin-divider"></span>

      <button class="user-menu admin-user" data-popover-trigger @click="menu = menu === 'user' ? '' : 'user'">
        <span class="avatar">{{ auth.displayInitial }}</span>
        <span class="admin-user-text">
          <span class="user-name">{{ auth.displayName }}</span>
          <span class="admin-user-role">{{ roleText }}</span>
        </span>
        <Icon name="chevronDown" :size="13" />
      </button>

      <div v-if="menu === 'bell'" class="popover">
        <div class="popover-meta">
          <strong>告警与通知</strong>
          <span>告警中心将在 P1 接入</span>
        </div>
        <p style="padding: 10px; font-size: var(--fs-sm); color: var(--text-muted)">暂无告警。</p>
      </div>

      <div v-if="menu === 'user'" class="popover">
        <div class="popover-meta">
          <strong>{{ auth.displayName }}</strong>
          <span>{{ roleText }}</span>
        </div>
        <button @click="menu = ''; router.push('/admin/users')">
          <Icon name="users" :size="14" /> 用户管理
        </button>
        <button @click="menu = ''; router.push('/admin/audit')">
          <Icon name="audit" :size="14" /> 我的操作记录
        </button>
        <button @click="logout">
          <Icon name="logout" :size="14" /> 退出登录
        </button>
      </div>
    </header>

    <main class="main">
      <RouterView />
    </main>
  </div>
</template>

<style scoped>
.admin-divider {
  width: 1px;
  height: 20px;
  background: var(--border);
  flex: 0 0 auto;
}
.admin-user {
  gap: 8px;
  padding-right: 8px;
}
.admin-user-text {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  line-height: 1.2;
}
.admin-user-role {
  font-size: var(--fs-xs);
  color: var(--text-muted);
  max-width: 110px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
