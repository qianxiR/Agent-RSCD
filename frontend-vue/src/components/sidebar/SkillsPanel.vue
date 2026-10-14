<script setup>
// ==================== 技能库面板 ====================
// 数据源: Python /api/v1/skills (model/skills 目录的 Markdown 技能方案)。
// 展示: 名称/版本/契约状态/必填输入/触发关键词, 只读。
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { ChatLineRound, Refresh, MagicStick } from '@element-plus/icons-vue'
import { getSkills } from '@/services/api'

const emit = defineEmits(['back'])
const skills = ref([])
const loading = ref(false)

onMounted(() => load())

async function load() {
  loading.value = true
  try {
    const data = await getSkills()
    if (data.status !== 'success') throw new Error(data.msg || '加载失败')
    skills.value = data.skills || []
  } catch (e) {
    ElMessage.error('技能加载失败: ' + e.message)
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="skills-panel">
    <div class="panel-actions">
      <button type="button" class="panel-action" @click="load">
        <el-icon><Refresh /></el-icon>
        <span>刷新</span>
      </button>
      <button type="button" class="panel-action" @click="emit('back')">
        <el-icon><ChatLineRound /></el-icon>
        <span>对话管理</span>
      </button>
    </div>

    <div class="panel-list">
      <div class="panel-section-header">标准技能</div>
      <div v-if="loading && !skills.length" class="panel-empty">加载中…</div>
      <div v-else-if="!skills.length" class="panel-empty">暂无技能</div>

      <div v-for="s in skills" :key="s.name" class="skill-card">
        <div class="skill-card-head">
          <el-icon class="skill-card-icon"><MagicStick /></el-icon>
          <span class="skill-card-title">{{ s.title || s.name }}</span>
          <span class="skill-tag" :class="s.contract_valid ? 'ok' : 'bad'">
            {{ s.contract_valid ? `契约 v${s.version || '?'}` : '契约缺失' }}
          </span>
        </div>
        <div class="skill-card-name">{{ s.name }}</div>
        <div v-if="s.required_inputs?.length" class="skill-card-row">
          <span class="skill-card-label">必填输入</span>
          <span class="skill-card-value">{{ s.required_inputs.join('、') }}</span>
        </div>
        <div v-if="s.keywords?.length" class="skill-card-row">
          <span class="skill-card-label">触发场景</span>
          <span class="skill-card-value">{{ s.keywords.slice(0, 6).join(' · ') }}</span>
        </div>
      </div>

      <div class="panel-hint">
        技能由 Markdown 方案承载，Agent 在对话中按需检索执行；新增技能放入
        backend/model/skills/ 目录即可被识别。
      </div>
    </div>
  </div>
</template>

<style scoped>
.skills-panel {
  display: flex;
  flex-direction: column;
  height: 100%;
  background: var(--bg-panel);
  overflow: hidden;
}
.panel-actions {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 14px 8px 12px;
  flex-shrink: 0;
}
.panel-action {
  width: 100%;
  height: 32px;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 0 8px;
  border: none;
  border-radius: var(--r-md);
  background: transparent;
  color: #2f3b49;
  cursor: pointer;
  font-size: 13px;
  text-align: left;
}
.panel-action:hover {
  background: var(--bg-hover);
}
.panel-list {
  flex: 1;
  overflow-y: auto;
  padding: 0 10px 10px;
}
.panel-section-header {
  font-size: var(--fs-md);
  color: var(--text-muted);
  padding: 4px 6px 8px;
}
.panel-empty {
  text-align: center;
  font-size: var(--fs-md);
  color: var(--text-muted);
  padding: 32px 0;
}
.skill-card {
  border: 1px solid var(--border);
  border-radius: var(--r-lg);
  padding: 10px 12px;
  margin-bottom: 8px;
  background: var(--bg-base);
}
.skill-card-head {
  display: flex;
  align-items: center;
  gap: 6px;
}
.skill-card-icon {
  color: var(--accent);
  flex-shrink: 0;
}
.skill-card-title {
  flex: 1;
  min-width: 0;
  font-size: var(--fs-lg);
  font-weight: 600;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.skill-tag {
  flex-shrink: 0;
  font-size: var(--fs-xs);
  padding: 1px 6px;
  border-radius: var(--r-sm);
}
.skill-tag.ok {
  color: var(--accent-green);
  background: rgba(63, 174, 90, 0.10);
}
.skill-tag.bad {
  color: var(--accent-red);
  background: rgba(207, 68, 68, 0.10);
}
.skill-card-name {
  font-family: var(--mono);
  font-size: var(--fs-xs);
  color: var(--text-muted);
  margin: 2px 0 6px;
}
.skill-card-row {
  display: flex;
  gap: 8px;
  font-size: var(--fs-md);
  line-height: 1.5;
}
.skill-card-label {
  flex-shrink: 0;
  color: var(--text-muted);
}
.skill-card-value {
  color: var(--text-secondary);
  word-break: break-all;
}
.panel-hint {
  margin-top: 10px;
  padding: 8px 10px;
  border-radius: var(--r-md);
  background: var(--bg-inset);
  color: var(--text-muted);
  font-size: var(--fs-sm);
  line-height: 1.6;
}
</style>
