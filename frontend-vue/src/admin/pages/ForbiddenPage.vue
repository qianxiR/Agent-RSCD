<!-- 管理端 403：账号已通过认证，但缺少管理权限 -->
<script setup>
import { useRouter } from 'vue-router'
import { useAdminAuthStore } from '@/shared/auth/authStore'

const auth = useAdminAuthStore()
const router = useRouter()

function logout() {
  auth.logout().then(() => router.replace('/admin/login'))
}
</script>

<template>
  <div class="state-page">
    <p class="state-code">403</p>
    <p class="state-title">当前账号没有管理控制台权限</p>
    <p class="state-desc">
      管理端仅对管理员开放。当前账号
      <strong>{{ auth.displayName }}</strong>
      未分配任何管理类角色或权限；如需访问，请联系系统管理员。
    </p>
    <div class="row" style="margin-top: 4px">
      <button class="btn btn-primary" @click="logout">退出并更换账号</button>
      <a class="btn" href="/">前往用户端工作台</a>
    </div>
  </div>
</template>
