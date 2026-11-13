<script setup lang="ts">
/** 授权记录表：授权页、账号详情、项目/命名空间成员共用。撤销按钮只对可撤销的授权显示。 */
import { bindingApi } from '@/api'
import type { BindingRow } from '@/api/types'
import TablePager from '@/components/TablePager.vue'
import { confirmAction, useAction } from '@/composables/useAction'
import { usePagination } from '@/composables/usePagination'
import { SCOPE_TEXT } from '@/constants'
import { fmtTime } from '@/utils/format'

const props = defineProps<{ rows: BindingRow[]; loading?: boolean; hideSubject?: boolean; hideScope?: boolean; emptyText?: string }>()
const pager = usePagination(() => props.rows)
const emit = defineEmits<{ revoked: [] }>()
const { busy, run } = useAction()

async function revoke(row: BindingRow) {
  const who = row.subject.display_name || row.subject.username
  if (!(await confirmAction(`撤销 ${who} 在“${row.scope_label}”的“${row.role_name}”授权？撤销后立即生效。`, '撤销授权', true))) return
  if (await run(() => bindingApi.remove(row.id), '授权已撤销')) emit('revoked')
}
</script>

<template>
  <el-table v-loading="loading" :data="pager.rows.value" :empty-text="emptyText ?? '暂无授权'">
    <el-table-column v-if="!hideSubject" label="账号" min-width="170">
      <template #default="{ row }">
        <b>{{ row.subject.display_name || row.subject.username }}</b>
        <div class="hint">{{ row.subject.username }}<el-tag v-if="!row.subject.enabled" size="small" type="info" class="gap">已停用</el-tag></div>
      </template>
    </el-table-column>
    <el-table-column label="角色" min-width="140">
      <template #default="{ row }">
        {{ row.role_name }}<el-tag v-if="!row.role_enabled" size="small" type="info" class="gap">角色已停用</el-tag>
      </template>
    </el-table-column>
    <el-table-column v-if="!hideScope" label="范围" min-width="200">
      <template #default="{ row }"><span class="hint">{{ SCOPE_TEXT[row.scope_type as keyof typeof SCOPE_TEXT] }}</span> {{ row.scope_label }}</template>
    </el-table-column>
    <el-table-column label="有效期" min-width="150">
      <template #default="{ row }">
        <el-tag v-if="row.status === 'expired'" size="small" type="info">已过期</el-tag>
        <span v-else>{{ row.expires_at ? `至 ${fmtTime(row.expires_at)}` : '长期' }}</span>
      </template>
    </el-table-column>
    <el-table-column label="授予" min-width="160">
      <template #default="{ row }">{{ row.created_by_username || '系统' }}<div class="hint">{{ fmtTime(row.created_at) }}</div></template>
    </el-table-column>
    <el-table-column prop="note" label="说明" min-width="140" show-overflow-tooltip />
    <el-table-column label="操作" width="80" fixed="right">
      <template #default="{ row }">
        <el-button v-if="row.can_revoke" link type="danger" :disabled="busy" @click="revoke(row)">撤销</el-button>
        <span v-else class="hint">—</span>
      </template>
    </el-table-column>
  </el-table>
  <TablePager v-model:page="pager.page.value" v-model:size="pager.size.value" :total="pager.total.value" />
</template>

<style scoped>
.gap { margin-left: 6px; }
</style>
