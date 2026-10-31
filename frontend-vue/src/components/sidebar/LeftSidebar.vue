<script setup>
// ==================== 左栏容器：对话列表 / 插件 / 技能 / MCP / 多智能体 ====================
// 图层管理已移到地图左上角（MapPanel），不再占用 Agent 左栏。
import { ref } from 'vue'
import ConvSidebar from '@/components/sidebar/ConvSidebar.vue'
import SkillsPanel from '@/components/sidebar/SkillsPanel.vue'
import McpPanel from '@/components/sidebar/McpPanel.vue'
import AgentPlaceholderPanel from '@/components/sidebar/AgentPlaceholderPanel.vue'
import { useImagerySearch } from '@/composables/useImagerySearch'

const { openDialog: openImageryDialog } = useImagerySearch()
const activeTab = ref('conv')

const pluginItems = [
  { name: '影像下载器', desc: '按 AOI/时间下载多源影像并自动入库，规划为可插拔数据源。', status: '占位' },
  { name: '报告导出', desc: '解译结果一键导出 Word / PDF / 专题图，规划为插件市场能力。', status: '占位' },
  { name: 'GeoServer 连接器', desc: '管理多个 GeoServer 实例与发布策略，规划为连接器插件。', status: '占位' },
]

const multiAgentItems = [
  {
    name: '规划智能体',
    desc: '理解目标、拆解任务、选择技能与工具，生成可检查的执行计划与阶段检查点。',
    status: '占位',
  },
  {
    name: '执行智能体',
    desc: '按计划调用影像检索、框选、解译、变化检测与数据工具，产出证据、图层和中间结果。',
    status: '占位',
  },
  {
    name: '报告智能体',
    desc: '汇总执行证据、统计与图注，生成业务报告、专题图和可追溯的结论。',
    status: '占位',
  },
  {
    name: '审查智能体',
    desc: '校验证据完整性、结果质量、权限与合规红线；不通过时要求回退或重试。',
    status: '占位',
  },
]

function openSkillsPanel() {
  activeTab.value = 'skills'
}

function openMcpPanel() {
  activeTab.value = 'mcp'
}

function openPluginsPanel() {
  activeTab.value = 'plugins'
}

function openMultiAgentPanel() {
  activeTab.value = 'multi-agent'
}

function openConversationPanel() {
  // 入参: 无
  // 方法: 从其他面板返回对话列表
  // 出参: 无
  activeTab.value = 'conv'
}
</script>

<template>
  <div class="left-sidebar shell-fade-in">
    <div v-show="activeTab === 'conv'" class="sidebar-pane">
      <ConvSidebar
        @open-plugins="openPluginsPanel"
        @open-skills="openSkillsPanel"
        @open-mcp="openMcpPanel"
        @open-multi-agent="openMultiAgentPanel"
        @open-imagery="openImageryDialog"
      />
    </div>
    <div v-show="activeTab === 'plugins'" class="sidebar-pane">
      <AgentPlaceholderPanel
        title="插件"
        subtitle="扩展 Agent 的数据源、工具与导出能力。"
        :items="pluginItems"
        @back="openConversationPanel"
      />
    </div>
    <div v-show="activeTab === 'skills'" class="sidebar-pane">
      <SkillsPanel @back="openConversationPanel" />
    </div>
    <div v-show="activeTab === 'mcp'" class="sidebar-pane">
      <McpPanel @back="openConversationPanel" />
    </div>
    <div v-show="activeTab === 'multi-agent'" class="sidebar-pane">
      <AgentPlaceholderPanel
        title="多智能体"
        subtitle="规划 → 执行 → 报告 → 审查：四个专业角色协同完成复杂遥感任务。"
        :items="multiAgentItems"
        @back="openConversationPanel"
      />
    </div>
  </div>
</template>

<style scoped>
.left-sidebar {
  display: flex;
  flex-direction: column;
  height: 100%;
  overflow: hidden;
  background: var(--bg-panel);
}

.sidebar-pane {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-height: 0;
  overflow: hidden;
}

</style>
