<script setup>
// ==================== 登录入口 (Java 业务服务 JWT) ====================
// ★ 一阶段 (security.mode=off) 登录可选：打通「登录 → token 持久化 →
//   REST 自动带 Bearer 头 / WS 连接带 ?token=」链路，未登录不影响使用。
//   权限语义 (接口拦截、按登录用户隔离数据) 在 enforce 阶段引入。
import { computed, ref } from 'vue'
import { login, logout, getAuthUser } from '@/services/api'

const emit = defineEmits(['auth-changed'])

const visible = ref(false)
const username = ref('')
const password = ref('')
const errorMsg = ref('')
const submitting = ref(false)
const user = ref(getAuthUser())

const isLoggedIn = computed(() => !!user.value)

// 入参: 无（读取表单输入）
// 方法: 调 login，成功后关闭对话框并通知父级刷新；失败显示后端 msg
// 出参: 无（副作用: localStorage 写 token，user 状态更新）
async function handleLogin() {
  if (!username.value || !password.value) {
    errorMsg.value = '请输入用户名和口令'
    return
  }
  submitting.value = true
  errorMsg.value = ''
  try {
    const data = await login(username.value, password.value)
    if (data.status === 'success') {
      user.value = getAuthUser()
      visible.value = false
      password.value = ''
      emit('auth-changed', user.value)
    } else {
      errorMsg.value = data.msg || '登录失败'
    }
  } catch (e) {
    errorMsg.value = `登录请求失败: ${e.message}`
  } finally {
    submitting.value = false
  }
}

// 入参: 无
// 方法: 清除本地 token 并通知父级
// 出参: 无
function handleLogout() {
  logout()
  user.value = null
  emit('auth-changed', null)
}
</script>

<template>
  <div class="auth-entry">
    <template v-if="isLoggedIn">
      <span class="auth-user">{{ user.username }}</span>
      <el-button link size="small" @click="handleLogout">退出</el-button>
    </template>
    <el-button v-else link size="small" @click="visible = true">登录</el-button>

    <el-dialog v-model="visible" title="登录" width="340px" append-to-body>
      <el-form label-position="top" @submit.prevent>
        <el-form-item label="用户名">
          <el-input v-model="username" placeholder="admin" autocomplete="username" />
        </el-form-item>
        <el-form-item label="口令">
          <el-input v-model="password" type="password" show-password autocomplete="current-password"
                    @keyup.enter="handleLogin" />
        </el-form-item>
        <div v-if="errorMsg" class="auth-error">{{ errorMsg }}</div>
      </el-form>
      <template #footer>
        <el-button @click="visible = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="handleLogin">登录</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.auth-entry {
  display: flex;
  align-items: center;
  gap: 4px;
}
.auth-user {
  color: var(--text-secondary);
  font-size: var(--fs-md);
}
.auth-error {
  color: var(--accent-red, #d03050);
  font-size: var(--fs-md);
  margin-bottom: 8px;
}
</style>
