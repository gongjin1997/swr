<script setup lang="ts">
import { Delete, Edit, Plus, Refresh, View } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { resourceApi } from '@/api'
import type { Resource, ResourceScopeType } from '@/api/types'
import PageHeader from '@/components/PageHeader.vue'
import { useAction } from '@/composables/useAction'
import { useSession } from '@/stores/session'
import { fmtTime } from '@/utils/format'
import { getResourceTypeLabel } from '@/utils/resourceTypes'

const session = useSession()
const router = useRouter()
const resources = ref<Resource[]>([])
const loading = ref(false)
const activeTab = ref<ResourceScopeType>('platform')

const canManage = computed(() => session.can('resource.manage'))

async function load() {
  loading.value = true
  try {
    const result = await resourceApi.list({ scope_type: activeTab.value })
    resources.value = result.items
  } catch (e) {
    ElMessage.error('加载资源列表失败')
    console.error(e)
  } finally {
    loading.value = false
  }
}

const deleteAction = useAction()

async function handleDelete(resource: Resource) {
  await deleteAction.run(async () => {
    await ElMessageBox.confirm(`确定要删除资源 "${resource.name}" 吗？`, '确认删除', {
      type: 'warning',
      confirmButtonText: '删除',
      cancelButtonText: '取消',
    })
    await resourceApi.remove(resource.id)
    ElMessage.success('资源已删除')
    await load()
  })
}

function handleTabChange(tab: ResourceScopeType) {
  activeTab.value = tab
  load()
}

function getScopeTag(resource: Resource) {
  return resource.scope_type === 'platform'
    ? { label: '平台', type: 'primary' as const }
    : { label: `环境 #${resource.scope_id}`, type: 'success' as const }
}

onMounted(load)
</script>

<template>
  <PageHeader title="资源管理">
    <template #actions>
      <el-button :icon="Refresh" @click="load" :loading="loading">刷新</el-button>
      <el-button v-if="canManage" type="primary" :icon="Plus" @click="router.push('/resources/new')">
        创建资源
      </el-button>
    </template>
  </PageHeader>

  <el-tabs :model-value="activeTab" @tab-change="handleTabChange">
    <el-tab-pane label="平台资源" name="platform" />
    <el-tab-pane label="环境资源" name="environment" />
  </el-tabs>

  <el-table :data="resources" v-loading="loading" stripe>
    <el-table-column prop="name" label="资源名称" min-width="150">
      <template #default="{ row }">
        <el-link type="primary" @click="router.push(`/resources/${row.id}`)">
          {{ row.name }}
        </el-link>
      </template>
    </el-table-column>

    <el-table-column prop="type" label="资源类型" width="150">
      <template #default="{ row }">
        <el-tag>{{ getResourceTypeLabel(row.type) }}</el-tag>
      </template>
    </el-table-column>

    <el-table-column prop="description" label="描述" min-width="200" show-overflow-tooltip />

    <el-table-column prop="version" label="版本" width="80" align="center" />

    <el-table-column label="作用域" width="150">
      <template #default="{ row }">
        <el-tag :type="getScopeTag(row).type">{{ getScopeTag(row).label }}</el-tag>
      </template>
    </el-table-column>

    <el-table-column label="使用情况" width="100" align="center">
      <template #default="{ row }">
        {{ row.binding_count !== undefined ? `${row.binding_count} 个` : '-' }}
      </template>
    </el-table-column>

    <el-table-column prop="created_at" label="创建时间" width="160">
      <template #default="{ row }">{{ fmtTime(row.created_at) }}</template>
    </el-table-column>

    <el-table-column label="操作" width="200" fixed="right">
      <template #default="{ row }">
        <el-button size="small" :icon="View" @click="router.push(`/resources/${row.id}`)">
          查看
        </el-button>
        <template v-if="row.can_edit">
          <el-button size="small" :icon="Edit" @click="router.push(`/resources/${row.id}/edit`)">
            编辑
          </el-button>
          <el-button
            size="small"
            type="danger"
            :icon="Delete"
            @click="handleDelete(row)"
            :loading="deleteAction.busy.value"
          >
            删除
          </el-button>
        </template>
      </template>
    </el-table-column>
  </el-table>
</template>
