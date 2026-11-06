<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { deploymentApi, namespaceApi, projectApi, serviceApi } from '@/api'
import type { Deployment, Namespace, Project, Service } from '@/api/types'
import DeployDialog from '@/components/DeployDialog.vue'
import DeploymentLogDialog from '@/components/DeploymentLogDialog.vue'
import StatusTag from '@/components/StatusTag.vue'
import { confirmAction, notifyError, useAction } from '@/composables/useAction'
import { usePlatform } from '@/stores/platform'
import { useSession } from '@/stores/session'
import { fmtTime } from '@/utils/format'

const session = useSession()
const platform = usePlatform()
const router = useRouter()
const services = ref<Service[]>([])
const deployments = ref<Deployment[]>([])
const projects = ref<Project[]>([])
const namespaces = ref<Namespace[]>([])
const loading = ref(false)
const { busy, run } = useAction()
const deployTarget = ref<Service | null>(null)
const deployOpen = ref(false)
const logId = ref<number | null>(null)
const creator = reactive({ visible: false, name: '', projectId: null as number | null, appId: null as number | null,
  namespaceId: null as number | null, deployment: '', container: '' })

const nsById = computed(() => new Map(namespaces.value.map((n) => [n.id, n])))
const projectApps = computed(() => platform.apps.filter((a) => a.project_id === creator.projectId))
const projectNamespaces = computed(() => {
  const project = projects.value.find((p) => p.id === creator.projectId)
  return namespaces.value.filter((n) => project?.namespace_ids.includes(n.id))
})

async function load() {
  loading.value = true
  try {
    const empty = { items: [], total: 0 }
    const [s, d, p, n] = await Promise.all([
      serviceApi.list(),
      deploymentApi.list({ limit: 100 }).catch(() => empty),
      projectApi.list().catch(() => empty),
      namespaceApi.list().catch(() => empty),
      platform.loadApps(),
    ])
    services.value = s.items
    deployments.value = (d.items as Deployment[]).filter((x) => x.service_id)
    projects.value = p.items as Project[]
    namespaces.value = n.items as Namespace[]
  } catch (e) {
    notifyError(e)
  } finally {
    loading.value = false
  }
}

function serviceName(id: number | null): string {
  return services.value.find((s) => s.id === id)?.name ?? `#${id}`
}

function openCreator() {
  const project = projects.value.find((p) => p.namespace_ids.length)
  if (!project || !platform.apps.length) return notifyError(new Error('需要可见项目、应用和已分配的命名空间才能创建服务'))
  Object.assign(creator, { visible: true, name: '', projectId: project.id, appId: null, namespaceId: null, deployment: '', container: '' })
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

async function decide(d: Deployment, approve: boolean) {
  if (!approve && !(await confirmAction('拒绝此服务发布？', '拒绝发布', true))) return
  const ok = await run(() => (approve ? deploymentApi.approve(d.id) : deploymentApi.reject(d.id)), approve ? '审批已提交' : '发布已拒绝')
  if (ok) await load()
}

onMounted(load)
</script>

<template>
  <el-card>
    <template #header>
      <div class="card-title">
        <span>服务</span>
        <el-button v-if="session.can('service.config')" type="primary" @click="openCreator">创建服务</el-button>
      </div>
    </template>
    <el-table v-loading="loading" :data="services" empty-text="暂无可见服务">
      <el-table-column label="服务" min-width="160">
        <template #default="{ row }">
          <el-button link type="primary" @click="router.push({ name: 'service-detail', params: { id: row.id } })">{{ row.name }}</el-button>
          <div class="hint">{{ row.spec?.mode }}</div>
        </template>
      </el-table-column>
      <el-table-column label="项目 / 应用" min-width="180">
        <template #default="{ row }">
          {{ projects.find((p) => p.id === row.project_id)?.name ?? `#${row.project_id}` }}
          <div class="hint">{{ platform.appName(row.app_id) }}</div>
        </template>
      </el-table-column>
      <el-table-column label="命名空间" min-width="180">
        <template #default="{ row }">{{ nsById.get(row.namespace_id)?.cluster_name }} / {{ nsById.get(row.namespace_id)?.name ?? `#${row.namespace_id}` }}</template>
      </el-table-column>
      <el-table-column label="配置修订" width="100"><template #default="{ row }"><span class="mono">{{ row.current_revision_id }}</span></template></el-table-column>
      <el-table-column label="已部署修订" width="110"><template #default="{ row }"><span class="mono">{{ row.deployed_revision_id ?? '-' }}</span></template></el-table-column>
      <el-table-column label="操作" width="140">
        <template #default="{ row }">
          <el-button link type="primary" @click="router.push({ name: 'service-detail', params: { id: row.id } })">详情</el-button>
          <el-button v-if="session.can('service.deploy')" link type="primary" @click="openDeploy(row)">发布</el-button>
        </template>
      </el-table-column>
    </el-table>
  </el-card>

  <el-card>
    <template #header><div class="card-title"><span>服务发布</span><el-button :loading="loading" @click="load">刷新</el-button></div></template>
    <el-table :data="deployments" empty-text="暂无服务发布">
      <el-table-column label="#" width="80"><template #default="{ row }"><span class="mono">#{{ row.id }}</span></template></el-table-column>
      <el-table-column label="服务" min-width="140">
        <template #default="{ row }">{{ serviceName(row.service_id) }}<div class="hint">{{ row.kind }}</div></template>
      </el-table-column>
      <el-table-column label="镜像摘要" min-width="260"><template #default="{ row }"><span class="mono break-word">{{ row.digest || row.image }}</span></template></el-table-column>
      <el-table-column label="状态" min-width="160">
        <template #default="{ row }"><StatusTag :status="row.status" /><div class="hint">{{ row.message || row.error }}</div></template>
      </el-table-column>
      <el-table-column label="提交" width="170">
        <template #default="{ row }">{{ fmtTime(row.created_at) }}<div class="hint">{{ row.operator }}</div></template>
      </el-table-column>
      <el-table-column label="操作" width="150">
        <template #default="{ row }">
          <template v-if="row.status === 'pending_approval' && session.can('deploy.approve')">
            <el-button link type="primary" :disabled="busy" @click="decide(row, true)">批准</el-button>
            <el-button link type="danger" :disabled="busy" @click="decide(row, false)">拒绝</el-button>
          </template>
          <el-button v-if="row.status !== 'pending_approval'" link @click="logId = row.id">日志</el-button>
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
        <el-form-item label="现有 Deployment"><el-input v-model="creator.deployment" /></el-form-item>
        <el-form-item label="容器名称"><el-input v-model="creator.container" /></el-form-item>
      </div>
      <el-alert type="info" :closable="false" title="当前服务发布支持 image-only 模式：只更新指定 Deployment 中单个容器的镜像。" />
    </el-form>
    <template #footer>
      <el-button @click="creator.visible = false">取消</el-button>
      <el-button type="primary" :loading="busy" @click="create">创建服务</el-button>
    </template>
  </el-dialog>

  <DeployDialog v-model="deployOpen" :service="deployTarget" @submitted="load" />
  <DeploymentLogDialog v-model="logId" />
</template>
