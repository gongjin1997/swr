<script setup lang="ts">
import { Delete, Plus, Refresh } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { computed, onMounted, ref } from 'vue'
import { serviceResourceApi, resourceApi } from '@/api'
import type { Resource, ResourceBinding } from '@/api/types'
import { useAction } from '@/composables/useAction'
import { useSession } from '@/stores/session'
import { getResourceTypeLabel } from '@/utils/resourceTypes'

const props = defineProps<{
  serviceId: number
  namespaceId: number
}>()

const session = useSession()
const bindings = ref<ResourceBinding[]>([])
const availableResources = ref<Resource[]>([])
const loading = ref(false)
const bindDialogVisible = ref(false)
const bindForm = ref({ resource_id: null as number | null, alias: '' })

const canManage = computed(() => session.canNamespace(props.namespaceId, 'service.config'))

async function loadBindings() {
  loading.value = true
  try {
    const result = await serviceResourceApi.listBindings(props.serviceId)
    bindings.value = result.items
  } catch (e) {
    ElMessage.error('加载资源绑定失败')
    console.error(e)
  } finally {
    loading.value = false
  }
}

async function loadAvailableResources() {
  try {
    // 加载平台资源和当前环境的资源
    const [platformRes, envRes] = await Promise.all([
      resourceApi.list({ scope_type: 'platform' }),
      resourceApi.list({ scope_type: 'environment', scope_id: props.namespaceId }),
    ])
    availableResources.value = [...platformRes.items, ...envRes.items]
  } catch (e) {
    ElMessage.error('加载可用资源失败')
    console.error(e)
  }
}

function openBindDialog() {
  bindForm.value = { resource_id: null, alias: '' }
  loadAvailableResources()
  bindDialogVisible.value = true
}

const [doBind, binding] = useAction(
  async () => {
    if (!bindForm.value.resource_id || !bindForm.value.alias) {
      ElMessage.error('请填写完整信息')
      return
    }
    await serviceResourceApi.bind(props.serviceId, {
      resource_id: bindForm.value.resource_id,
      alias: bindForm.value.alias,
    })
    ElMessage.success('资源已绑定')
    bindDialogVisible.value = false
    await loadBindings()
  },
  { confirm: false }
)

const [doUnbind, unbinding] = useAction(
  async (alias: string, resourceName: string) => {
    await ElMessageBox.confirm(`确定要解绑资源 "${resourceName}" (别名: ${alias}) 吗？`, '确认解绑', {
      type: 'warning',
      confirmButtonText: '解绑',
      cancelButtonText: '取消',
    })
    await serviceResourceApi.unbind(props.serviceId, alias)
    ElMessage.success('资源已解绑')
    await loadBindings()
  },
  { confirm: false }
)

function getScopeTag(binding: ResourceBinding) {
  return binding.resource.scope_type === 'platform'
    ? { label: '平台', type: 'primary' as const }
    : { label: '环境', type: 'success' as const }
}

onMounted(loadBindings)
</script>

<template>
  <div>
    <div class="toolbar" style="margin-bottom: 16px">
      <el-button :icon="Refresh" @click="loadBindings" :loading="loading">刷新</el-button>
      <el-button v-if="canManage" type="primary" :icon="Plus" @click="openBindDialog">绑定资源</el-button>
    </div>

    <el-table :data="bindings" v-loading="loading" stripe>
      <el-table-column prop="alias" label="别名" width="150">
        <template #default="{ row }">
          <el-text tag="b">{{ row.alias }}</el-text>
        </template>
      </el-table-column>

      <el-table-column label="资源名称" min-width="150">
        <template #default="{ row }">
          <router-link :to="`/resources/${row.resource.id}`" class="el-link el-link--primary">
            {{ row.resource.name }}
          </router-link>
        </template>
      </el-table-column>

      <el-table-column label="资源类型" width="150">
        <template #default="{ row }">
          <el-tag>{{ getResourceTypeLabel(row.resource.type) }}</el-tag>
        </template>
      </el-table-column>

      <el-table-column label="版本" width="80" align="center">
        <template #default="{ row }">v{{ row.resource.version }}</template>
      </el-table-column>

      <el-table-column label="作用域" width="120">
        <template #default="{ row }">
          <el-tag :type="getScopeTag(row).type">{{ getScopeTag(row).label }}</el-tag>
        </template>
      </el-table-column>

      <el-table-column label="操作" width="100" fixed="right">
        <template #default="{ row }">
          <el-button
            v-if="canManage"
            size="small"
            type="danger"
            :icon="Delete"
            @click="doUnbind(row.alias, row.resource.name)"
            :loading="unbinding"
          >
            解绑
          </el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-empty v-if="!loading && bindings.length === 0" description="暂无资源绑定" />

    <el-dialog v-model="bindDialogVisible" title="绑定资源" width="500px">
      <el-form :model="bindForm" label-width="80px">
        <el-form-item label="资源" required>
          <el-select v-model="bindForm.resource_id" placeholder="选择要绑定的资源" filterable style="width: 100%">
            <el-option
              v-for="resource in availableResources"
              :key="resource.id"
              :label="`${resource.name} (${getResourceTypeLabel(resource.type)})`"
              :value="resource.id"
            />
          </el-select>
        </el-form-item>

        <el-form-item label="别名" required>
          <el-input
            v-model="bindForm.alias"
            placeholder="例如：db, cache, mq"
            maxlength="63"
          />
          <div style="font-size: 12px; color: var(--el-text-color-secondary); margin-top: 4px">
            用于导出环境变量的前缀，例如：cache → CACHE_HOST, CACHE_PORT
          </div>
        </el-form-item>
      </el-form>

      <template #footer>
        <el-button @click="bindDialogVisible = false">取消</el-button>
        <el-button type="primary" @click="doBind" :loading="binding">确定</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.toolbar {
  display: flex;
  gap: 8px;
}
</style>
