<!-- 管理端 · 任务与模型（接口待接入） -->
<script setup>
import PendingPage from './PendingPage.vue'

const columns = [
  { label: '#', width: '7%' },
  { label: '类型', width: '11%' },
  { label: '项目', width: '15%' },
  { label: '用户', width: '11%' },
  { label: '状态', width: '10%' },
  { label: '进度', width: '12%' },
  { label: '耗时', width: '10%' },
  { label: 'Worker', width: '12%' },
  { label: '操作', width: '12%' },
]

const capabilities = [
  { icon: 'jobs', title: '任务队列', desc: '按状态/项目/用户筛选，支持取消与重试（生成新 job）。' },
  { icon: 'cpu', title: 'Worker / GPU', desc: '主机、GPU、显存、心跳与当前任务。' },
  { icon: 'settings', title: '模型配置', desc: '模型名/提供方/参数/默认开关与启用状态。' },
  { icon: 'alert', title: '失败诊断', desc: '错误摘要、日志与 request_id 关联排查。' },
]
</script>

<template>
  <PendingPage
    title="任务与模型"
    subtitle="任务队列、Worker/GPU 资源与模型配置。"
    action-label="调整并发"
    api-path="GET /api/v1/admin/jobs、POST /api/v1/admin/jobs/{id}/retry"
    description="管理端任务接口尚未接入。Celery + Redis 队列落地后，任务与 Worker 状态由管理端统一观测。"
    :columns="columns"
    :capabilities="capabilities"
    :tabs="['任务队列', 'Worker / GPU', '模型配置']"
  />
</template>
