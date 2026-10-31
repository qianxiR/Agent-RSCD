<!-- 管理端 · 待接入页通用骨架
     用途: 信息架构先固定（标题/操作位/表格列/接入计划），等后端接口就绪后替换为真实数据。
     边界: 不伪造数据；所有数值位显示「—」或空态，并明确标注待接入的 API 路径。
-->
<script setup>
import Icon from '@/shared/ui/Icon.vue'
import PageHeader from '@/shared/ui/PageHeader.vue'
import EmptyState from '@/shared/ui/EmptyState.vue'

defineProps({
  title: { type: String, required: true },
  subtitle: { type: String, default: '' },
  actionLabel: { type: String, default: '' },
  apiPath: { type: String, default: '' },
  description: { type: String, default: '' },
  // [{ icon, title, desc }]
  capabilities: { type: Array, default: () => [] },
  // [{ label, width }]
  columns: { type: Array, default: () => [] },
  // 规划中的页内分区，例如 ['任务队列', 'Worker / GPU', '模型配置']
  tabs: { type: Array, default: () => [] },
})
</script>

<template>
  <div class="page">
    <PageHeader :title="title" :subtitle="subtitle">
      <template #actions>
        <span class="badge badge-gray">P1 接入</span>
        <button v-if="actionLabel" class="btn btn-primary" disabled :title="`${actionLabel}将在 P1 开放`">
          <Icon name="plus" :size="14" />
          {{ actionLabel }}
        </button>
      </template>
    </PageHeader>

    <div v-if="tabs.length" class="segmented" style="margin-bottom: 12px">
      <button v-for="(t, i) in tabs" :key="t" class="seg" :class="{ 'is-active': i === 0 }" disabled>
        {{ t }}
      </button>
    </div>

    <p class="alert alert-info" style="margin-bottom: 12px">
      <Icon name="alert" :size="14" />
      <span>
        {{ description || '该模块的接口尚未接入，本页先固定信息架构与交互位置。' }}
        <template v-if="apiPath">
          计划接口：<code class="mono">{{ apiPath }}</code>
        </template>
      </span>
    </p>

    <section class="card">
      <div class="card-hd">
        <span class="card-title">列表</span>
        <div class="card-actions"><span class="small muted">等待接口接入</span></div>
      </div>
      <div class="card-bd flush">
        <div class="table-wrap">
          <table class="table">
            <thead>
              <tr>
                <th v-for="c in columns" :key="c.label" :style="c.width ? { width: c.width } : null">
                  {{ c.label }}
                </th>
              </tr>
            </thead>
          </table>
        </div>
        <EmptyState
          icon="folder"
          title="暂无数据"
          description="接口接入后，这里将以高密度表格展示全系统数据，支持筛选、批量操作与下钻。"
        />
      </div>
    </section>

    <section v-if="capabilities.length" class="card">
      <div class="card-hd"><span class="card-title">接入能力</span></div>
      <div class="card-bd">
        <div class="cap-grid">
          <div v-for="c in capabilities" :key="c.title" class="cap">
            <span class="cap-icon"><Icon :name="c.icon" :size="16" /></span>
            <div>
              <p class="cap-title">{{ c.title }}</p>
              <p class="hint">{{ c.desc }}</p>
            </div>
          </div>
        </div>
      </div>
    </section>
  </div>
</template>

<style scoped>
.cap-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(248px, 1fr));
  gap: 12px;
}
.cap {
  display: flex;
  gap: 10px;
  padding: 12px;
  border: 1px dashed var(--border);
  border-radius: var(--r-md);
  background: var(--bg-inset);
}
.cap-icon {
  width: 30px;
  height: 30px;
  border-radius: var(--r-md);
  background: var(--bg-panel);
  border: 1px solid var(--border);
  color: var(--accent);
  display: grid;
  place-items: center;
  flex: 0 0 auto;
}
.cap-title {
  font-size: var(--fs-lg);
  font-weight: 600;
  margin-bottom: 3px;
}
</style>
