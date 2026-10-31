<!-- 管理端 · 数据治理（接口待接入） -->
<script setup>
import PendingPage from './PendingPage.vue'

const columns = [
  { label: '数据集', width: '24%' },
  { label: '类型', width: '10%' },
  { label: '项目', width: '16%' },
  { label: '坐标系', width: '12%' },
  { label: '大小', width: '10%' },
  { label: '质检', width: '12%' },
  { label: '来源', width: '12%' },
  { label: '状态', width: '8%' },
]

const capabilities = [
  { icon: 'alert', title: '质量筛选', desc: '缺 CRS、空文件、重复 checksum、长期未使用、孤儿文件。' },
  { icon: 'refresh', title: '重新解析', desc: '修复元数据、重新解析 CRS/范围/缩略图。' },
  { icon: 'storage', title: '存储迁移', desc: '本地 → OSS 迁移任务化，完成后写审计。' },
  { icon: 'clock', title: '保留策略', desc: '按项目/类型设置保留天数，到期进入回收站。' },
]
</script>

<template>
  <PendingPage
    title="数据治理"
    subtitle="全量数据集、质检状态与保留策略。"
    action-label="导出清单"
    api-path="GET /api/v1/admin/datasets、POST /api/v1/admin/datasets/{id}/reparse"
    description="数据治理接口尚未接入；当前数据文件仍落在本地 agent-files 目录，尚无统一 datasets 表与质检字段。"
    :columns="columns"
    :capabilities="capabilities"
  />
</template>
