<script setup lang="ts">
/** 按分组选择某个授权范围内可授予的权限，并预览补齐隐含权限后的实际效果。 */
import { computed } from 'vue'
import type { ScopeType } from '@/api/types'
import { useCatalog } from '@/stores/catalog'

const props = defineProps<{ scope: ScopeType; disabled?: boolean }>()
const selected = defineModel<string[]>({ required: true })
const catalog = useCatalog()

const groups = computed(() => catalog.grouped(catalog.data?.scope_permissions[props.scope] ?? []))
const implied = computed(() => {
  const chosen = new Set(selected.value)
  return catalog.expand(selected.value, props.scope).filter((code) => !chosen.has(code))
})
</script>

<template>
  <div class="picker">
    <section v-for="group in groups" :key="group.code" class="picker-group">
      <h4>{{ group.label }}</h4>
      <el-checkbox-group v-model="selected" :disabled="disabled" class="picker-options">
        <el-checkbox v-for="p in group.items" :key="p.code" :value="p.code">
          {{ p.label }}<span class="code">{{ p.code }}</span>
        </el-checkbox>
      </el-checkbox-group>
    </section>
    <p v-if="implied.length" class="hint">保存后会自动包含：{{ implied.map(catalog.permissionLabel).join('、') }}</p>
  </div>
</template>

<style scoped>
.picker { width: 100%; }
.picker-group + .picker-group { margin-top: 10px; }
.picker-group h4 { margin: 0 0 4px; font-size: 12px; font-weight: 600; color: var(--hi-text-2); }
.picker-options { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 0 16px; }
.code { margin-left: 6px; font: 11px var(--app-mono); color: var(--hi-text-3); }
@media (max-width: 600px) { .picker-options { grid-template-columns: 1fr; } }
</style>
