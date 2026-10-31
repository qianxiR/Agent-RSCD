<!-- 权限门：无权限时不渲染内容（后端方法级权限仍是最终权威） -->
<script setup>
import { computed } from 'vue'
import { useAdminAuthStore } from '@/shared/auth/authStore'

const props = defineProps({
  // 单个权限码或权限码数组（满足任一即可）
  permission: { type: [String, Array], required: true },
})

const auth = useAdminAuthStore()
const allowed = computed(() => {
  const codes = Array.isArray(props.permission) ? props.permission : [props.permission]
  return auth.hasAnyPermission(codes)
})
</script>

<template>
  <slot v-if="allowed" />
</template>
