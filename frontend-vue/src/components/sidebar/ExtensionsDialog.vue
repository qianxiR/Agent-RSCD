<script setup>
// ==================== 扩展弹窗：专家 / 技能 / 连接器 ====================
// 合并原三个侧栏面板，布局对齐影像检索弹窗：左栏分类 + 右侧内容列表。
// 数据源: 技能 /api/v1/skills；连接器 /api/v1/mcp/servers；专家暂无后端。
import { computed, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { Box, Connection, MagicStick, User } from '@element-plus/icons-vue'
import PanelDialog from '@/shared/ui/PanelDialog.vue'
import { getMcpServers, getSkills } from '@/services/api'

const open = defineModel('open', { type: Boolean, default: false })

const tabs = [
  { key: 'experts', label: '专家', icon: User, empty: '暂无专家' },
  { key: 'skills', label: '技能', icon: MagicStick, empty: '暂无技能' },
  { key: 'connectors', label: '连接器', icon: Box, empty: '暂无连接器' },
]

const active = ref('experts')
const skills = ref([])
const servers = ref([])
const loading = ref(false)
const loadedTabs = ref([])

const activeTab = computed(() => tabs.find((t) => t.key === active.value) || tabs[0])

const counts = computed(() => ({
  experts: 0,
  skills: skills.value.length,
  connectors: servers.value.length,
}))

const currentCount = computed(() => counts.value[active.value])

watch(open, (value) => {
  if (value) loadTab(active.value)
})

watch(active, (key) => {
  if (open.value) loadTab(key)
})

async function loadTab(key) {
  if (key === 'experts' || loadedTabs.value.includes(key)) return
  loading.value = true
  try {
    if (key === 'skills') {
      const data = await getSkills()
      if (data.status !== 'success') throw new Error(data.msg || '加载失败')
      skills.value = data.skills || []
    } else if (key === 'connectors') {
      const data = await getMcpServers()
      if (data.status !== 'success') throw new Error(data.msg || '加载失败')
      servers.value = data.servers || []
    }
    loadedTabs.value = [...loadedTabs.value, key]
  } catch (e) {
    ElMessage.error('加载失败: ' + e.message)
  } finally {
    loading.value = false
  }
}

function reload() {
  loadedTabs.value = loadedTabs.value.filter((k) => k !== active.value)
  loadTab(active.value)
}
</script>

<template>
  <PanelDialog
    v-model:open="open"
    title="专家 · 技能 · 连接器"
    :count="currentCount"
    @refresh="reload"
  >
    <template #icon>
      <el-icon><MagicStick /></el-icon>
    </template>

    <template #side>
      <button
        v-for="tab in tabs"
        :key="tab.key"
        type="button"
        class="pnl-side-item"
        :class="{ active: active === tab.key }"
        @click="active = tab.key"
      >
        <el-icon><component :is="tab.icon" /></el-icon>
        <span>{{ tab.label }}</span>
        <em>{{ counts[tab.key] }}</em>
      </button>
    </template>

    <template #toolbar>{{ activeTab.label }}</template>

    <div v-if="loading" class="pnl-empty">加载中…</div>
    <div v-else-if="active === 'experts'" class="pnl-empty">暂无专家</div>
    <div v-else-if="!currentCount" class="pnl-empty">{{ activeTab.empty }}</div>

    <template v-else>
      <div v-if="active === 'skills'" class="pnl-grid">
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
        </div>
      </div>

      <div v-else class="pnl-grid">
        <div v-for="s in servers" :key="s.id" class="mcp-card" :class="{ disabled: !s.enabled }">
          <div class="mcp-card-head">
            <el-icon class="mcp-card-icon"><Connection /></el-icon>
            <span class="mcp-card-title">{{ s.name }}</span>
            <span class="skill-tag">{{ s.transport.toUpperCase() }}</span>
            <span class="mcp-status" :class="s.enabled ? 'on' : 'off'">
              {{ s.enabled ? '已配置' : '已停用' }}
            </span>
          </div>
          <div class="mcp-card-meta">
            <span class="mcp-endpoint">{{ s.transport === 'sse' ? s.url : s.command }}</span>
          </div>
          <div v-if="s.description" class="mcp-card-desc">{{ s.description }}</div>
        </div>
      </div>
    </template>

    <template #hint>
      专家与连接器的注册与接入能力尚未开放，当前仅展示。
    </template>
  </PanelDialog>
</template>

<style scoped>
.pnl-side-item {
  display: flex;
  align-items: center;
  gap: 8px;
  height: 32px;
  padding: 0 10px;
  border: none;
  border-radius: var(--r-md);
  background: transparent;
  color: var(--text-secondary);
  font-size: var(--fs-lg);
  cursor: pointer;
  text-align: left;
  width: 100%;
}
.pnl-side-item:hover {
  background: var(--bg-hover);
}
.pnl-side-item.active {
  background: #ececec;
  color: var(--text-primary);
  font-weight: var(--fw-medium);
}
.pnl-side-item em {
  margin-left: auto;
  font-style: normal;
  font-size: var(--fs-xs);
  color: var(--text-muted);
}
.pnl-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}
.pnl-empty {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 240px;
  font-size: var(--fs-md);
  color: var(--text-muted);
}
.skill-card {
  border: 1px solid var(--border);
  border-radius: var(--r-lg);
  padding: 10px 12px;
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
  font-weight: var(--fw-strong);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.skill-tag.ok {
  flex-shrink: 0;
  font-size: var(--fs-xs);
  padding: 1px 6px;
  border-radius: var(--r-sm);
  color: var(--accent-green);
  background: rgba(63, 174, 90, 0.10);
}
.skill-tag.bad {
  flex-shrink: 0;
  font-size: var(--fs-xs);
  padding: 1px 6px;
  border-radius: var(--r-sm);
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
.mcp-card {
  border: 1px solid var(--border);
  border-radius: var(--r-lg);
  padding: 10px 12px;
  background: var(--bg-base);
}
.mcp-card.disabled {
  opacity: 0.65;
}
.mcp-card-head {
  display: flex;
  align-items: center;
  gap: 6px;
}
.mcp-card-icon {
  color: var(--accent);
  flex-shrink: 0;
}
.mcp-card-title {
  flex: 1;
  min-width: 0;
  font-size: var(--fs-lg);
  font-weight: var(--fw-strong);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.mcp-status {
  flex-shrink: 0;
  font-size: var(--fs-xs);
}
.mcp-status.on {
  color: var(--accent-green);
}
.mcp-status.off {
  color: var(--text-muted);
}
.mcp-endpoint {
  font-family: var(--mono);
  font-size: var(--fs-xs);
  color: var(--text-muted);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.mcp-card-desc {
  margin-top: 6px;
  font-size: var(--fs-md);
  color: var(--text-secondary);
  line-height: 1.5;
}
.skill-tag {
  flex-shrink: 0;
  font-size: var(--fs-xs);
  color: var(--text-secondary);
  background: var(--bg-inset);
  padding: 1px 6px;
  border-radius: var(--r-sm);
}
</style>
