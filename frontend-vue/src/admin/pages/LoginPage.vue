<!-- 管理端登录页：共用 AuthPage，管理端文案 + 强制 MFA 提示 -->
<script setup>
import { useRouter } from 'vue-router'
import AuthPage from '@/shared/auth/AuthPage.vue'
import { useAdminAuthStore } from '@/shared/auth/authStore'

const auth = useAdminAuthStore()
const router = useRouter()

function onAuthenticated() {
  const redirect = router.currentRoute.value.query.redirect
  router.replace(
    typeof redirect === 'string' && redirect.startsWith('/admin') ? redirect : '/admin/overview',
  )
}
</script>

<template>
  <AuthPage portal="admin" :store="auth" @authenticated="onAuthenticated" />
</template>
