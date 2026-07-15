<script setup>
// ==================== 左栏容器：对话列表 / 图层管理 ====================
import { ref } from 'vue'
import ConvSidebar from '@/components/sidebar/ConvSidebar.vue'
import LayerPanel from '@/components/map/LayerPanel.vue'

const activeTab = ref('conv')

function openLayerPanel() {
  // 入参: 无
  // 方法: 将左侧主区域切换到图层管理，保留对话侧栏的单列导航结构
  // 出参: 无
  activeTab.value = 'layer'
}

function openConversationPanel() {
  // 入参: 无
  // 方法: 从图层管理返回对话列表
  // 出参: 无
  activeTab.value = 'conv'
}
</script>

<template>
  <div class="left-sidebar shell-fade-in">
    <div v-show="activeTab === 'conv'" class="sidebar-pane">
      <ConvSidebar @open-layer="openLayerPanel" />
    </div>
    <div v-show="activeTab === 'layer'" class="sidebar-pane">
      <LayerPanel @back="openConversationPanel" />
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
