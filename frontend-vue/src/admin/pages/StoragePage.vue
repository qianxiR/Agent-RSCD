<!-- 管理端 · 存储与配额（接口待接入） -->
<script setup>
import PendingPage from './PendingPage.vue'

const columns = [
  { label: '范围', width: '20%' },
  { label: '已用', width: '12%' },
  { label: '配额', width: '12%' },
  { label: '使用率', width: '14%' },
  { label: '对象数', width: '12%' },
  { label: '生命周期', width: '14%' },
  { label: '操作', width: '16%' },
]

const capabilities = [
  { icon: 'storage', title: '容量总览', desc: '总容量/已用/增长趋势，按组织与项目 Top 列表。' },
  { icon: 'settings', title: '配额编辑', desc: '软限制提醒、硬限制禁止上传，变更写审计。' },
  { icon: 'trash', title: '清理任务', desc: '孤儿文件与回收站清理异步执行，完成后通知。' },
  { icon: 'clock', title: '生命周期', desc: '按桶/前缀设置转冷归档与过期删除策略。' },
]
</script>

<template>
  <PendingPage
    title="存储与配额"
    subtitle="OSS 容量、项目配额与清理任务。"
    action-label="调整配额"
    api-path="GET /api/v1/admin/storage、PUT /api/v1/admin/storage/quota"
    description="存储接口尚未接入。当前产物仍落本地磁盘，接入云 OSS 后由管理端统一统计与配额。"
    :columns="columns"
    :capabilities="capabilities"
  />
</template>
