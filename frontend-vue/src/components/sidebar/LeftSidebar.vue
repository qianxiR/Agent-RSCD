<script setup>
// ==================== 左栏容器：对话列表 + 扩展/资料库 弹窗 ====================
// 图层管理已移到地图左上角（MapPanel），不再占用 Agent 左栏。
// 专家/技能/连接器合并为扩展弹窗，资料库独立弹窗；布局均对齐影像检索弹窗。
import { ref } from 'vue'
import ConvSidebar from '@/components/sidebar/ConvSidebar.vue'
import ExtensionsDialog from '@/components/sidebar/ExtensionsDialog.vue'
import LibraryDialog from '@/components/sidebar/LibraryDialog.vue'
import { useImagerySearch } from '@/composables/useImagerySearch'

const { openDialog: openImageryDialog } = useImagerySearch()

const showExtensions = ref(false)
const showLibrary = ref(false)
</script>

<template>
  <div class="left-sidebar shell-fade-in">
    <div class="sidebar-pane">
      <ConvSidebar
        @open-extensions="showExtensions = true"
        @open-library="showLibrary = true"
        @open-imagery="openImageryDialog"
      />
    </div>

    <ExtensionsDialog v-model:open="showExtensions" />
    <LibraryDialog v-model:open="showLibrary" />
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
