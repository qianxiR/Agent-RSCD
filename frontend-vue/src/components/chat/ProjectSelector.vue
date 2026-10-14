<script setup>
// ==================== 对话窗口上方的项目选择器 ====================
// ★ 解决「对话与项目割裂」: 在聊天面板顶部直接显示/切换当前对话的归属项目。
//   切换语义:
//   - 已落库会话 (有消息, 后端已建会话) → PATCH conversation 的 project_id
//     (空串 = 移出项目), 同步左侧栏分组;
//   - 未落库新对话 (尚无消息) → 仅更新激活项目, 首条消息发送时按此归属;
//   - 无激活对话 → 同上 (决定下一个新建对话的归属)。
import { computed } from 'vue'
import { storeToRefs } from 'pinia'
import { ElMessage } from 'element-plus'
import { Folder } from '@element-plus/icons-vue'
import { useProjectStore } from '@/stores/project'
import { useConversationStore } from '@/stores/conversation'
import { patchConversation } from '@/services/api'

const projectStore = useProjectStore()
const convStore = useConversationStore()
const { projects, convList, activeProjectId } = storeToRefs(projectStore)
const { activeConvId, active } = storeToRefs(convStore)

// 当前选择器展示的项目 id: 优先取当前会话摘要的归属, 其次激活项目
const currentProjectId = computed(() => {
  if (activeConvId.value) {
    const summary = convList.value.find((c) => c.id === activeConvId.value)
    if (summary) return summary.project_id || ''
  }
  return activeProjectId.value || ''
})

const currentProjectName = computed(() => {
  if (!currentProjectId.value) return ''
  return projects.value.find((p) => p.id === currentProjectId.value)?.name || ''
})

// 入参: pid 目标项目 id, 空串表示移出项目
// 方法: 按会话落库状态分流 (落库 → PATCH 后端; 未落库 → 仅本地激活项目)
// 出参: 无
async function onChange(pid) {
  const convId = activeConvId.value
  const hasMessages = !!(active.value && active.value.messages && active.value.messages.length)
  if (convId && hasMessages) {
    // 已落库: 移动会话归属 (后端 PATCH, 空串 = 移出项目)
    try {
      const res = await patchConversation(convId, { project_id: pid })
      if (res.status !== 'success') throw new Error(res.msg || '后端返回失败')
      projectStore.upsertConv({ id: convId, project_id: pid || null })
      ElMessage.success(pid ? '已移动到项目' : '已移出项目')
    } catch (e) {
      ElMessage.error('项目归属修改失败: ' + e.message)
    }
  } else {
    // 未落库/无激活对话: 只影响下一个新建对话的归属
    projectStore.setActiveProject(pid || null)
    if (convId) projectStore.upsertConv({ id: convId, project_id: pid || null })
  }
}
</script>

<template>
  <div class="conv-context-bar">
    <el-select
      :model-value="currentProjectId"
      size="small"
      class="project-select"
      @change="onChange"
    >
      <template #prefix>
        <el-icon class="project-select-icon"><Folder /></el-icon>
      </template>
      <el-option label="未分组 · 不属于任何项目" value="" />
      <el-option v-for="p in projects" :key="p.id" :label="p.name" :value="p.id" />
    </el-select>
    <span v-if="currentProjectName" class="ctx-project-name">{{ currentProjectName }}</span>
    <span v-else class="ctx-project-name muted">未关联项目</span>
  </div>
</template>

<style scoped>
.conv-context-bar {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 14px;
  background: var(--bg-panel);
  border-bottom: 1px solid var(--border);
}
.project-select {
  width: 240px;
}
.project-select-icon {
  color: var(--text-secondary);
}
.ctx-project-name {
  font-size: var(--fs-md);
  color: var(--text-secondary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.ctx-project-name.muted {
  color: var(--text-muted);
}
</style>
