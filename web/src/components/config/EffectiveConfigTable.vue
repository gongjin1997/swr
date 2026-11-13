<script setup lang="ts">
/**
 * 最终生效的配置：每个键的值与来源层（平台 / 项目 / 环境 / 服务），以及被它覆盖的上层。
 * 传入 serviceId 时显示该服务的生效配置（含服务层），否则显示环境（可选叠加一个项目）的生效配置。
 */
import { Search } from '@element-plus/icons-vue'
import { computed, onMounted, ref, watch } from 'vue'
import { environmentApi, serviceApi } from '@/api'
import type { ConfigKind, ConfigOrigin, EffectiveConfig, EffectiveEntry } from '@/api/types'
import TablePager from '@/components/TablePager.vue'
import { notifyError } from '@/composables/useAction'
import { usePagination } from '@/composables/usePagination'
import { CONFIG_KIND_TEXT, CONFIG_SCOPE_TAG, CONFIG_SCOPE_TEXT } from '@/constants'
import { useSession } from '@/stores/session'

const props = defineProps<{ namespaceId: number; projectIds: number[]; serviceId?: number }>()
const session = useSession()
const projectId = ref<number | null>(props.projectIds[0] ?? null)
const data = ref<EffectiveConfig | null>(null)
const loading = ref(false)
const keyword = ref('')
const rows = computed<(EffectiveEntry & { secretRow: boolean })[]>(() => [
  ...(data.value?.env ?? []).map((e) => ({ ...e, secretRow: false })),
  ...(data.value?.secret_env ?? []).map((e) => ({ ...e, secretRow: true })),
].filter((row) => !keyword.value.trim() || row.key.toLowerCase().includes(keyword.value.trim().toLowerCase())))
const pager = usePagination(() => rows.value, { size: 20, resetOn: () => keyword.value })

const originTag = (origin: ConfigOrigin) => CONFIG_SCOPE_TAG[origin.scope_type]
const originText = (origin: ConfigOrigin) => `${CONFIG_SCOPE_TEXT[origin.scope_type]} v${origin.version}`
const overrideText = (row: EffectiveEntry) => row.overrides.map((o) => CONFIG_SCOPE_TEXT[o.scope_type]).join('、') || '—'
const kindText = (kind: ConfigKind) => CONFIG_KIND_TEXT[kind]

async function load() {
  loading.value = true
  try {
    data.value = props.serviceId ? await serviceApi.effective(props.serviceId)
      : await environmentApi.effective(props.namespaceId, projectId.value)
  } catch (e) {
    notifyError(e)
  } finally {
    loading.value = false
  }
}

watch(projectId, load)
watch(() => props.serviceId, load)
onMounted(load)
defineExpose({ reload: load })
</script>

<template>
  <div v-loading="loading">
    <div class="filter-bar">
      <el-input v-model="keyword" :prefix-icon="Search" clearable placeholder="搜索键" aria-label="搜索键" class="key-search" />
      <el-select v-if="!serviceId" v-model="projectId" clearable placeholder="不叠加项目层" aria-label="叠加项目层" class="project-select">
        <el-option v-for="id in projectIds" :key="id" :label="`叠加项目：${session.projectName(id)}`" :value="id" />
      </el-select>
      <span class="hint">{{ serviceId ? '平台层 → 项目层 → 环境层 → 服务层逐层覆盖，上层锁定的键下层不能覆盖。'
        : '平台层 → 项目层 → 环境层逐层覆盖；服务自己的配置在服务中维护。' }}</span>
    </div>
    <el-alert v-for="w in data?.warnings ?? []" :key="w" type="warning" :title="w" :closable="false" class="warning" />
    <el-table :data="pager.rows.value" size="small" empty-text="没有环境变量">
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
          <el-tag size="small" :type="originTag(row.origin)" effect="plain">{{ originText(row.origin) }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="覆盖了" width="140">
        <template #default="{ row }">
          <span class="hint">{{ overrideText(row) }}</span>
        </template>
      </el-table-column>
    </el-table>
    <TablePager v-model:page="pager.page.value" v-model:size="pager.size.value" :total="pager.total.value" compact />

    <h4>配置文件与敏感文件</h4>
    <el-table :data="data?.files ?? []" size="small" empty-text="没有配置文件">
      <el-table-column label="名称" min-width="200"><template #default="{ row }"><span class="mono">{{ row.name }}</span></template></el-table-column>
      <el-table-column label="类型" width="110"><template #default="{ row }">{{ kindText(row.kind) }}</template></el-table-column>
      <el-table-column label="文件" min-width="260"><template #default="{ row }"><span class="mono break-word">{{ row.keys.join('、') }}</span></template></el-table-column>
      <el-table-column label="来源" width="110">
        <template #default="{ row }">
          <el-tag size="small" :type="originTag(row.origin)" effect="plain">{{ originText(row.origin) }}</el-tag>
        </template>
      </el-table-column>
    </el-table>
  </div>
</template>

<style scoped>
.project-select { width: 260px; }
.key-search { width: 220px; }
.warning { margin-bottom: 8px; }
.tag-gap { margin-left: 6px; }
h4 { margin: 18px 0 8px; }
</style>
