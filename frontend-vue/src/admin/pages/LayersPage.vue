<!-- 管理端 · 图层服务（接口待接入） -->
<script setup>
import PendingPage from './PendingPage.vue'

const columns = [
  { label: '图层', width: '26%' },
  { label: '工作空间', width: '14%' },
  { label: '类型', width: '10%' },
  { label: 'bbox', width: '22%' },
  { label: '样式', width: '12%' },
  { label: '引用项目', width: '14%' },
  { label: '状态', width: '10%' },
]

const capabilities = [
  { icon: 'layers', title: 'GeoServer 健康', desc: '版本、响应时间、工作空间数与图层数。' },
  { icon: 'refresh', title: '重发布与换样式', desc: '发布失败可重试；样式可切换与回滚。' },
  { icon: 'trash', title: '批量清理', desc: '删除需二次确认；先检查引用项目后执行。' },
  { icon: 'alert', title: '失败任务列表', desc: '发布失败原因、重试次数与错误堆栈摘要。' },
]
</script>

<template>
  <PendingPage
    title="图层服务"
    subtitle="GeoServer 工作空间、图层与发布任务治理。"
    action-label="刷新健康状态"
    api-path="GET /api/v1/admin/layers/health、DELETE /api/v1/admin/layers/{workspace}/{layer}"
    description="图层服务接口尚未接入。现有工作台已提供图层管理与发布（LayerPanel / GeoServer 端点），管理端需要全库健康检查、重发布与批量清理能力。"
    :columns="columns"
    :capabilities="capabilities"
  />
</template>
