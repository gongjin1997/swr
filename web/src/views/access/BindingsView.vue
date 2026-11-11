<script setup lang="ts">
import { Plus, Refresh, Search } from '@element-plus/icons-vue'
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { bindingApi } from '@/api'
import type { BindingRow, ScopeType } from '@/api/types'
import GrantDialog from '@/components/access/GrantDialog.vue'
import GrantTable from '@/components/access/GrantTable.vue'
import { notifyError } from '@/composables/useAction'
import { SCOPE_TEXT } from '@/constants'
import { useCatalog } from '@/stores/catalog'

const catalog = useCatalog()
const route = useRoute()
const router = useRouter()
const rows = ref<BindingRow[]>([])
const loading = ref(false)
const adding = ref(false)
const filters = reactive({ keyword: '', scope: '' as ScopeType | '', role: String(route.query.role ?? ''), status: '' })

const filtered = computed(() => rows.value.filter((row) => {
  const keyword = filters.keyword.trim().toLowerCase()
  if (keyword && !`${row.subject.username} ${row.subject.display_name} ${row.scope_label} ${row.note}`.toLowerCase().includes(keyword)) return false
  if (filters.scope && row.scope_type !== filters.scope) return false
  if (filters.role && row.role !== filters.role) return false
  if (filters.status && row.status !== filters.status) return false
  return true
}))
const roleOptions = computed(() => catalog.roles.filter((r) => !filters.scope || r.scope_type === filters.scope))

async function load() {
  loading.value = true
  try {
    const [list] = await Promise.all([bindingApi.list(), catalog.load()])
    rows.value = list.items
  } catch (e) {
    notifyError(e)
  } finally {
    loading.value = false
  }
}

watch(() => filters.role, (role) => {
  void router.replace({ query: { ...route.query, role: role || undefined } })
})
onMounted(load)
</script>

<template>
  <el-card>
    <div class="filter-bar">
      <el-input v-model="filters.keyword" :prefix-icon="Search" placeholder="搜索账号、范围或说明" clearable aria-label="搜索授权" />
      <el-select v-model="filters.scope" clearable placeholder="全部范围" aria-label="按范围筛选">
        <el-option v-for="(label, key) in SCOPE_TEXT" :key="key" :label="label" :value="key" />
      </el-select>
      <el-select v-model="filters.role" clearable filterable placeholder="全部角色" aria-label="按角色筛选">
        <el-option v-for="r in roleOptions" :key="r.code" :label="r.name" :value="r.code" />
      </el-select>
      <el-select v-model="filters.status" clearable placeholder="全部状态" aria-label="按状态筛选">
        <el-option label="有效" value="active" /><el-option label="已过期" value="expired" />
      </el-select>
      <el-button :icon="Refresh" :loading="loading" @click="load">刷新</el-button>
      <span class="count">共 {{ filtered.length }} 条授权</span>
      <el-button type="primary" :icon="Plus" @click="adding = true">新增授权</el-button>
    </div>
    <GrantTable :rows="filtered" :loading="loading" empty-text="没有匹配的授权" @revoked="load" />
  </el-card>
  <GrantDialog v-model="adding" @granted="load" />
</template>
