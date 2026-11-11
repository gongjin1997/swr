<script setup lang="ts">
/** 以分组标签展示一组权限。 */
import { computed } from 'vue'
import { useCatalog } from '@/stores/catalog'

const props = defineProps<{ permissions: string[]; empty?: string }>()
const catalog = useCatalog()
const groups = computed(() => catalog.grouped(props.permissions))
</script>

<template>
  <div v-if="groups.length" class="permission-tags">
    <div v-for="group in groups" :key="group.code" class="row">
      <span class="group">{{ group.label }}</span>
      <span class="items">
        <el-tag v-for="p in group.items" :key="p.code" size="small" effect="plain" disable-transitions>{{ p.label }}</el-tag>
      </span>
    </div>
  </div>
  <span v-else class="hint">{{ empty ?? '无' }}</span>
</template>

<style scoped>
.permission-tags { display: grid; gap: 6px; }
.row { display: flex; gap: 10px; align-items: baseline; }
.group { flex: none; width: 86px; font-size: 12px; color: var(--hi-text-3); }
.items { display: flex; flex-wrap: wrap; gap: 4px; }
</style>
