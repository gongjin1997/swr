<script setup lang="ts">
/** 服务列表：应用部署到命名空间的 Deployment。独立页面可筛选；嵌入项目工作台时固定为该项目。 */
import { Plus, Refresh, Search } from '@element-plus/icons-vue'
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { namespaceApi, projectApi, serviceApi } from '@/api'
import type { Namespace, NamespaceResources, Project, Service } from '@/api/types'
import DeployDialog from '@/components/DeployDialog.vue'
import PageHeader from '@/components/PageHeader.vue'
import { notifyError, useAction } from '@/composables/useAction'
import { TIER_TAG, TIER_TEXT } from '@/constants'
import { usePlatform } from '@/stores/platform'
import { useSession } from '@/stores/session'

const props = defineProps<{ projectId?: number; embedded?: boolean }>()
const session = useSession()
const platform = usePlatform()
const router = useRouter()
const route = useRoute()
const services = ref<Service[]>([])
const projects = ref<Project[]>([])
const namespaces = ref<Namespace[]>([])
const loading = ref(false)
const keyword = ref('')
const filters = reactive({
  projectId: props.projectId ?? (Number(route.query.project) || null),
  appId: props.embedded ? null : (Number(route.query.app) || null),
  namespaceId: props.embedded ? null : (Number(route.query.namespace) || null),
})
const { busy, run } = useAction()
const deployTarget = ref<Service | null>(null)
const deployOpen = ref(false)

const filtered = computed(() => services.value.filter((s) =>
  (!filters.projectId || s.project_id === filters.projectId) && (!filters.appId || s.app_id === filters.appId) &&
  (!filters.namespaceId || s.namespace_id === filters.namespaceId) &&
  (!keyword.value.trim() || `${s.name} ${s.app_name} ${s.namespace_name}`.toLowerCase().includes(keyword.value.trim().toLowerCase()))))
const appOptions = computed(() => platform.apps.filter((a) => !filters.projectId || a.project_id === filters.projectId))
const canCreate = computed(() => session.can('service.config'))

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

function openDeploy(service: Service) {
  deployTarget.value = service
  deployOpen.value = true
}

// ------------------------------------------------------------------ 新建服务
const creator = reactive({ visible: false, name: '', projectId: null as number | null, appId: null as number | null,
  namespaceId: null as number | null, deployment: '', container: '' })
const discovered = ref<NamespaceResources | null>(null)
const discovering = ref(false)
const resourceError = ref('')
let resourceSequence = 0
const creatorProjects = computed(() => projects.value.filter((p) => p.namespace_ids.some((id) => session.canNamespace(id, 'service.config'))))
const creatorApps = computed(() => platform.apps.filter((a) => a.project_id === creator.projectId))
const creatorNamespaces = computed(() => {
  const project = projects.value.find((p) => p.id === creator.projectId)
  return namespaces.value.filter((n) => project?.namespace_ids.includes(n.id) && session.canNamespace(n.id, 'service.config'))
})
const containers = computed(() => discovered.value?.deployments.find((d) => d.name === creator.deployment)?.containers ?? [])

function openCreator() {
  const preferred = creatorProjects.value.find((p) => p.id === filters.projectId) ?? creatorProjects.value[0]
  if (!preferred) return notifyError(new Error('需要一个已分配命名空间、且你在该命名空间有服务配置权限的项目'))
  Object.assign(creator, { visible: true, name: '', projectId: preferred.id, appId: filters.appId, namespaceId: null, deployment: '', container: '' })
}

function changeProject() {
  creator.appId = null
  creator.namespaceId = null
}

async function create() {
  if (!creator.projectId || !creator.appId || !creator.namespaceId) return notifyError(new Error('请选择项目、应用和命名空间'))
  const result: { id?: number } = {}
  const ok = await run(async () => {
    const created = await serviceApi.create({
      name: creator.name.trim(), project_id: creator.projectId as number, namespace_id: creator.namespaceId as number,
      app_id: creator.appId as number,
      spec: { mode: 'image_only', target: { kind: 'Deployment', name: creator.deployment.trim(), container: creator.container.trim() } },
    })
    result.id = created.id
  }, '服务已创建')
  if (ok && result.id) {
    creator.visible = false
    await router.push({ name: 'service-detail', params: { id: result.id } })
  }
}

watch(() => [creator.namespaceId, creator.visible] as const, async ([id, visible]) => {
  const request = ++resourceSequence
  discovered.value = null
  creator.deployment = ''
  creator.container = ''
  resourceError.value = ''
  if (!id || !visible) {
    discovering.value = false
    return
  }
  discovering.value = true
  try {
    const result = await namespaceApi.resources(id)
    if (request === resourceSequence) discovered.value = result
  } catch (e) {
    if (request === resourceSequence) resourceError.value = e instanceof Error ? e.message : '读取集群资源失败'
  } finally {
    if (request === resourceSequence) discovering.value = false
  }
})
watch(() => creator.deployment, () => { creator.container = containers.value[0]?.name ?? '' })
watch(() => [filters.projectId, filters.appId, filters.namespaceId], () => {
  if (!props.embedded) {
    void router.replace({ query: { project: filters.projectId ? String(filters.projectId) : undefined,
      app: filters.appId ? String(filters.appId) : undefined, namespace: filters.namespaceId ? String(filters.namespaceId) : undefined } })
  }
})
onMounted(load)
</script>

<template>
  <div :class="{ page: !embedded }">
    <PageHeader v-if="!embedded" title="服务" description="服务把项目的应用关联到已分配命名空间中的 Deployment，发布时固定镜像摘要与配置修订。">
      <el-button :icon="Refresh" :loading="loading" @click="load">刷新</el-button>
      <el-button v-if="canCreate" type="primary" :icon="Plus" @click="openCreator">创建服务</el-button>
    </PageHeader>
    <el-card>
      <div class="filter-bar">
        <el-input v-model="keyword" :prefix-icon="Search" placeholder="搜索服务、应用或命名空间" clearable aria-label="搜索服务" />
        <el-select v-if="!embedded" v-model="filters.projectId" clearable filterable placeholder="全部项目" aria-label="按项目筛选">
          <el-option v-for="p in projects" :key="p.id" :label="p.name" :value="p.id" />
        </el-select>
        <el-select v-model="filters.appId" clearable filterable placeholder="全部应用" aria-label="按应用筛选">
          <el-option v-for="a in appOptions" :key="a.id" :label="a.name" :value="a.id" />
        </el-select>
        <el-select v-if="!embedded" v-model="filters.namespaceId" clearable filterable placeholder="全部命名空间" aria-label="按命名空间筛选">
          <el-option v-for="n in namespaces" :key="n.id" :label="`${n.cluster_name} / ${n.name}`" :value="n.id" />
        </el-select>
        <span class="count">共 {{ filtered.length }} 个服务</span>
        <el-button v-if="embedded && canCreate" type="primary" :icon="Plus" @click="openCreator">创建服务</el-button>
      </div>
      <el-table v-loading="loading" :data="filtered" empty-text="暂无可见服务">
        <el-table-column label="服务" min-width="170">
          <template #default="{ row }">
            <RouterLink :to="{ name: 'service-detail', params: { id: row.id } }"><b>{{ row.name }}</b></RouterLink>
            <div class="hint mono">{{ row.spec?.target?.name ?? row.spec?.mode }}</div>
          </template>
        </el-table-column>
        <el-table-column label="项目 / 应用" min-width="180">
          <template #default="{ row }">
            <RouterLink v-if="!embedded" :to="{ name: 'project-detail', params: { id: row.project_id } }">{{ row.project_name }}</RouterLink>
            <div :class="{ hint: !embedded }">{{ row.app_name }}</div>
          </template>
        </el-table-column>
        <el-table-column label="环境" min-width="200">
          <template #default="{ row }">
            <el-tag size="small" :type="TIER_TAG[row.tier]" effect="plain">{{ TIER_TEXT[row.tier] }}</el-tag>
            <span class="hint gap">{{ row.cluster_name }} / {{ row.namespace_name }}</span>
          </template>
        </el-table-column>
        <el-table-column label="配置 / 已部署修订" width="150">
          <template #default="{ row }">
            <span class="mono">{{ row.current_revision_id }} / {{ row.deployed_revision_id ?? '-' }}</span>
            <el-tag v-if="row.deployed_revision_id && row.deployed_revision_id !== row.current_revision_id" size="small" type="warning" class="gap">有未发布配置</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="150" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" @click="router.push({ name: 'service-detail', params: { id: row.id } })">详情</el-button>
            <el-button v-if="session.deployEnabled && session.canNamespace(row.namespace_id, 'service.deploy')" link type="primary" @click="openDeploy(row)">发布</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-dialog v-model="creator.visible" title="创建服务" width="640px" :close-on-click-modal="false">
      <el-form label-position="top" @submit.prevent="create">
        <el-form-item label="服务名称" required><el-input v-model="creator.name" maxlength="63" placeholder="小写字母、数字和 -" /></el-form-item>
        <div class="form-grid">
          <el-form-item label="项目">
            <el-select v-model="creator.projectId" :disabled="!!projectId" @change="changeProject">
              <el-option v-for="p in creatorProjects" :key="p.id" :label="p.name" :value="p.id" />
            </el-select>
          </el-form-item>
          <el-form-item label="应用">
            <el-select v-model="creator.appId" filterable placeholder="选择项目内的应用">
              <el-option v-for="a in creatorApps" :key="a.id" :label="a.name" :value="a.id" />
            </el-select>
          </el-form-item>
          <el-form-item label="命名空间">
            <el-select v-model="creator.namespaceId" placeholder="项目已分配的命名空间">
              <el-option v-for="n in creatorNamespaces" :key="n.id" :label="`${n.cluster_name} / ${n.name}（${TIER_TEXT[n.tier]}）`" :value="n.id" />
            </el-select>
          </el-form-item>
          <el-form-item label="Deployment">
            <el-select v-model="creator.deployment" filterable :loading="discovering" :disabled="!creator.namespaceId" placeholder="从集群读取工作负载">
              <el-option v-for="d in discovered?.deployments ?? []" :key="d.name" :label="`${d.name}（${d.ready_replicas}/${d.replicas} 就绪）`" :value="d.name" />
            </el-select>
          </el-form-item>
          <el-form-item label="容器">
            <el-select v-model="creator.container" :disabled="!creator.deployment" placeholder="选择目标容器">
              <el-option v-for="c in containers" :key="c.name" :label="c.name" :value="c.name" />
            </el-select>
          </el-form-item>
        </div>
        <el-alert v-if="resourceError" type="error" :closable="false" :title="resourceError" />
        <p v-if="creator.container" class="hint mono break-word">当前镜像：{{ containers.find((c) => c.name === creator.container)?.image }}</p>
      </el-form>
      <template #footer>
        <el-button @click="creator.visible = false">取消</el-button>
        <el-button type="primary" :loading="busy" :disabled="discovering || !creator.deployment || !creator.container" @click="create">创建服务</el-button>
      </template>
    </el-dialog>

    <DeployDialog v-model="deployOpen" :service="deployTarget" @submitted="load" />
  </div>
</template>

<style scoped>
.gap { margin-left: 6px; }
</style>
