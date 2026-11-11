<script setup lang="ts">
import { Search } from '@element-plus/icons-vue'
import { onMounted, reactive, ref } from 'vue'
import { auditApi } from '@/api'
import type { AuditEntry } from '@/api/types'
import PageHeader from '@/components/PageHeader.vue'
import TablePager from '@/components/TablePager.vue'
import { notifyError } from '@/composables/useAction'
import { fmtTime } from '@/utils/format'

const PAGE = 20
/** 常用操作前缀，与后端审计动作命名一致。 */
const PREFIXES: [string, string][] = [
  ['auth.', '登录'], ['account.', '个人账号'], ['user.', '账号管理'], ['binding.', '授权'], ['role.', '角色'],
  ['project.', '项目'], ['app.', '应用'], ['build.', '构建'], ['job.', '构建任务'], ['service.', '服务'],
  ['deploy.', '发布与审批'], ['cluster.', '集群'], ['namespace.', '命名空间'], ['settings.', '设置'],
]
const items = ref<AuditEntry[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(PAGE)
const loading = ref(false)
const filters = reactive({ username: '', action: '' })

async function load(reset = true) {
  if (reset) page.value = 1
  loading.value = true
  try {
    const data = await auditApi.list({ limit: pageSize.value, offset: (page.value - 1) * pageSize.value,
      username: filters.username.trim(), action: filters.action.trim() })
    items.value = data.items
    total.value = data.total
  } catch (e) {
    notifyError(e)
  } finally {
    loading.value = false
  }
}

function pick(prefix: string) {
  filters.action = filters.action === prefix ? '' : prefix
  void load(true)
}

onMounted(() => load(true))
</script>

<template>
  <div class="page">
    <PageHeader title="操作审计" description="登录、账号与授权、项目资源、构建与发布等操作记录，不含密码与密钥等敏感值。" />
    <el-card>
      <div class="filter-bar">
        <el-input v-model="filters.username" placeholder="账号" clearable aria-label="按账号筛选" @keyup.enter="load(true)" @clear="load(true)" />
        <el-input v-model="filters.action" placeholder="操作前缀，如 deploy." clearable aria-label="按操作筛选" @keyup.enter="load(true)" @clear="load(true)" />
        <el-button :icon="Search" :loading="loading" @click="load(true)">查询</el-button>
        <span class="count">共 {{ total }} 条记录</span>
      </div>
      <div class="prefixes">
        <el-check-tag v-for="[prefix, label] in PREFIXES" :key="prefix" :checked="filters.action === prefix" @change="pick(prefix)">{{ label }}</el-check-tag>
      </div>
      <el-table v-loading="loading && !items.length" :data="items" empty-text="暂无审计记录">
        <el-table-column label="时间" width="170"><template #default="{ row }">{{ fmtTime(row.created_at) }}</template></el-table-column>
        <el-table-column prop="username" label="账号" width="140" />
        <el-table-column label="操作" min-width="200"><template #default="{ row }"><span class="mono">{{ row.action }}</span></template></el-table-column>
        <el-table-column label="目标 / 摘要" min-width="260">
          <template #default="{ row }">{{ row.target }}<div class="hint break-word">{{ row.detail }}</div></template>
        </el-table-column>
        <el-table-column prop="ip" label="IP" width="140" />
      </el-table>
      <TablePager v-model:page="page" v-model:size="pageSize" :total="total" @change="load(false)" />
    </el-card>
  </div>
</template>

<style scoped>
.prefixes { display: flex; flex-wrap: wrap; gap: 8px; margin: -6px 0 16px; }
</style>
