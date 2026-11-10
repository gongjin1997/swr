<script setup lang="ts">
import { Plus, Refresh } from '@element-plus/icons-vue'
import { useSession } from '@/stores/session'
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { jobApi } from '@/api'
import type { Job } from '@/api/types'
import JobDetail from '@/components/JobDetail.vue'
import StatusTag from '@/components/StatusTag.vue'
import { notifyError, notifySuccess } from '@/composables/useAction'
import { JOB_FINAL, MODE_TEXT, STATUS_TEXT } from '@/constants'
import { usePlatform } from '@/stores/platform'
import { copyText, fmtDuration, fmtTime } from '@/utils/format'

const session = useSession()
const PAGE = 30
const platform = usePlatform()
const route = useRoute()
const router = useRouter()
const filters = reactive({ appId: Number(route.query.app) || null as number | null, status: '' })
const jobs = ref<Job[]>([])
const total = ref(0)
const loading = ref(false)
let requestSequence = 0
let disposed = false
let timer: ReturnType<typeof setTimeout> | null = null

const detailId = computed<number | null>(() => (route.query.detail ? Number(route.query.detail) : null))
const drawer = computed({
  get: () => detailId.value !== null,
  set: (open: boolean) => {
    if (!open) void router.replace({ query: { ...route.query, detail: undefined } })
  },
})

async function load(reset = true) {
  const current = ++requestSequence
  if (timer) clearTimeout(timer)
  loading.value = true
  try {
    const limit = reset ? Math.max(PAGE, jobs.value.length) : PAGE
    const data = await jobApi.list({ app_id: filters.appId, status: filters.status, limit, offset: reset ? 0 : jobs.value.length })
    if (disposed || current !== requestSequence) return
    jobs.value = reset ? data.items : jobs.value.concat(data.items)
    total.value = data.total
  } catch (e) {
    if (!disposed && current === requestSequence) notifyError(e)
  } finally {
    if (current === requestSequence) loading.value = false
  }
  // 有未结束的任务时每 4 秒刷新
  if (!disposed && current === requestSequence && jobs.value.some((j) => !JOB_FINAL.has(j.status))) timer = setTimeout(() => load(true), 4000)
}

async function copyImage(image: string) {
  try {
    await copyText(image)
    notifySuccess('已复制镜像地址')
  } catch (e) {
    notifyError(e)
  }
}

function openDetail(id: number) {
  void router.replace({ query: { ...route.query, detail: String(id) } })
}

watch(filters, () => load(true))
watch(() => route.query.app, app => { filters.appId = Number(app) || null })
onMounted(async () => {
  platform.loadApps().catch(notifyError)
  await load(true)
})
onBeforeUnmount(() => {
  disposed = true
  requestSequence++
  if (timer) clearTimeout(timer)
})
</script>

<template>
  <div class="page">
    <el-card>
      <div class="filter-bar">
        <el-select v-model="filters.appId" clearable placeholder="全部应用" aria-label="按应用筛选"><el-option v-for="app in platform.apps" :key="app.id" :label="app.name" :value="app.id" /></el-select>
        <el-select v-model="filters.status" clearable placeholder="全部状态" aria-label="按状态筛选"><el-option v-for="status in ['running', 'queued', 'success', 'failed', 'cancelled']" :key="status" :label="STATUS_TEXT[status]" :value="status" /></el-select>
        <el-button :icon="Refresh" :loading="loading" @click="load(true)">刷新</el-button><span class="count">共 {{ total }} 条记录</span>

        <el-button v-if="session.can('build.run')" type="primary" :icon="Plus" @click="router.push('/build')">新建构建</el-button>
      </div>
      <el-table v-loading="loading && !jobs.length" :data="jobs" empty-text="暂无构建记录">
        <el-table-column label="#" width="80"><template #default="{ row }"><span class="mono">#{{ row.id }}</span></template></el-table-column>
        <el-table-column prop="app_name" label="应用" min-width="160" />
        <el-table-column label="版本" min-width="180">
          <template #default="{ row }">
            <span class="mono"><template v-if="row.prev_tag">{{ row.prev_tag }} → </template><b>{{ row.new_tag || row.requested_tag || '自动' }}</b></span>
          </template>
        </el-table-column>
        <el-table-column label="方式" width="100"><template #default="{ row }">{{ MODE_TEXT[row.mode] || '-' }}</template></el-table-column>
        <el-table-column label="状态" width="100"><template #default="{ row }"><StatusTag :status="row.status" /></template></el-table-column>
        <el-table-column prop="operator" label="操作人" width="120" />
        <el-table-column label="提交时间" width="170"><template #default="{ row }">{{ fmtTime(row.created_at) }}</template></el-table-column>
        <el-table-column label="耗时" width="100"><template #default="{ row }">{{ fmtDuration(row.started_at, row.finished_at) }}</template></el-table-column>
        <el-table-column label="操作" width="170">
          <template #default="{ row }">
            <el-button link type="primary" @click="openDetail(row.id)">日志</el-button>
            <el-button v-if="row.status === 'success' && row.image" link @click="copyImage(row.image)">复制镜像地址</el-button>
          </template>
        </el-table-column>
      </el-table>
      <div v-if="jobs.length < total" class="more">
        <el-button :loading="loading" @click="load(false)">加载更多（{{ jobs.length }} / {{ total }}）</el-button>
      </div>
    </el-card>

    <el-drawer v-model="drawer" :title="`构建详情 #${detailId ?? ''}`" size="60%" destroy-on-close>
      <JobDetail :job-id="detailId" log-height="calc(100vh - 260px)" @finished="load(true)" />
    </el-drawer>
  </div>
</template>

<style scoped>
.more {
  display: flex;
  justify-content: center;
  padding-top: 12px;
}
</style>
