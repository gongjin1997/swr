<script setup lang="ts">
/** 环境最终生效的配置：每个键的值与来源层（平台 / 项目 / 环境），以及被它覆盖的上层。 */
import { computed, onMounted, ref, watch } from 'vue'
import { environmentApi } from '@/api'
import type { ConfigKind, ConfigOrigin, EffectiveConfig, EffectiveEntry } from '@/api/types'
import { notifyError } from '@/composables/useAction'
import { CONFIG_KIND_TEXT, CONFIG_SCOPE_TAG, CONFIG_SCOPE_TEXT } from '@/constants'
import { useSession } from '@/stores/session'

const props = defineProps<{ namespaceId: number; projectIds: number[] }>()
const session = useSession()
const projectId = ref<number | null>(props.projectIds[0] ?? null)
const data = ref<EffectiveConfig | null>(null)
const loading = ref(false)
const rows = computed<(EffectiveEntry & { secretRow: boolean })[]>(() => [
  ...(data.value?.env ?? []).map((e) => ({ ...e, secretRow: false })),
  ...(data.value?.secret_env ?? []).map((e) => ({ ...e, secretRow: true })),
])

const originText = (origin: ConfigOrigin) => `${CONFIG_SCOPE_TEXT[origin.scope_type]} v${origin.version}`
const overrideText = (row: EffectiveEntry) => row.overrides.map((o) => CONFIG_SCOPE_TEXT[o.scope_type]).join('、') || '-'
const kindText = (kind: ConfigKind) => CONFIG_KIND_TEXT[kind]

async function load() {
  loading.value = true
  try {
    data.value = await environmentApi.effective(props.namespaceId, projectId.value)
  } catch (e) {
    notifyError(e)
  } finally {
    loading.value = false
  }
}

watch(projectId, load)
onMounted(load)
defineExpose({ reload: load })
</script>

<template>
  <div v-loading="loading">
    <div class="filter-bar">
      <el-select v-model="projectId" clearable placeholder="不叠加项目层" aria-label="叠加项目层" class="project-select">
        <el-option v-for="id in projectIds" :key="id" :label="`叠加项目：${session.projectName(id)}`" :value="id" />
      </el-select>
      <span class="hint">平台层 → 项目层 → 环境层逐层覆盖；服务自己的配置在新建服务时再叠加。</span>
    </div>
    <el-alert v-for="w in data?.warnings ?? []" :key="w" type="warning" :title="w" :closable="false" class="warning" />
    <el-table :data="rows" size="small" empty-text="没有环境变量">
      <el-table-column label="键" min-width="220">
        <template #default="{ row }">
          <span class="mono">{{ row.key }}</span>
          <el-tag v-if="row.locked" size="small" type="warning" effect="plain" class="tag-gap">锁定</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="值" min-width="280">
        <template #default="{ row }">
          <span v-if="!row.secretRow" class="mono break-word">{{ row.value }}</span>
          <span v-else-if="row.source === 'certificate'" class="hint">敏感 · 证书 {{ row.certificate_name }} v{{ row.certificate_version }}</span>
          <span v-else class="hint">敏感 · 已设置</span>
        </template>
      </el-table-column>
      <el-table-column label="来源" width="110">
        <template #default="{ row }">
          <el-tag size="small" :type="CONFIG_SCOPE_TAG[row.origin.scope_type]" effect="plain">{{ originText(row.origin) }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="覆盖了" width="140">
        <template #default="{ row }">
          <span class="hint">{{ overrideText(row) }}</span>
        </template>
      </el-table-column>
    </el-table>

    <h4>配置文件与敏感文件</h4>
    <el-table :data="data?.files ?? []" size="small" empty-text="没有配置文件">
      <el-table-column label="名称" min-width="200"><template #default="{ row }"><span class="mono">{{ row.name }}</span></template></el-table-column>
      <el-table-column label="类型" width="110"><template #default="{ row }">{{ kindText(row.kind) }}</template></el-table-column>
      <el-table-column label="文件" min-width="260"><template #default="{ row }"><span class="mono break-word">{{ row.keys.join('、') }}</span></template></el-table-column>
      <el-table-column label="来源" width="110">
        <template #default="{ row }">
          <el-tag size="small" :type="CONFIG_SCOPE_TAG[row.origin.scope_type]" effect="plain">{{ originText(row.origin) }}</el-tag>
        </template>
      </el-table-column>
    </el-table>
  </div>
</template>

<style scoped>
.project-select { width: 260px; }
.warning { margin-bottom: 8px; }
.tag-gap { margin-left: 6px; }
h4 { margin: 18px 0 8px; }
</style>
