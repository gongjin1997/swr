<script setup lang="ts">
/**
 * 发布中心：可发布的服务与发布记录（含审批）。独立页面可按项目、应用、服务、命名空间与状态筛选；
 * 嵌入项目工作台时固定为该项目。有进行中的发布时每 4 秒刷新。
 */
import { Refresh } from '@element-plus/icons-vue'
import { ElMessageBox } from 'element-plus'
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { deploymentApi, serviceApi } from '@/api'
import type { Deployment, Service } from '@/api/types'
import DeployDialog from '@/components/DeployDialog.vue'
import DeploymentLogDialog from '@/components/DeploymentLogDialog.vue'
import PageHeader from '@/components/PageHeader.vue'
import StatusTag from '@/components/StatusTag.vue'
import TablePager from '@/components/TablePager.vue'
import { notifyError, useAction } from '@/composables/useAction'
import { usePagination } from '@/composables/usePagination'
import { usePolling } from '@/composables/usePolling'
import { DEPLOYMENT_ACTIVE, DEPLOYMENT_KIND_TEXT, DEPLOYMENT_STATUSES, STATUS_TEXT, TIER_TAG, TIER_TEXT } from '@/constants'
import { useSession } from '@/stores/session'
import { fmtTime, shortDigest, shortImage } from '@/utils/format'

const PAGE = 30
const props = defineProps<{ projectId?: number; embedded?: boolean }>()
const route = useRoute()
const router = useRouter()
const session = useSession()
const services = ref<Service[]>([])
const records = ref<Deployment[]>([])
const total = ref(0)
const loading = ref(false)
const page = ref(1)
const deployOpen = ref(false)
const deployTarget = ref<Service | null>(null)
const logId = ref<number | null>(null)
const { busy, run } = useAction()
const filters = reactive({
  projectId: props.projectId ?? (Number(route.query.project) || undefined),
  appId: props.embedded ? undefined : (Number(route.query.app) || undefined),
  serviceId: props.embedded ? undefined : (Number(route.query.service) || undefined),
  namespaceId: props.embedded ? undefined : (Number(route.query.namespace) || undefined),
  status: props.embedded ? '' : String(route.query.status ?? ''),
})
const jobId = computed(() => (props.embedded ? undefined : Number(route.query.job) || undefined))
const scopedServices = computed(() => services.value.filter((s) =>
  (!filters.appId || s.app_id === filters.appId) && (!filters.serviceId || s.id === filters.serviceId) &&
  (!filters.namespaceId || s.namespace_id === filters.namespaceId)))
const scoped = computed(() => !!(filters.appId || filters.serviceId || filters.namespaceId))
const servicePager = usePagination(() => scopedServices.value, { size: 10, resetOn: () => [filters.appId, filters.serviceId, filters.namespaceId] })
const pageSize = ref(PAGE)

const { run: refresh } = usePolling(async (isCurrent) => {
  loading.value = true
  try {
    const [s, d] = await Promise.all([
      serviceApi.list(filters.projectId ? { project_id: filters.projectId } : {}),
      deploymentApi.list({ project_id: filters.projectId, app_id: filters.appId, service_id: filters.serviceId,
        namespace_id: filters.namespaceId, status: filters.status || undefined, limit: pageSize.value,
        offset: (page.value - 1) * pageSize.value }),
    ])
    if (!isCurrent()) return false
    services.value = s.items
    records.value = d.items
    total.value = d.total
    return d.items.some((r) => DEPLOYMENT_ACTIVE.has(r.status))
  } catch (e) {
    if (isCurrent()) notifyError(e)
    return false
  } finally {
    if (isCurrent()) loading.value = false
  }
})

function deploy(service: Service) {
  deployTarget.value = service
  deployOpen.value = true
}

function clearScope() {
  Object.assign(filters, { appId: undefined, serviceId: undefined, namespaceId: undefined })
}

async function decide(record: Deployment, approve: boolean) {
  try {
    const { value } = await ElMessageBox.prompt(`${approve ? '批准' : '拒绝'} ${record.target_name} 的发布 #${record.id}`, '发布审批', {
      inputPlaceholder: '填写审批意见（可选）', confirmButtonText: approve ? '批准' : '拒绝', cancelButtonText: '取消',
      inputValidator: (text) => (text?.length ?? 0) <= 500 || '审批意见不能超过 500 字符',
    })
    if (await run(() => (approve ? deploymentApi.approve(record.id, value ?? '') : deploymentApi.reject(record.id, value ?? '')),
      '审批意见已提交')) await refresh()
  } catch (e) {
    if (e !== 'cancel' && e !== 'close') notifyError(e)
  }
}

watch(() => [filters.projectId, filters.appId, filters.serviceId, filters.namespaceId, filters.status], () => {
  page.value = 1
  if (!props.embedded) {
    const query: Record<string, string | undefined> = {
      project: filters.projectId ? String(filters.projectId) : undefined, app: filters.appId ? String(filters.appId) : undefined,
      service: filters.serviceId ? String(filters.serviceId) : undefined,
      namespace: filters.namespaceId ? String(filters.namespaceId) : undefined, status: filters.status || undefined,
    }
    void router.replace({ query })
  }
  void refresh()
})
onMounted(() => {
  void refresh()
  if (jobId.value && filters.appId) {
    // 从构建详情"发布此构建"进入：只有一个可发布的服务时直接打开发布对话框
    void serviceApi.list({ app_id: filters.appId }).then((list) => {
      const deployable = list.items.filter((s) => session.canNamespace(s.namespace_id, 'service.deploy'))
      if (deployable.length === 1) deploy(deployable[0])
    }).catch(notifyError)
  }
})
</script>

<template>
  <div :class="{ page: !embedded }">
    <PageHeader v-if="!embedded" title="发布中心" description="选择服务所属应用的成功构建发布到命名空间；UAT 与生产环境需要独立审批，发起人不能审批自己的任务。">
      <el-button :icon="Refresh" :loading="loading" @click="refresh">刷新</el-button>
    </PageHeader>
    <el-card>
      <template #header>
        <div class="card-title">
          <span>服务</span>
          <el-button v-if="scoped" size="small" @click="clearScope">显示全部服务</el-button>
        </div>
      </template>
      <el-table :data="servicePager.rows.value" empty-text="暂无可见服务：请先在项目中分配命名空间并创建服务">
        <el-table-column label="服务" min-width="160">
          <template #default="{ row }"><RouterLink :to="{ name: 'service-detail', params: { id: row.id } }"><b>{{ row.name }}</b></RouterLink></template>
        </el-table-column>
        <el-table-column label="项目 / 应用" min-width="180">
          <template #default="{ row }">{{ row.project_name }}<div class="hint">{{ row.app_name }}</div></template>
        </el-table-column>
        <el-table-column label="环境" min-width="200">
          <template #default="{ row }">
            <el-tag size="small" :type="TIER_TAG[row.tier]" effect="plain">{{ TIER_TEXT[row.tier] }}</el-tag>
            <span class="hint gap">{{ row.cluster_name }} / {{ row.namespace_name }}</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="180" fixed="right">
          <template #default="{ row }">
            <el-button v-if="session.canNamespace(row.namespace_id, 'service.deploy')" link type="primary" @click="deploy(row)">选择构建发布</el-button>
            <el-button link @click="router.push({ name: 'service-detail', params: { id: row.id } })">运行状态</el-button>
          </template>
        </el-table-column>
      </el-table>
      <TablePager v-model:page="servicePager.page.value" v-model:size="servicePager.size.value" :total="servicePager.total.value" compact />
    </el-card>

    <el-card>
      <template #header>
        <div class="card-title">
          <span>发布记录与审批</span>
          <el-select v-model="filters.status" clearable placeholder="全部状态" aria-label="发布状态" style="width: 170px">
            <el-option v-for="status in DEPLOYMENT_STATUSES" :key="status" :label="STATUS_TEXT[status]" :value="status" />
          </el-select>
        </div>
      </template>
      <el-table v-loading="loading && !records.length" :data="records" empty-text="暂无发布记录">
        <el-table-column label="发布 / 服务" min-width="140">
          <template #default="{ row }">
            <b>#{{ row.id }}</b> <span class="hint">{{ DEPLOYMENT_KIND_TEXT[row.kind] ?? row.kind }}</span>
            <div>
              <RouterLink v-if="row.service_id" :to="{ name: 'service-detail', params: { id: row.service_id } }">{{ row.target_name }}</RouterLink>
              <span v-else>{{ row.target_name }}</span>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="项目 / 环境" min-width="170">
          <template #default="{ row }">
            {{ row.project_name }} / {{ row.app_name }}
            <div class="hint">{{ row.cluster_name }} / {{ row.namespace_name }} · {{ TIER_TEXT[row.environment] ?? row.environment }}</div>
          </template>
        </el-table-column>
        <el-table-column label="构建 / 镜像" min-width="180">
          <template #default="{ row }">
            <RouterLink v-if="row.job_id" :to="{ name: 'jobs', query: { app: row.app_id, detail: row.job_id } }">构建 #{{ row.job_id }}</RouterLink>
            <span v-else class="hint">线上镜像（接管发布）</span>
            <el-tooltip :content="row.image" placement="top">
              <div class="hint mono break-word">{{ shortImage(row.image.split('@')[0]) }}<template v-if="row.digest"> · {{ shortDigest(row.digest) }}</template></div>
            </el-tooltip>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="150">
          <template #default="{ row }">
            <StatusTag :status="row.status" />
            <div v-if="row.status === 'pending_approval'" class="hint">{{ row.require_admin_approval ? '需要平台管理员确认' : '等待独立审批' }}</div>
            <div v-else-if="row.error" class="hint">{{ row.error }}</div>
          </template>
        </el-table-column>
        <el-table-column label="提交" width="150"><template #default="{ row }">{{ row.operator }}<div class="hint">{{ fmtTime(row.created_at) }}</div></template></el-table-column>
        <el-table-column label="操作" width="170" fixed="right">
          <template #default="{ row }">
            <el-button v-if="row.can_approve" link type="primary" :disabled="busy" @click="decide(row, true)">批准</el-button>
            <el-button v-if="row.can_reject" link type="danger" :disabled="busy" @click="decide(row, false)">拒绝</el-button>
            <el-button link @click="logId = row.id">详情与日志</el-button>
          </template>
        </el-table-column>
      </el-table>
      <TablePager v-model:page="page" v-model:size="pageSize" :total="total" @change="refresh" />
    </el-card>
    <DeployDialog v-model="deployOpen" :service="deployTarget" :job-id="jobId" @submitted="refresh" />
    <DeploymentLogDialog v-model="logId" />
  </div>
</template>

<style scoped>
.gap { margin-left: 6px; }
</style>
