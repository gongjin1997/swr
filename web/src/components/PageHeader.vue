<script setup lang="ts">
/** 页面标题区：标题、说明与右侧操作按钮。各页面统一使用。 */
defineProps<{ title: string; description?: string }>()
</script>

<template>
  <div class="page-heading">
    <div class="page-heading-text">
      <h1>{{ title }}</h1>
      <p v-if="description" class="hint">{{ description }}</p>
      <slot name="meta" />
    </div>
    <div v-if="$slots.default" class="toolbar"><slot /></div>
  </div>
</template>

<style scoped>
.page-heading-text { min-width: 0; }
.page-heading-text p { margin: 6px 0 0; }
</style>
