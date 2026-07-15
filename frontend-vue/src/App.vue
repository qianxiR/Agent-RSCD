<script setup>
// ==================== 根组件 ====================
// ★ 仅负责 WS 连接 + 渲染 MainLayout
//   原 P1 测试页逻辑已拆入 ChatPanel / ConvSidebar，此文件回归根职责
import { onMounted } from 'vue'
import { useWebSocket } from '@/composables/useWebSocket'
import MainLayout from '@/layouts/MainLayout.vue'

const ws = useWebSocket()
onMounted(() => ws.connect())
</script>

<template>
  <MainLayout />
</template>