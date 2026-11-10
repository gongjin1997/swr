<script setup lang="ts">
import { Search } from '@element-plus/icons-vue'
import { onMounted, reactive, ref } from 'vue'
import { userApi } from '@/api'
import type { AuditEntry } from '@/api/types'
import { notifyError } from '@/composables/useAction'
import { fmtTime } from '@/utils/format'

const PAGE = 50
const items = ref<AuditEntry[]>([])
const total = ref(0)
const loading = ref(false)
const filters = reactive({ username: '', action: '' })

async function load(reset = true) {
  loading.value = true
  try {
    const data = await userApi.audit({ limit: PAGE, offset: reset ? 0 : items.value.length,
      username: filters.username.trim(), action: filters.action.trim() })
    items.value = reset ? data.items : items.value.concat(data.items)
    total.value = data.total
  } catch (e) {
    notifyError(e)
  } finally {
    loading.value = false
  }
}

onMounted(() => load(true))
</script>

<template>
  <div class="page">
    <el-card>
      <div class="filter-bar">
        <el-input v-model="filters.username" placeholder="账号" clearable aria-label="按账号筛选" @keyup.enter="load(true)" />
        <el-input v-model="filters.action" placeholder="操作前缀，如 deploy." clearable aria-label="按操作筛选" @keyup.enter="load(true)" />
        <el-button :icon="Search" :loading="loading" @click="load(true)">查询</el-button><span class="count">共 {{ total }} 条记录</span>
      </div>
      <el-table v-loading="loading && !items.length" :data="items" empty-text="暂无审计记录">
        <el-table-column label="时间" width="170"><template #default="{ row }">{{ fmtTime(row.created_at) }}</template></el-table-column>
        <el-table-column prop="username" label="账号" width="140" />
        <el-table-column label="操作" min-width="200"><template #default="{ row }"><span class="mono">{{ row.action }}</span></template></el-table-column>
        <el-table-column label="目标 / 摘要" min-width="240">
          <template #default="{ row }">{{ row.target }}<div class="hint break-word">{{ row.detail }}</div></template>
        </el-table-column>
        <el-table-column prop="ip" label="IP" width="140" />
      </el-table>
      <div v-if="items.length < total" class="more">
        <el-button :loading="loading" @click="load(false)">加载更多（{{ items.length }} / {{ total }}）</el-button>
      </div>
    </el-card>
  </div>
</template>

<style scoped>
.more {
  display: flex;
  justify-content: center;
  padding-top: 12px;
}
</style>
