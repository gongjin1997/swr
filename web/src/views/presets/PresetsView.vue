<script setup lang="ts">
import { Delete, Edit, Plus, Refresh } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { presetApi } from '@/api'
import type { Preset } from '@/api/types'
import PageHeader from '@/components/PageHeader.vue'
import { useAction } from '@/composables/useAction'
import { useSession } from '@/stores/session'
import { fmtTime } from '@/utils/format'

const session = useSession()
const router = useRouter()
const presets = ref<Preset[]>([])
const loading = ref(false)

const canManage = session.hasPermission('resource.manage')

async function load() {
  loading.value = true
  try {
    const result = await presetApi.list()
    presets.value = result.items
  } catch (e) {
    ElMessage.error('加载预置列表失败')
    console.error(e)
  } finally {
    loading.value = false
  }
}

const [doDelete, deleting] = useAction(
  async (preset: Preset) => {
    await ElMessageBox.confirm(`确定要删除预置 "${preset.name}" 吗？`, '确认删除', {
      type: 'warning',
      confirmButtonText: '删除',
      cancelButtonText: '取消',
    })
    await presetApi.delete(preset.id)
    ElMessage.success('预置已删除')
    await load()
  },
  { confirm: false }
)

onMounted(load)
</script>

<template>
  <PageHeader title="环境预置">
    <template #actions>
      <el-button :icon="Refresh" @click="load" :loading="loading">刷新</el-button>
      <el-button v-if="canManage" type="primary" :icon="Plus" @click="router.push('/presets/new')">
        创建预置
      </el-button>
    </template>
  </PageHeader>

  <el-card>
    <el-table :data="presets" v-loading="loading" stripe>
      <el-table-column prop="name" label="预置名称" min-width="150">
        <template #default="{ row }">
          <el-link type="primary" @click="router.push(`/presets/${row.id}`)">
            {{ row.name }}
          </el-link>
        </template>
      </el-table-column>

      <el-table-column prop="description" label="描述" min-width="200" show-overflow-tooltip />

      <el-table-column label="必需资源类型" min-width="150">
        <template #default="{ row }">
          <el-tag
            v-for="type in row.required_resource_types"
            :key="type"
            size="small"
            style="margin-right: 4px"
          >
            {{ type }}
          </el-tag>
          <span v-if="row.required_resource_types.length === 0">-</span>
        </template>
      </el-table-column>

      <el-table-column label="默认配置集" width="100" align="center">
        <template #default="{ row }">
          {{ row.default_config_sets.length }} 个
        </template>
      </el-table-column>

      <el-table-column label="必需参数" width="100" align="center">
        <template #default="{ row }">
          {{ row.required_params.length }} 个
        </template>
      </el-table-column>

      <el-table-column prop="created_at" label="创建时间" width="160">
        <template #default="{ row }">{{ fmtTime(row.created_at) }}</template>
      </el-table-column>

      <el-table-column label="操作" width="150" fixed="right">
        <template #default="{ row }">
          <el-button
            v-if="canManage"
            size="small"
            :icon="Edit"
            @click="router.push(`/presets/${row.id}/edit`)"
          >
            编辑
          </el-button>
          <el-button
            v-if="canManage"
            size="small"
            type="danger"
            :icon="Delete"
            @click="doDelete(row)"
            :loading="deleting"
          >
            删除
          </el-button>
        </template>
      </el-table-column>
    </el-table>
  </el-card>
</template>
