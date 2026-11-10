<script setup lang="ts">
import type { Component } from 'vue'
withDefaults(defineProps<{ title: string; value: number | string; sub: string; icon: Component; tone?: string }>(), { tone: 'primary' })
</script>
<template>
  <div class="stat-card" :style="{ '--accent': `var(--el-color-${tone})` }">
    <div><div class="stat-title">{{ title }}</div><div class="stat-value num">{{ value }}</div><div class="stat-sub">{{ sub }}</div></div>
    <div class="stat-icon"><el-icon :size="22"><component :is="icon" /></el-icon></div>
  </div>
</template>
<style scoped>
.stat-card { display: flex; justify-content: space-between; gap: 14px; padding: 16px 18px; background: var(--hi-bg-card); border: 1px solid var(--hi-border); border-radius: 10px; box-shadow: var(--hi-shadow); }
.stat-title { color: var(--hi-text-2); font-size: 13px; }
.stat-value { font-size: 26px; font-weight: 650; margin: 8px 0 6px; line-height: 1.1; }
.stat-sub { font-size: 12px; color: var(--hi-text-3); }
.stat-icon { width: 40px; height: 40px; display: grid; place-items: center; border-radius: 10px; color: var(--accent); background: color-mix(in srgb, var(--accent) 9%, var(--hi-bg-card)); }
@media (max-width: 767px) { .stat-card { padding: 14px 12px; gap: 8px; } .stat-icon { width: 30px; height: 30px; } .stat-value { font-size: 26px; } }
</style>
