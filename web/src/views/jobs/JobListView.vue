<script setup lang="ts">
/** 构建记录。独立页面可按项目、应用、状态筛选；嵌入项目工作台时固定为该项目。有进行中的任务时每 4 秒刷新。 */
import { Plus, Refresh } from '@element-plus/icons-vue'
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { jobApi } from '@/api'
import type { Job } from '@/api/types'
import JobDetail from '@/components/JobDetail.vue'
import PageHeader from '@/components/PageHeader.vue'
import StatusTag from '@/components/StatusTag.vue'
import { notifyError, notifySuccess } from '@/composables/useAction'
import { usePolling } from '@/composables/usePolling'
import { JOB_FINAL, JOB_STATUSES, MODE_TEXT, STATUS_TEXT } from '@/constants'
import { usePlatform } from '@/stores/platform'
import { useSession } from '@/stores/session'
import { copyText, fmtDuration, fmtTime } from '@/utils/format'

const props = defineProps<{ projectId?: number; embedded?: boolean }>()
const PAGE = 30
const session = useSession()
const platform = usePlatform()
const route = useRoute()
const router = useRouter()
const filters = reactive({
  projectId: props.projectId ?? (Number(route.query.project) || null),
  appId: props.embedded ? null : (Number(route.query.app) || null),
  status: '',
})
const jobs = ref<Job[]>([])
const total = ref(0)
const loading = ref(false)
const detail = ref<number | null>(props.embedded ? null : (Number(route.query.detail) || null))
const apps = computed(() => platform.apps.filter((a) => !filters.projectId || a.project_id === filters.projectId))

const { run: refresh } = usePolling(async (isCurrent) => {
  loading.value = true
  try {
    const data = await jobApi.list({ project_id: filters.projectId, app_id: filters.appId, status: filters.status,
      limit: Math.max(PAGE, jobs.value.length), offset: 0 })
    if (!isCurrent()) return false
    jobs.value = data.items
    total.value = data.total
    return data.items.some((j) => !JOB_FINAL.has(j.status))
  } catch (e) {
    if (isCurrent()) notifyError(e)
    return false
  } finally {
    if (isCurrent()) loading.value = false
  }
})

async function more() {
  loading.value = true
  try {
    const data = await jobApi.list({ project_id: filters.projectId, app_id: filters.appId, status: filters.status, limit: PAGE,
      offset: jobs.value.length })
    jobs.value = jobs.value.concat(data.items)
    total.value = data.total
  } catch (e) {
    notifyError(e)
  } finally {
    loading.value = false
  }
}

async function copyImage(image: string) {
  try {
    await copyText(image)
    notifySuccess('已复制镜像地址')
  } catch (e) {
    notifyError(e)
  }
}

const drawer = computed({
  get: () => detail.value !== null,
  set: (open: boolean) => { if (!open) detail.value = null },
})
watch(detail, (id) => {
  if (!props.embedded) void router.replace({ query: { ...route.query, detail: id ? String(id) : undefined } })
})
watch(() => [filters.projectId, filters.appId, filters.status], () => {
  jobs.value = []
  if (!props.embedded) {
    void router.replace({ query: { ...route.query, project: filters.projectId ? String(filters.projectId) : undefined,
      app: filters.appId ? String(filters.appId) : undefined } })
  }
  void refresh()
})
watch(() => filters.projectId, () => {
  if (filters.appId && !apps.value.some((a) => a.id === filters.appId)) filters.appId = null
})
onMounted(() => {
  platform.loadApps().catch(notifyError)
  void refresh()
})
</script>

<template>
  <div :class="{ page: !embedded }">
    <PageHeader v-if="!embedded" title="构建记录" description="每次构建的版本、方式、镜像地址与完整日志。">
      <el-button v-if="session.can('build.run')" type="primary" :icon="Plus" @click="router.push({ name: 'build' })">新建构建</el-button>
    </PageHeader>
    <el-card>
      <div class="filter-bar">
        <el-select v-if="!embedded" v-model="filters.projectId" clearable filterable placeholder="全部项目" aria-label="按项目筛选">
          <el-option v-for="p in session.profile?.projects ?? []" :key="p.id" :label="p.name" :value="p.id" />
        </el-select>
        <el-select v-model="filters.appId" clearable filterable placeholder="全部应用" aria-label="按应用筛选">
          <el-option v-for="app in apps" :key="app.id" :label="app.name" :value="app.id" />
        </el-select>
        <el-select v-model="filters.status" clearable placeholder="全部状态" aria-label="按状态筛选">
          <el-option v-for="status in JOB_STATUSES" :key="status" :label="STATUS_TEXT[status]" :value="status" />
        </el-select>
        <el-button :icon="Refresh" :loading="loading" @click="refresh">刷新</el-button>
        <span class="count">共 {{ total }} 条记录</span>
      </div>
      <el-table v-loading="loading && !jobs.length" :data="jobs" empty-text="暂无构建记录">
        <el-table-column label="#" width="80"><template #default="{ row }"><span class="mono">#{{ row.id }}</span></template></el-table-column>
        <el-table-column label="应用" min-width="170">
          <template #default="{ row }">{{ row.app_name }}<div v-if="!embedded" class="hint">{{ session.projectName(row.project_id) }}</div></template>
        </el-table-column>
        <el-table-column label="版本" min-width="180">
          <template #default="{ row }">
            <span class="mono"><template v-if="row.prev_tag">{{ row.prev_tag }} → </template><b>{{ row.new_tag || row.requested_tag || '自动' }}</b></span>
          </template>
        </el-table-column>
        <el-table-column label="方式" width="100"><template #default="{ row }">{{ MODE_TEXT[row.mode] || '-' }}</template></el-table-column>
        <el-table-column label="状态" width="100"><template #default="{ row }"><StatusTag :status="row.status" /></template></el-table-column>
        <el-table-column prop="operator" label="提交人" width="120" />
        <el-table-column label="提交时间" width="170"><template #default="{ row }">{{ fmtTime(row.created_at) }}</template></el-table-column>
        <el-table-column label="耗时" width="100"><template #default="{ row }">{{ fmtDuration(row.started_at, row.finished_at) }}</template></el-table-column>
        <el-table-column label="操作" width="170" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" @click="detail = row.id">日志</el-button>
            <el-button v-if="row.status === 'success' && row.image" link @click="copyImage(row.image)">复制镜像</el-button>
          </template>
        </el-table-column>
      </el-table>
      <div v-if="jobs.length < total" class="more">
        <el-button :loading="loading" @click="more">加载更多（{{ jobs.length }} / {{ total }}）</el-button>
      </div>
    </el-card>

    <el-drawer v-model="drawer" :title="`构建详情 #${detail ?? ''}`" size="min(1000px, 100vw)" destroy-on-close>
      <JobDetail :job-id="detail" log-height="calc(100vh - 260px)" @finished="refresh" />
    </el-drawer>
  </div>
</template>

<style scoped>
.more { display: flex; justify-content: center; padding-top: 12px; }
</style>
