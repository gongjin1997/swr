<script setup lang="ts">
/** 环境列表：每个已纳管的命名空间就是一个环境，展示环境档案、负载均衡入口与服务数。 */
import { Refresh, Search } from '@element-plus/icons-vue'
import { computed, onMounted, reactive, ref } from 'vue'
import { environmentApi } from '@/api'
import type { EnvironmentSummary } from '@/api/types'
import PageHeader from '@/components/PageHeader.vue'
import { notifyError } from '@/composables/useAction'
import { TIER_TAG, TIER_TEXT, TIERS } from '@/constants'
import { useSession } from '@/stores/session'
import TablePager from '@/components/TablePager.vue'
import { usePagination } from '@/composables/usePagination'

const session = useSession()
const items = ref<EnvironmentSummary[]>([])
const loading = ref(false)
const filters = reactive({ keyword: '', tier: '' })
const filtered = computed(() => items.value.filter((e) => (!filters.tier || e.tier === filters.tier) &&
  (!filters.keyword.trim() || `${e.name} ${e.cluster_name}`.toLowerCase().includes(filters.keyword.trim().toLowerCase()))))
const pager = usePagination(() => filtered.value, { resetOn: () => [filters.keyword, filters.tier] })

const projectNames = (ids: number[]) => ids.map((id) => session.projectName(id)).join('、')

async function load() {
  loading.value = true
  try {
    items.value = (await environmentApi.list()).items
  } catch (e) {
    notifyError(e)
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="page">
    <PageHeader title="环境" description="每个已纳管的命名空间是一个环境。环境档案保存命名规则、入口、调度等结构参数，基础配置保存与环境相关的环境变量和配置文件。" />
    <el-card>
      <div class="filter-bar">
        <el-input v-model="filters.keyword" :prefix-icon="Search" placeholder="搜索环境或集群" clearable aria-label="搜索环境" />
        <el-select v-model="filters.tier" clearable placeholder="全部级别" aria-label="按级别筛选">
          <el-option v-for="[value, label] in TIERS" :key="value" :label="label" :value="value" />
        </el-select>
        <el-button :icon="Refresh" :loading="loading" @click="load">刷新</el-button>
        <span class="count">共 {{ filtered.length }} 个环境</span>
      </div>
      <el-table v-loading="loading" :data="pager.rows.value" empty-text="暂无可见环境；平台管理员在“集群与命名空间”中纳管命名空间后即出现在这里">
        <el-table-column label="环境" min-width="200">
          <template #default="{ row }">
            <RouterLink :to="{ name: 'environment-detail', params: { id: row.id } }"><b>{{ row.name }}</b></RouterLink>
            <div class="hint">{{ row.cluster_name }}</div>
          </template>
        </el-table-column>
        <el-table-column label="级别" width="130">
          <template #default="{ row }"><el-tag size="small" :type="TIER_TAG[row.tier]" effect="plain">{{ TIER_TEXT[row.tier] }}</el-tag></template>
        </el-table-column>
        <el-table-column label="所属项目" min-width="160">
          <template #default="{ row }">
            <span v-if="!row.project_ids.length" class="hint">未分配</span>
            <span v-else>{{ projectNames(row.project_ids) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="服务" width="80"><template #default="{ row }">{{ row.service_count }}</template></el-table-column>
        <el-table-column label="环境档案" width="110">
          <template #default="{ row }">
            <el-tag v-if="row.profile_version" size="small" type="success" effect="plain">v{{ row.profile_version }}</el-tag>
            <el-text v-else type="info">—</el-text>
          </template>
        </el-table-column>
        <el-table-column label="负载均衡入口" min-width="160">
          <template #default="{ row }">{{ row.ingress_profile_name || '—' }}</template>
        </el-table-column>
        <el-table-column label="操作" width="90" fixed="right">
          <template #default="{ row }">
            <RouterLink :to="{ name: 'environment-detail', params: { id: row.id } }"><el-button link type="primary">详情</el-button></RouterLink>
          </template>
        </el-table-column>
      </el-table>
      <TablePager v-model:page="pager.page.value" v-model:size="pager.size.value" :total="pager.total.value" />
    </el-card>
  </div>
</template>
