<!-- 管理端 · 项目管理（接口待接入） -->
<script setup>
import PendingPage from './PendingPage.vue'

const columns = [
  { label: '项目', width: '26%' },
  { label: '所有者', width: '12%' },
  { label: '组织', width: '14%' },
  { label: '资产', width: '10%' },
  { label: '任务', width: '10%' },
  { label: '存储', width: '10%' },
  { label: '状态', width: '10%' },
  { label: '更新', width: '12%' },
]

const capabilities = [
  { icon: 'eye', title: '只读查看项目', desc: '管理员可查看任意项目的数据、任务与产物，访问行为写审计。' },
  { icon: 'folder', title: '转移与归档', desc: '转移所有者、归档停用、按项目配额调整。' },
  { icon: 'trash', title: '删除与恢复', desc: '软删除保留审计，支持回收站与恢复窗口。' },
  { icon: 'data', title: '资产与任务统计', desc: '项目维度汇总数据集、图层、任务数、存储占用。' },
]
</script>

<template>
  <PendingPage
    title="项目管理"
    subtitle="全系统项目治理：所有者、配额、归档与只读下钻。"
    action-label="新建项目"
    api-path="GET /api/v1/admin/projects、PATCH /api/v1/admin/projects/{id}"
    description="项目管理接口尚未接入。当前项目数据仍由用户端 /api/v1/projects 维护，管理端需要独立的全量只读视图。"
    :columns="columns"
    :capabilities="capabilities"
  />
</template>
