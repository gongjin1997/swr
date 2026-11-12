<script setup lang="ts">
/**
 * 服务列表：应用在各环境中的部署。显示服务方式（托管 / 纳管）、集群中的运行状态与就绪副本、未发布的配置。
 * 独立页面可筛选；嵌入项目工作台时固定为该项目。运行状态只读取当前页的服务（每个命名空间一次请求）。
 */
import { ArrowDown, Plus, Refresh, Search, Upload } from '@element-plus/icons-vue'
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { namespaceApi, projectApi, serviceApi } from '@/api'
import type { Namespace, Project, Service, ServiceStatusRow } from '@/api/types'
import DeployDialog from '@/components/DeployDialog.vue'
import PageHeader from '@/components/PageHeader.vue'
import TablePager from '@/components/TablePager.vue'
import { notifyError } from '@/composables/useAction'
import { usePagination } from '@/composables/usePagination'
import { RUNTIME_STATE_TAG, RUNTIME_STATE_TEXT, SERVICE_MODE_HINT, SERVICE_MODE_TEXT, TIER_TAG, TIER_TEXT } from '@/constants'
import { usePlatform } from '@/stores/platform'
import { useSession } from '@/stores/session'
import { shortImage } from '@/utils/format'

const props = defineProps<{ projectId?: number; embedded?: boolean }>()
const session = useSession()
const platform = usePlatform()
const router = useRouter()
const route = useRoute()
const services = ref<Service[]>([])
const projects = ref<Project[]>([])
const namespaces = ref<Namespace[]>([])
const status = ref<Record<number, ServiceStatusRow>>({})
const statusLoading = ref(false)
const loading = ref(false)
const keyword = ref('')
const filters = reactive({
  projectId: props.projectId ?? (Number(route.query.project) || null),
  appId: props.embedded ? null : (Number(route.query.app) || null),
  namespaceId: props.embedded ? null : (Number(route.query.namespace) || null),
  mode: '',
})
const deployTarget = ref<Service | null>(null)
const deployOpen = ref(false)
let statusSequence = 0

const filtered = computed(() => services.value.filter((s) =>
  (!filters.projectId || s.project_id === filters.projectId) && (!filters.appId || s.app_id === filters.appId) &&
  (!filters.namespaceId || s.namespace_id === filters.namespaceId) && (!filters.mode || s.mode === filters.mode) &&
  (!keyword.value.trim() || `${s.name} ${s.app_name} ${s.namespace_name} ${s.workload}`.toLowerCase()
    .includes(keyword.value.trim().toLowerCase()))))
const pager = usePagination(() => filtered.value, { size: 20, resetOn: () => [keyword.value, filters.projectId, filters.appId, filters.namespaceId, filters.mode] })
const appOptions = computed(() => platform.apps.filter((a) => !filters.projectId || a.project_id === filters.projectId))
const canCreate = computed(() => session.can('service.create') || session.can('service.config'))
const importTargets = computed(() => namespaces.value.filter((n) => session.canNamespace(n.id, 'service.create')))
const managedCount = computed(() => filtered.value.filter((s) => s.mode === 'managed').length)

async function load() {
  loading.value = true
  try {
    const empty = { items: [], total: 0 }
    const [s, p, n] = await Promise.all([
      serviceApi.list(filters.projectId ? { project_id: filters.projectId } : {}),
      projectApi.list().catch(() => empty),
      namespaceApi.list().catch(() => empty),
      session.can('app.view') ? platform.loadApps() : Promise.resolve([]),
    ])
    services.value = s.items
    projects.value = p.items as Project[]
    namespaces.value = n.items as Namespace[]
  } catch (e) {
    notifyError(e)
  } finally {
    loading.value = false
  }
}

/** 当前页服务的运行状态。读取失败只影响状态列。 */
async function loadStatus() {
  const ids = pager.rows.value.map((s) => s.id).filter((id) => !status.value[id])
  if (!ids.length) return
  const request = ++statusSequence
  statusLoading.value = true
  try {
    const result = await serviceApi.status(ids)
    if (request !== statusSequence) return
    status.value = { ...status.value, ...Object.fromEntries(result.items.map((row) => [row.service_id, row])) }
  } catch {
    /* 状态列显示“未读取” */
  } finally {
    if (request === statusSequence) statusLoading.value = false
  }
}

async function refresh() {
  status.value = {}
  await load()
  await loadStatus()
}

function openDeploy(service: Service) {
  deployTarget.value = service
  deployOpen.value = true
}

function create() {
  void router.push({ name: 'service-create', query: { project: filters.projectId ? String(filters.projectId) : undefined,
    app: filters.appId ? String(filters.appId) : undefined, namespace: filters.namespaceId ? String(filters.namespaceId) : undefined } })
}

function openImport(command: string | number | object) {
  void router.push({ name: 'environment-import', params: { id: Number(command) },
    query: { project: filters.projectId ? String(filters.projectId) : undefined } })
}

const rowStatus = (service: Service) => status.value[service.id]

watch(() => pager.rows.value.map((s) => s.id).join(','), () => { void loadStatus() })
watch(() => [filters.projectId, filters.appId, filters.namespaceId], () => {
  if (!props.embedded) {
    void router.replace({ query: { project: filters.projectId ? String(filters.projectId) : undefined,
      app: filters.appId ? String(filters.appId) : undefined, namespace: filters.namespaceId ? String(filters.namespaceId) : undefined } })
  }
})
onMounted(async () => {
  await load()
  await loadStatus()
})
</script>

<template>
  <div :class="{ page: !embedded }">
    <PageHeader v-if="!embedded" title="服务" description="服务是应用在一个环境中的部署。托管服务由平台生成并下发全部对象；纳管服务关联已有的 Deployment，发布时只更新镜像。">
      <el-button :icon="Refresh" :loading="loading" @click="refresh">刷新</el-button>
      <el-dropdown v-if="importTargets.length" trigger="click" @command="openImport">
        <el-button :icon="Upload">导入已有服务<el-icon class="el-icon--right"><ArrowDown /></el-icon></el-button>
        <template #dropdown>
          <el-dropdown-menu>
            <el-dropdown-item v-for="n in importTargets" :key="n.id" :command="n.id">{{ n.cluster_name }} / {{ n.name }}（{{ TIER_TEXT[n.tier] }}）</el-dropdown-item>
          </el-dropdown-menu>
        </template>
      </el-dropdown>
      <el-button v-if="canCreate" type="primary" :icon="Plus" @click="create">新建服务</el-button>
    </PageHeader>
    <el-card>
      <div class="filter-bar">
        <el-input v-model="keyword" :prefix-icon="Search" placeholder="搜索服务、应用、环境或工作负载" clearable aria-label="搜索服务" />
        <el-select v-if="!embedded" v-model="filters.projectId" clearable filterable placeholder="全部项目" aria-label="按项目筛选">
          <el-option v-for="p in projects" :key="p.id" :label="p.name" :value="p.id" />
        </el-select>
        <el-select v-model="filters.appId" clearable filterable placeholder="全部应用" aria-label="按应用筛选">
          <el-option v-for="a in appOptions" :key="a.id" :label="a.name" :value="a.id" />
        </el-select>
        <el-select v-if="!embedded" v-model="filters.namespaceId" clearable filterable placeholder="全部环境" aria-label="按环境筛选">
          <el-option v-for="n in namespaces" :key="n.id" :label="`${n.cluster_name} / ${n.name}`" :value="n.id" />
        </el-select>
        <el-select v-model="filters.mode" clearable placeholder="全部方式" aria-label="按服务方式筛选" class="mode-select">
          <el-option label="托管" value="managed" /><el-option label="纳管" value="image_only" />
        </el-select>
        <span class="count">共 {{ filtered.length }} 个服务，托管 {{ managedCount }} 个</span>
        <el-button v-if="embedded && canCreate" type="primary" :icon="Plus" @click="create">新建服务</el-button>
      </div>
      <el-table v-loading="loading" :data="pager.rows.value" empty-text="暂无可见服务">
        <el-table-column label="服务" min-width="170">
          <template #default="{ row }">
            <RouterLink :to="{ name: 'service-detail', params: { id: row.id } }"><b>{{ row.name }}</b></RouterLink>
            <el-tooltip :content="SERVICE_MODE_HINT[row.mode]">
              <el-tag size="small" :type="row.mode === 'managed' ? 'primary' : 'info'" effect="plain" class="gap">{{ SERVICE_MODE_TEXT[row.mode] }}</el-tag>
            </el-tooltip>
            <div v-if="row.workload && row.workload !== row.name" class="hint mono">{{ row.workload }}</div>
          </template>
        </el-table-column>
        <el-table-column label="项目 / 应用" min-width="140">
          <template #default="{ row }">
            <RouterLink v-if="!embedded" :to="{ name: 'project-detail', params: { id: row.project_id } }">{{ row.project_name }}</RouterLink>
            <div :class="{ hint: !embedded }">{{ row.app_name }}</div>
          </template>
        </el-table-column>
        <el-table-column label="环境" min-width="170">
          <template #default="{ row }">
            <el-tag size="small" :type="TIER_TAG[row.tier]" effect="plain">{{ TIER_TEXT[row.tier] }}</el-tag>
            <span class="hint gap">{{ row.cluster_name }} / {{ row.namespace_name }}</span>
          </template>
        </el-table-column>
        <el-table-column label="运行状态" min-width="170">
          <template #default="{ row }">
            <template v-if="rowStatus(row)">
              <el-tooltip :content="rowStatus(row)?.message" placement="top">
                <el-tag size="small" :type="RUNTIME_STATE_TAG[rowStatus(row)?.state ?? 'unknown']">{{ RUNTIME_STATE_TEXT[rowStatus(row)?.state ?? 'unknown'] }}</el-tag>
              </el-tooltip>
              <span v-if="rowStatus(row)?.desired !== undefined && !['missing', 'unknown'].includes(rowStatus(row)?.state ?? '')"
                class="num gap">{{ rowStatus(row)?.ready }}/{{ rowStatus(row)?.desired }}</span>
              <div v-if="rowStatus(row)?.image" class="hint mono break-word">{{ shortImage(rowStatus(row)?.image?.split('@')[0]) }}</div>
            </template>
            <span v-else class="hint">{{ statusLoading ? '读取中…' : '未读取' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="配置" width="120">
          <template #default="{ row }">
            <el-tag v-if="!row.deployed_revision_id" size="small" type="info" effect="plain">尚未发布</el-tag>
            <el-tag v-else-if="row.deployed_revision_id !== row.current_revision_id || row.config_status?.pending" size="small" type="warning">有未发布配置</el-tag>
            <span v-else class="hint">已同步</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="110" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" @click="router.push({ name: 'service-detail', params: { id: row.id } })">详情</el-button>
            <el-button v-if="session.deployEnabled && session.canNamespace(row.namespace_id, 'service.deploy')" link type="primary" @click="openDeploy(row)">发布</el-button>
          </template>
        </el-table-column>
      </el-table>
      <TablePager v-model:page="pager.page.value" v-model:size="pager.size.value" :total="pager.total.value" />
    </el-card>

    <DeployDialog v-model="deployOpen" :service="deployTarget" :prefer-latest="!!deployTarget?.config_status?.pending" @submitted="refresh" />
  </div>
</template>

<style scoped>
.gap { margin-left: 6px; }
.filter-bar .mode-select { width: 130px; }
</style>
