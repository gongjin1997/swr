<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, shallowRef, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessageBox } from 'element-plus'
import { deploymentApi, serviceApi } from '@/api'
import type { Deployment, Service } from '@/api/types'
import DeployDialog from '@/components/DeployDialog.vue'
import DeploymentLogDialog from '@/components/DeploymentLogDialog.vue'
import StatusTag from '@/components/StatusTag.vue'
import { notifyError, useAction } from '@/composables/useAction'
import { STATUS_TEXT, TIER_TEXT } from '@/constants'
import { useSession } from '@/stores/session'
import { fmtTime } from '@/utils/format'
const route = useRoute()
const router = useRouter()
const session = useSession()
const services = ref<Service[]>([])
const records = ref<Deployment[]>([])
const loading = shallowRef(false)
const status = shallowRef('')
const page = shallowRef(1)
const total = shallowRef(0)
const deployOpen = shallowRef(false)
const deployTarget = ref<Service | null>(null)
const logId = shallowRef<number | null>(null)
const { busy, run } = useAction()
let refreshTimer: ReturnType<typeof setTimeout> | undefined
let requestSequence = 0
let disposed = false
const projectId = computed(() => Number(route.query.project) || undefined)
const appId = computed(() => Number(route.query.app) || undefined)
const serviceId = computed(() => Number(route.query.service) || undefined)
const namespaceId = computed(() => Number(route.query.namespace) || undefined)
const scopedServices = computed(() => services.value.filter(s =>
  (!projectId.value || s.project_id === projectId.value) && (!appId.value || s.app_id === appId.value) &&
  (!serviceId.value || s.id === serviceId.value) && (!namespaceId.value || s.namespace_id === namespaceId.value)))
async function load() {
  const request = ++requestSequence
  clearTimeout(refreshTimer)
  loading.value = true
  try {
    const [s, d] = await Promise.all([serviceApi.list(), deploymentApi.list({ project_id: projectId.value,
      app_id: appId.value, service_id: serviceId.value, namespace_id: namespaceId.value,
      status: status.value || undefined, limit: 30, offset: (page.value - 1) * 30 })])
    if (disposed || request !== requestSequence) return
    services.value = s.items
    records.value = d.items
    total.value = d.total
    if (d.items.some(record => ['queued', 'applying', 'verifying'].includes(record.status))) refreshTimer = setTimeout(load, 4000)
  } catch (e) { if (!disposed && request === requestSequence) notifyError(e) }
  finally { if (request === requestSequence) loading.value = false }
}
function deploy(service: Service) { deployTarget.value = service; deployOpen.value = true }
async function decide(record: Deployment, approve: boolean) {
  try {
    const { value } = await ElMessageBox.prompt(`${approve ? '批准' : '拒绝'} ${record.target_name} 的发布 #${record.id}`, '发布审批',
      { inputPlaceholder: '填写审批意见', confirmButtonText: approve ? '批准' : '拒绝', cancelButtonText: '取消',
        inputValidator: value => (value?.length ?? 0) <= 500 || '审批意见不能超过 500 字符' })
    if (await run(() => approve ? deploymentApi.approve(record.id, value ?? '') : deploymentApi.reject(record.id, value ?? ''), '审批意见已提交')) await load()
  } catch (e) { if (e !== 'cancel' && e !== 'close') notifyError(e) }
}
watch(() => route.fullPath, () => { page.value = 1; void load() })
onMounted(load)
onBeforeUnmount(() => { disposed = true; requestSequence++; clearTimeout(refreshTimer) })
</script>
<template>
  <div class="page" v-loading="loading">
    <el-card>
      <template #header><div class="card-title"><span>服务发布</span><div class="toolbar"><el-button v-if="projectId || appId || serviceId || namespaceId" @click="router.push({ name: 'deploy' })">全部服务</el-button><RouterLink to="/resources/services"><el-button>管理服务</el-button></RouterLink><el-button @click="load">刷新</el-button></div></div></template>
      <el-table :data="scopedServices" empty-text="暂无关联服务，请先在项目中关联应用与命名空间并创建服务">
        <el-table-column label="项目 / 应用" min-width="180"><template #default="{ row }"><RouterLink :to="{ name: 'apps', query: { project: row.project_id } }">{{ row.project_name }}</RouterLink><p class="hint">{{ row.app_name }}</p></template></el-table-column>
        <el-table-column label="服务" min-width="160"><template #default="{ row }"><RouterLink :to="{ name: 'service-detail', params: { id: row.id } }">{{ row.name }}</RouterLink></template></el-table-column>
        <el-table-column label="集群 / 命名空间" min-width="190"><template #default="{ row }">{{ row.cluster_name }} / {{ row.namespace_name }}</template></el-table-column>
        <el-table-column label="环境" width="150"><template #default="{ row }">{{ TIER_TEXT[row.tier] }}</template></el-table-column>
        <el-table-column label="操作" width="160"><template #default="{ row }"><el-button v-if="session.canNamespace(row.namespace_id, 'service.deploy')" link type="primary" @click="deploy(row)">选择构建发布</el-button><el-button link @click="router.push({ name: 'service-detail', params: { id: row.id } })">运行状态</el-button></template></el-table-column>
      </el-table>
    </el-card>
    <el-card>
      <template #header><div class="card-title"><span>发布记录与审批</span><el-select v-model="status" clearable placeholder="全部状态" aria-label="发布状态" style="width: 170px" @change="page = 1; load()"><el-option v-for="(label, value) in STATUS_TEXT" :key="value" :label="label" :value="value" /></el-select></div></template>
      <el-table :data="records" empty-text="暂无发布记录">
        <el-table-column label="发布 / 服务" min-width="160"><template #default="{ row }"><b>#{{ row.id }}</b><p><RouterLink v-if="row.service_id" :to="{ name: 'service-detail', params: { id: row.service_id } }">{{ row.target_name }}</RouterLink><span v-else>{{ row.target_name }}</span></p></template></el-table-column>
        <el-table-column label="项目 / 应用 / 环境" min-width="190"><template #default="{ row }">{{ row.project_name }} / {{ row.app_name }}<p class="hint">{{ row.cluster_name }} / {{ row.namespace_name }} · {{ TIER_TEXT[row.environment] ?? row.environment }}</p></template></el-table-column>
        <el-table-column label="构建 / 镜像" min-width="240"><template #default="{ row }"><RouterLink :to="{ name: 'jobs', query: { app: row.app_id, detail: row.job_id } }">构建 #{{ row.job_id }}</RouterLink><p class="hint mono break-word">{{ row.image }}</p></template></el-table-column>
        <el-table-column label="状态" width="150"><template #default="{ row }"><StatusTag :status="row.status" /><p v-if="row.status === 'pending_approval'" class="hint">{{ row.require_admin_approval ? '管理员确认' : '等待独立审批' }}</p><p v-if="row.error" class="hint">{{ row.error }}</p></template></el-table-column>
        <el-table-column label="提交" width="165"><template #default="{ row }">{{ row.operator }}<p class="hint">{{ fmtTime(row.created_at) }}</p></template></el-table-column>
        <el-table-column label="操作" width="160" fixed="right"><template #default="{ row }"><el-button v-if="row.can_approve" link type="primary" :disabled="busy" @click="decide(row, true)">批准</el-button><el-button v-if="row.can_reject" link type="danger" :disabled="busy" @click="decide(row, false)">拒绝</el-button><el-button link @click="logId = row.id">详情与日志</el-button></template></el-table-column>
      </el-table>
      <el-pagination v-if="total > 30" v-model:current-page="page" :page-size="30" :total="total" layout="prev, pager, next, total" @current-change="load" class="section-gap" />
    </el-card>
    <DeployDialog v-model="deployOpen" :service="deployTarget" :job-id="Number(route.query.job) || undefined" @submitted="load" />
    <DeploymentLogDialog v-model="logId" />
  </div>
</template>
