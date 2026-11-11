<script setup lang="ts">
/** 比较两个环境的全部生效参数：环境变量、敏感变量（只比较是否相同）、配置文件（properties 按键比较）。 */
import { computed, ref } from 'vue'
import { environmentApi } from '@/api'
import type { CompareRow, CompareStatus, EnvironmentCompare, EnvironmentSummary } from '@/api/types'
import { useAction } from '@/composables/useAction'
import { COMPARE_TAG, COMPARE_TEXT, TIER_TEXT } from '@/constants'
import { useSession } from '@/stores/session'

const props = defineProps<{ namespaceId: number; namespaceName: string; environments: EnvironmentSummary[]; projectIds: number[] }>()
const session = useSession()
const others = computed(() => props.environments.filter((e) => e.id !== props.namespaceId))
const rightId = ref<number | null>(others.value[0]?.id ?? null)
const projectId = ref<number | null>(props.projectIds[0] ?? null)
const onlyDiff = ref(true)
const result = ref<EnvironmentCompare | null>(null)
const { busy, run } = useAction()

const rightName = computed(() => others.value.find((e) => e.id === rightId.value)?.name ?? '')
const visible = (rows: CompareRow[]) => (onlyDiff.value ? rows.filter((r) => r.status !== 'same') : rows)
const files = computed(() => (result.value?.files ?? []).filter((f) => !onlyDiff.value || f.status !== 'same'))
const statusText = (status: CompareStatus | '') => (status ? COMPARE_TEXT[status] : '')
const statusTag = (status: CompareStatus | '') => (status ? COMPARE_TAG[status] : 'info')

async function compare() {
  const right = rightId.value
  if (!right) return
  await run(async () => {
    result.value = await environmentApi.compare(props.namespaceId, right, projectId.value)
  })
}
</script>

<template>
  <div>
    <div class="filter-bar">
      <span>与</span>
      <el-select v-model="rightId" filterable placeholder="选择另一个环境" aria-label="比较的环境" class="env-select">
        <el-option v-for="e in others" :key="e.id" :label="`${e.cluster_name} / ${e.name}（${TIER_TEXT[e.tier]}）`" :value="e.id" />
      </el-select>
      <el-select v-model="projectId" clearable placeholder="不叠加项目层" aria-label="叠加项目层" class="env-select">
        <el-option v-for="id in projectIds" :key="id" :label="`叠加项目：${session.projectName(id)}`" :value="id" />
      </el-select>
      <el-switch v-model="onlyDiff" active-text="只看差异" />
      <el-button type="primary" :loading="busy" :disabled="!rightId" @click="compare">比较</el-button>
    </div>
    <el-empty v-if="!others.length" description="没有其他可见的环境" />
    <template v-if="result">
      <h4>环境变量</h4>
      <el-table :data="visible(result.env)" size="small" empty-text="没有差异">
        <el-table-column prop="key" label="键" min-width="200" />
        <el-table-column :label="namespaceName" min-width="220">
          <template #default="{ row }"><span class="mono break-word">{{ row.left ?? '—' }}</span></template>
        </el-table-column>
        <el-table-column :label="rightName" min-width="220">
          <template #default="{ row }"><span class="mono break-word">{{ row.right ?? '—' }}</span></template>
        </el-table-column>
        <el-table-column label="结果" width="100">
          <template #default="{ row }"><el-tag size="small" :type="statusTag(row.status)">{{ statusText(row.status) }}</el-tag></template>
        </el-table-column>
      </el-table>

      <h4>敏感环境变量</h4>
      <el-table :data="visible(result.secret_env)" size="small" empty-text="没有差异">
        <el-table-column prop="key" label="键" min-width="200" />
        <el-table-column label="结果" width="120">
          <template #default="{ row }"><el-tag size="small" :type="statusTag(row.status)">{{ statusText(row.status) }}</el-tag></template>
        </el-table-column>
      </el-table>

      <h4>配置文件与敏感文件</h4>
      <el-empty v-if="!files.length" description="没有差异" :image-size="60" />
      <el-collapse v-else>
        <el-collapse-item v-for="set in files" :key="set.name" :name="set.name">
          <template #title>
            <span class="mono">{{ set.name }}</span>
            <el-tag size="small" class="tag-gap" :type="statusTag(set.status)">{{ statusText(set.status) }}</el-tag>
          </template>
          <div v-for="file in set.files.filter((f) => !onlyDiff || f.status !== 'same')" :key="file.key" class="file-block">
            <div><span class="mono">{{ file.key }}</span>
              <el-tag size="small" class="tag-gap" :type="statusTag(file.status)">{{ statusText(file.status) }}</el-tag></div>
            <el-table v-if="file.properties?.length" :data="file.properties" size="small">
              <el-table-column prop="key" label="属性" min-width="200" />
              <el-table-column :label="namespaceName" min-width="200">
                <template #default="{ row }"><span class="mono break-word">{{ row.left ?? '—' }}</span></template>
              </el-table-column>
              <el-table-column :label="rightName" min-width="200">
                <template #default="{ row }"><span class="mono break-word">{{ row.right ?? '—' }}</span></template>
              </el-table-column>
            </el-table>
          </div>
        </el-collapse-item>
      </el-collapse>
    </template>
  </div>
</template>

<style scoped>
.env-select { width: 280px; }
.tag-gap { margin-left: 8px; }
.file-block + .file-block { margin-top: 12px; }
h4 { margin: 18px 0 8px; }
</style>
