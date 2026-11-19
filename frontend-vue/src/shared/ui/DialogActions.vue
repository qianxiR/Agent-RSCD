<!-- 弹窗底部操作：右对齐，主按钮靠右，次按钮在其左。 -->
<template>
  <div class="ui-dialog-actions">
    <slot />
  </div>
</template>

<style scoped>
.ui-dialog-actions {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 14px;
}
</style>
