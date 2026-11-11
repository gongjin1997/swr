<script setup lang="ts">
import type { SettingField } from '@/api/types'
defineProps<{ field: SettingField }>()
const value = defineModel<string | number | boolean | undefined>({ required: true })
const clear = defineModel<boolean>('clear', { default: false })
const selects: Record<string, [string, string][]> = {
  'hcs.auth_mode': [['aksk', 'AK/SK 签名换 Token'], ['password', '用户名密码'], ['token', '预置 Token']],
  'hcs.token_scope': [['project', '项目级（project）'], ['domain', '账号级（domain）']],
}
const sources = { db: '页面设置', file: '配置文件 / 默认值', env: '环境变量' }
</script>

<template>
  <el-form-item :label="field.label" :class="{ 'switch-field': field.type === 'bool' }">
    <el-select v-if="selects[field.path]" v-model="value" :disabled="!!field.locked_by" :aria-label="field.label">
      <el-option v-for="[option, label] in selects[field.path]" :key="option" :label="label" :value="option" />
    </el-select>
    <el-switch v-else-if="field.type === 'bool'" :model-value="Boolean(value)" @update:model-value="value = $event" :disabled="!!field.locked_by" :aria-label="field.label" />
    <el-input-number v-else-if="field.type === 'int' || field.type === 'float'" :model-value="value as number" @update:model-value="value = $event" :min="field.minimum" :max="field.maximum" :step="field.type === 'int' ? 1 : .5" :precision="field.type === 'int' ? 0 : undefined" controls-position="right" :disabled="!!field.locked_by" :aria-label="field.label" />
    <el-input v-else-if="field.type === 'secret'" v-model="value" type="password" show-password autocomplete="new-password" :placeholder="field.is_set ? '已设置，留空保持不变' : '未设置'" :disabled="!!field.locked_by || clear" :aria-label="field.label" />
    <el-input v-else-if="field.type === 'list' || field.path === 'hcs.aksk_request_body'" v-model="value" type="textarea" :rows="3" class="mono" spellcheck="false" :placeholder="field.type === 'list' ? '[&quot;--network&quot;, &quot;host&quot;]' : ''" :disabled="!!field.locked_by" :aria-label="field.label" />
    <el-input v-else v-model="value" :disabled="!!field.locked_by" :aria-label="field.label" />
    <div class="field-help"><span v-if="field.help">{{ field.help }}</span><span class="source" :class="{ locked: field.locked_by }">{{ field.locked_by ? `由 ${field.locked_by} 控制（只读）` : `来源：${sources[field.source]}` }}</span></div>
    <el-checkbox v-if="field.type === 'secret' && !field.locked_by && field.is_set" v-model="clear" size="small">清空已保存的值</el-checkbox>
  </el-form-item>
</template>

<style scoped>
.field-help { width: 100%; margin-top: 5px; font-size: 11px; color: var(--hi-text-3); line-height: 1.65; overflow-wrap: anywhere; }
.source { display: block; }
.locked { color: var(--el-color-warning); }
.el-input-number { width: 100%; }
</style>
