<!-- ==================== 工作台路由视图 ====================
     现有三栏工作台（会话 + 地图 + Copilot）的挂载点。
     WS 生命周期放在这里：进入工作台连接，转到管理端时断开。
-->
<script setup>
import { onBeforeUnmount, onMounted } from 'vue'
import { useWebSocket } from '@/composables/useWebSocket'
import MainLayout from '@/layouts/MainLayout.vue'

const ws = useWebSocket()

onMounted(() => ws.connect())
onBeforeUnmount(() => ws.disconnect())
</script>

<template>
  <MainLayout />
</template>
