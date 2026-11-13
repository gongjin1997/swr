<script setup lang="ts">
import { Delete, Edit, Refresh, View } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { resourceApi } from '@/api'
import type { Resource, ResourceVersion } from '@/api/types'
import PageHeader from '@/components/PageHeader.vue'
import { useAction } from '@/composables/useAction'
import { fmtTime } from '@/utils/format'
import { getResourceTypeLabel, getSecretAttributes, maskSecrets } from '@/utils/resourceTypes'

const route = useRoute()
const router = useRouter()
const resourceId = computed(() => Number(route.params.id))

const resource = ref<Resource | null>(null)
const versions = ref<ResourceVersion[]>([])
const loading = ref(false)
const revealed = ref(false)
const revealedData = ref<Record<string, any> | null>(null)
const activeTab = ref('info')

async function load() {
  loading.value = true
  try {
    const [r, v] = await Promise.all([
      resourceApi.get(resourceId.value),
      resourceApi.versions(resourceId.value),
    ])
    resource.value = r
    versions.value = v.items
  } catch (e) {
    ElMessage.error('加载资源详情失败')
    console.error(e)
  } finally {
    loading.value = false
  }
}

const deleteAction = useAction()

async function handleDelete() {
  await deleteAction.run(async () => {
    if (!resource.value) return
    await ElMessageBox.confirm(`确定要删除资源 "${resource.value.name}" 吗？`, '确认删除', {
      type: 'warning',
      confirmButtonText: '删除',
      cancelButtonText: '取消',
    })
    await resourceApi.remove(resourceId.value)
    ElMessage.success('资源已删除')
    router.push('/resources')
  })
}

const revealAction = useAction()

async function handleReveal() {
  await revealAction.run(async () => {
    if (revealed.value) {
      revealed.value = false
      revealedData.value = null
      ElMessage.info('已隐藏敏感信息')
    } else {
      const result = await resourceApi.reveal(resourceId.value)
      revealedData.value = result.attributes
      revealed.value = true
      ElMessage.success('敏感信息已解密')
    }
  })
}

const displayAttributes = computed(() => {
  if (!resource.value) return {}
  return revealed.value && revealedData.value
    ? revealedData.value
    : maskSecrets(resource.value.type, resource.value.attributes)
})

const secretKeys = computed(() => {
  return resource.value ? getSecretAttributes(resource.value.type) : []
})

function isSecretAttribute(key: string): boolean {
  return secretKeys.value.includes(key)
}

function getScopeLabel(res: Resource): string {
  return res.scope_type === 'platform' ? '平台' : `环境 (ID: ${res.scope_id})`
}

onMounted(load)
</script>

<template>
  <PageHeader v-if="resource" :title="resource.name">
    <template #actions>
      <el-button :icon="Refresh" @click="load" :loading="loading">刷新</el-button>
      <el-button
        v-if="resource.can_reveal"
        :icon="View"
        @click="handleReveal"
        :loading="revealAction.busy.value"
      >
        {{ revealed ? '隐藏' : '显示' }}敏感信息
      </el-button>
      <el-button
        v-if="resource.can_edit"
        type="primary"
        :icon="Edit"
        @click="router.push(`/resources/${resourceId}/edit`)"
      >
        编辑
      </el-button>
      <el-button
        v-if="resource.can_edit"
        type="danger"
        :icon="Delete"
        @click="handleDelete"
        :loading="deleteAction.busy.value"
      >
        删除
      </el-button>
    </template>
  </PageHeader>

  <el-card v-loading="loading">
    <template v-if="resource">
      <div style="margin-bottom: 16px">
        <el-tag>{{ getResourceTypeLabel(resource.type) }}</el-tag>
        <el-tag :type="resource.scope_type === 'platform' ? 'primary' : 'success'" style="margin-left: 8px">
          {{ resource.scope_type === 'platform' ? '平台' : '环境' }}
        </el-tag>
      </div>

      <el-tabs v-model="activeTab">
        <el-tab-pane label="基本信息" name="info">
          <el-descriptions :column="2" border>
            <el-descriptions-item label="资源名称">{{ resource.name }}</el-descriptions-item>
            <el-descriptions-item label="资源类型">
              {{ getResourceTypeLabel(resource.type) }}
            </el-descriptions-item>
            <el-descriptions-item label="当前版本">v{{ resource.version }}</el-descriptions-item>
            <el-descriptions-item label="使用情况">
              {{ resource.binding_count !== undefined ? `${resource.binding_count} 个服务` : '-' }}
            </el-descriptions-item>
            <el-descriptions-item label="作用域" :span="2">
              {{ getScopeLabel(resource) }}
            </el-descriptions-item>
            <el-descriptions-item label="描述" :span="2">
              {{ resource.description || '-' }}
            </el-descriptions-item>
            <el-descriptions-item label="创建时间">
              {{ fmtTime(resource.created_at) }}
            </el-descriptions-item>
            <el-descriptions-item label="更新时间">
              {{ fmtTime(resource.updated_at) }}
            </el-descriptions-item>
          </el-descriptions>

          <el-divider content-position="left">资源属性</el-divider>

          <el-descriptions :column="1" border>
            <el-descriptions-item
              v-for="(value, key) in displayAttributes"
              :key="key"
              :label="String(key)"
            >
              <el-text v-if="isSecretAttribute(String(key))" type="warning" tag="code">
                {{ value }}
              </el-text>
              <el-text v-else tag="code">{{ value }}</el-text>
            </el-descriptions-item>
          </el-descriptions>
        </el-tab-pane>

        <el-tab-pane label="版本历史" name="versions">
          <el-table :data="versions" stripe>
            <el-table-column prop="number" label="版本号" width="100" align="center">
              <template #default="{ row }">v{{ row.number }}</template>
            </el-table-column>
            <el-table-column prop="note" label="变更说明" min-width="200" />
            <el-table-column prop="checksum" label="Checksum" width="180">
              <template #default="{ row }">
                <el-text tag="code" size="small">{{ row.checksum.slice(0, 16) }}...</el-text>
              </template>
            </el-table-column>
            <el-table-column prop="created_at" label="创建时间" width="160">
              <template #default="{ row }">{{ fmtTime(row.created_at) }}</template>
            </el-table-column>
          </el-table>
        </el-tab-pane>
      </el-tabs>
    </template>
  </el-card>
</template>
