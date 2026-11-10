<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { namespaceApi, projectApi, serviceApi } from '@/api'
import type { Namespace, Project, Service, NamespaceResources } from '@/api/types'
import DeployDialog from '@/components/DeployDialog.vue'
import { notifyError, useAction } from '@/composables/useAction'
import { usePlatform } from '@/stores/platform'
import { useSession } from '@/stores/session'

const session = useSession()
const platform = usePlatform()
const router = useRouter()
const route = useRoute()
const discovered = ref<NamespaceResources | null>(null)
const discovering = ref(false)
const resourceError = ref('')
let resourceSequence = 0
const filtered = computed(() => services.value.filter(s =>
  (!route.query.project || s.project_id === Number(route.query.project)) &&
  (!route.query.app || s.app_id === Number(route.query.app)) &&
  (!route.query.namespace || s.namespace_id === Number(route.query.namespace))))
const services = ref<Service[]>([])
const projects = ref<Project[]>([])
const namespaces = ref<Namespace[]>([])
const loading = ref(false)
const { busy, run } = useAction()
const deployTarget = ref<Service | null>(null)
const deployOpen = ref(false)
const creator = reactive({ visible: false, name: '', projectId: null as number | null, appId: null as number | null,
  namespaceId: null as number | null, deployment: '', container: '' })

const projectApps = computed(() => platform.apps.filter((a) => a.project_id === creator.projectId))
const projectNamespaces = computed(() => {
  const project = projects.value.find((p) => p.id === creator.projectId)
  return namespaces.value.filter((n) => project?.namespace_ids.includes(n.id))
})

async function load() {
  loading.value = true
  try {
    const empty = { items: [], total: 0 }
    const [s, p, n] = await Promise.all([
      serviceApi.list(),
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

function openCreator() {
  const project = projects.value.find(p => p.id === Number(route.query.project)) ?? projects.value.find((p) => p.namespace_ids.length)
  if (!project || !platform.apps.length) return notifyError(new Error('需要可见项目、应用和已分配的命名空间才能创建服务'))
  Object.assign(creator, { visible: true, name: '', projectId: project.id, appId: Number(route.query.app) || null, namespaceId: Number(route.query.namespace) || null, deployment: '', container: '' })
}

async function create() {
  if (!creator.projectId || !creator.appId || !creator.namespaceId) return notifyError(new Error('请选择项目、应用和命名空间'))
  let createdId = 0
  const ok = await run(async () => {
    const created = await serviceApi.create({
      name: creator.name.trim(), project_id: creator.projectId!, namespace_id: creator.namespaceId!, app_id: creator.appId!,
      spec: { mode: 'image_only', target: { kind: 'Deployment', name: creator.deployment.trim(), container: creator.container.trim() } },
    })
    createdId = created.id
  }, '服务已创建')
  if (ok) {
    creator.visible = false
    await router.push({ name: 'service-detail', params: { id: createdId } })
  }
}

function openDeploy(service: Service) {
  deployTarget.value = service
  deployOpen.value = true
}

watch(() => [creator.namespaceId, creator.visible] as const, async ([id, visible]) => {
  const request = ++resourceSequence
  discovered.value = null
  creator.deployment = ''
  creator.container = ''
  resourceError.value = ''
  if (!id || !visible) { discovering.value = false; return }
  discovering.value = true
  try {
    const result = await namespaceApi.resources(id)
    if (request === resourceSequence) discovered.value = result
  } catch (e) { if (request === resourceSequence) resourceError.value = e instanceof Error ? e.message : '读取集群资源失败' }
  finally { if (request === resourceSequence) discovering.value = false }
})
const containers = computed(() => discovered.value?.deployments.find(d => d.name === creator.deployment)?.containers ?? [])
watch(() => creator.deployment, () => { creator.container = containers.value[0]?.name ?? '' })
onMounted(load)
</script>

<template>
  <el-card>
    <template #header>
      <div class="card-title">
        <span>服务</span>
        <div class="toolbar"><el-button v-if="route.query.project || route.query.app || route.query.namespace" @click="router.push({ name: 'services' })">全部服务</el-button><RouterLink :to="{ name: 'deploy', query: route.query }"><el-button>发布记录与审批</el-button></RouterLink></div>
        <el-button v-if="session.can('service.config')" type="primary" @click="openCreator">创建服务</el-button>
      </div>
    </template>
    <el-table v-loading="loading" :data="filtered" empty-text="暂无可见服务">
      <el-table-column label="服务" min-width="160">
        <template #default="{ row }">
          <el-button link type="primary" @click="router.push({ name: 'service-detail', params: { id: row.id } })">{{ row.name }}</el-button>
          <div class="hint">{{ row.spec?.mode }}</div>
        </template>
      </el-table-column>
      <el-table-column label="项目 / 应用" min-width="180">
        <template #default="{ row }">
          <RouterLink :to="{ name: 'apps', query: { project: row.project_id } }">{{ row.project_name }}</RouterLink>
          <div class="hint">{{ row.app_name }}</div>
        </template>
      </el-table-column>
      <el-table-column label="命名空间" min-width="180">
        <template #default="{ row }">{{ row.cluster_name }} / {{ row.namespace_name }}</template>
      </el-table-column>
      <el-table-column label="配置修订" width="100"><template #default="{ row }"><span class="mono">{{ row.current_revision_id }}</span></template></el-table-column>
      <el-table-column label="已部署修订" width="110"><template #default="{ row }"><span class="mono">{{ row.deployed_revision_id ?? '-' }}</span></template></el-table-column>
      <el-table-column label="操作" width="140">
        <template #default="{ row }">
          <el-button link type="primary" @click="router.push({ name: 'service-detail', params: { id: row.id } })">详情</el-button>
          <el-button v-if="session.canNamespace(row.namespace_id, 'service.deploy')" link type="primary" @click="openDeploy(row)">发布</el-button>
        </template>
      </el-table-column>
    </el-table>
  </el-card>

  <el-dialog v-model="creator.visible" title="创建服务" width="620px">
    <el-form label-position="top">
      <el-form-item label="服务名称"><el-input v-model="creator.name" maxlength="63" placeholder="小写字母、数字和 -" /></el-form-item>
      <el-form-item label="项目">
        <el-select v-model="creator.projectId" @change="creator.appId = null; creator.namespaceId = null">
          <el-option v-for="p in projects" :key="p.id" :label="p.name" :value="p.id" />
        </el-select>
      </el-form-item>
      <div class="form-grid">
        <el-form-item label="应用">
          <el-select v-model="creator.appId" placeholder="选择项目内的应用">
            <el-option v-for="a in projectApps" :key="a.id" :label="a.name" :value="a.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="命名空间">
          <el-select v-model="creator.namespaceId" placeholder="项目已分配的命名空间">
            <el-option v-for="n in projectNamespaces" :key="n.id" :label="`${n.cluster_name} / ${n.name}`" :value="n.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="Deployment"><el-select v-model="creator.deployment" filterable :loading="discovering" :disabled="!creator.namespaceId" placeholder="从集群获取工作负载"><el-option v-for="d in discovered?.deployments ?? []" :key="d.name" :label="`${d.name}（${d.ready_replicas}/${d.replicas} 就绪）`" :value="d.name" /></el-select></el-form-item>
        <el-form-item label="容器"><el-select v-model="creator.container" :disabled="!creator.deployment" placeholder="选择目标容器"><el-option v-for="c in containers" :key="c.name" :label="c.name" :value="c.name" /></el-select></el-form-item>
      </div>
      <el-alert v-if="resourceError" type="error" :closable="false" :title="resourceError" />
      <p v-if="creator.container" class="hint mono break-word">当前镜像：{{ containers.find(c => c.name === creator.container)?.image }}</p>
    </el-form>
    <template #footer>
      <el-button @click="creator.visible = false">取消</el-button>
      <el-button type="primary" :loading="busy" :disabled="discovering || !creator.deployment || !creator.container" @click="create">创建服务</el-button>
    </template>
  </el-dialog>

  <DeployDialog v-model="deployOpen" :service="deployTarget" @submitted="load" />
</template>
