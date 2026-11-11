<script setup lang="ts">
/** 工作台：可见范围内的汇总、待我审批的发布、最近构建、运行环境与快捷入口。 */
import { ArrowRight, Box, CircleCheck, Clock, Connection, Files, Grid, Plus, Promotion, Refresh, Setting, Share } from '@element-plus/icons-vue'
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { platformApi } from '@/api'
import type { Overview } from '@/api/types'
import PageHeader from '@/components/PageHeader.vue'
import StatCard from '@/components/StatCard.vue'
import StatusTag from '@/components/StatusTag.vue'
import { notifyError } from '@/composables/useAction'
import { TIER_TAG, TIER_TEXT } from '@/constants'
import { allowed } from '@/router'
import { usePlatform } from '@/stores/platform'
import { useSession } from '@/stores/session'
import { fmtDuration, fmtTime } from '@/utils/format'

const router = useRouter()
const session = useSession()
const platform = usePlatform()
const loading = ref(false)
const overview = ref<Overview | null>(null)
const successRate = computed(() => {
  const jobs = overview.value?.jobs
  const finished = jobs ? jobs.success + jobs.failed : 0
  return jobs && finished ? `${Math.round((jobs.success / finished) * 100)}%` : '—'
})
const shortcuts = computed(() => [
  { title: '构建镜像', desc: '上传制品，生成新版本', icon: Box, name: 'build' },
  { title: '发布中心', desc: '选择构建发布到命名空间', icon: Promotion, name: 'releases' },
  { title: '项目', desc: '应用、服务与成员', icon: Files, name: 'projects' },
  { title: '应用', desc: '镜像仓库与构建模板', icon: Grid, name: 'apps' },
  { title: '服务', desc: '运行状态与配置修订', icon: Share, name: 'services' },
  { title: '系统设置', desc: '镜像仓库与构建环境', icon: Setting, name: 'settings' },
].filter((item) => {
  const record = router.getRoutes().find((r) => r.name === item.name)
  return !!record && allowed(record)
}))
const greeting = computed(() => {
  const hour = new Date().getHours()
  return hour < 12 ? '上午好' : hour < 18 ? '下午好' : '晚上好'
})

async function load() {
  loading.value = true
  try {
    const [data] = await Promise.all([platformApi.overview(), platform.loadInfo()])
    overview.value = data
  } catch (e) {
    notifyError(e)
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="page dashboard">
    <PageHeader :title="`${greeting}，${session.profile?.display_name ?? ''}`" :description="session.profile?.role_label">
      <el-button :icon="Refresh" :loading="loading" @click="load">刷新</el-button>
      <el-button v-if="session.can('build.run')" type="primary" :icon="Plus" @click="router.push({ name: 'build' })">新建构建</el-button>
    </PageHeader>

    <el-alert v-if="!loading && overview && !session.profile?.permissions.length" type="info" show-icon :closable="false"
      title="你的账号还没有任何授权" description="请联系平台管理员或项目管理员，为你授予项目或命名空间角色。" class="section-block" />

    <div class="stats" v-loading="loading && !overview">
      <StatCard title="项目" :value="overview?.projects ?? '—'" sub="可见的项目" :icon="Files" />
      <StatCard v-if="overview?.apps !== undefined" title="应用" :value="overview.apps" sub="可见的镜像仓库" :icon="Grid" />
      <StatCard v-if="overview?.services !== undefined" title="服务" :value="overview.services" sub="可见的部署目标" :icon="Share" tone="success" />
      <StatCard v-if="overview?.jobs" title="进行中的构建" :value="overview.jobs.active" :sub="`累计 ${overview.jobs.total} 次构建`" :icon="Clock" tone="warning" />
      <StatCard v-if="overview?.jobs" title="构建成功率" :value="successRate" :sub="`${overview.jobs.success} 次成功 / ${overview.jobs.failed} 次失败`" :icon="CircleCheck" tone="success" />
    </div>

    <div class="dashboard-grid">
      <div class="primary-column">
        <el-card v-if="overview?.approvals?.length" class="approvals">
          <template #header>
            <div class="card-title">
              <span>待我审批 <el-tag type="warning" size="small" round>{{ overview.approvals.length }}</el-tag></span>
              <el-button link type="primary" @click="router.push({ name: 'releases', query: { status: 'pending_approval' } })">全部待审批 <el-icon><ArrowRight /></el-icon></el-button>
            </div>
          </template>
          <el-table :data="overview.approvals.slice(0, 6)">
            <el-table-column label="服务" min-width="170">
              <template #default="{ row }">
                <RouterLink :to="{ name: 'service-detail', params: { id: row.service_id } }"><b>{{ row.target_name }}</b></RouterLink>
                <div class="hint">#{{ row.id }} · {{ row.project_name }}</div>
              </template>
            </el-table-column>
            <el-table-column label="环境" min-width="180">
              <template #default="{ row }">
                <el-tag size="small" :type="TIER_TAG[row.environment]" effect="plain">{{ TIER_TEXT[row.environment] ?? row.environment }}</el-tag>
                <span class="hint gap">{{ row.cluster_name }} / {{ row.namespace_name }}</span>
              </template>
            </el-table-column>
            <el-table-column label="发起" min-width="150"><template #default="{ row }">{{ row.operator }}<div class="hint">{{ fmtTime(row.created_at) }}</div></template></el-table-column>
          </el-table>
        </el-card>

        <el-card v-if="overview?.jobs">
          <template #header>
            <div class="card-title">
              <span>最近构建</span>
              <el-button link type="primary" @click="router.push({ name: 'jobs' })">全部记录 <el-icon><ArrowRight /></el-icon></el-button>
            </div>
          </template>
          <el-table :data="overview.jobs.recent" empty-text="还没有构建任务，创建应用后即可开始第一次构建。">
            <el-table-column label="应用 / 版本" min-width="190">
              <template #default="{ row }"><b>{{ row.app_name }}</b><div class="hint mono">#{{ row.id }} · {{ row.new_tag || row.requested_tag || '自动版本' }}</div></template>
            </el-table-column>
            <el-table-column label="状态" width="100"><template #default="{ row }"><StatusTag :status="row.status" /></template></el-table-column>
            <el-table-column label="提交" min-width="160">
              <template #default="{ row }">{{ row.operator }}<div class="hint">{{ fmtTime(row.created_at) }} · {{ fmtDuration(row.started_at, row.finished_at) }}</div></template>
            </el-table-column>
            <el-table-column label="操作" width="75">
              <template #default="{ row }"><el-button link type="primary" @click="router.push({ name: 'jobs', query: { detail: row.id } })">详情</el-button></template>
            </el-table-column>
          </el-table>
        </el-card>

        <el-card v-if="shortcuts.length">
          <template #header><span class="card-title">快捷入口</span></template>
          <div class="shortcuts">
            <RouterLink v-for="item in shortcuts" :key="item.name" :to="{ name: item.name }" class="shortcut">
              <el-icon :size="20"><component :is="item.icon" /></el-icon>
              <span><b>{{ item.title }}</b><small>{{ item.desc }}</small></span>
              <el-icon class="arrow"><ArrowRight /></el-icon>
            </RouterLink>
          </div>
        </el-card>
      </div>

      <div class="secondary-column">
        <el-card>
          <template #header><span class="card-title">运行环境</span></template>
          <div class="environment-item">
            <div class="environment-title"><el-icon><Box /></el-icon><b>构建引擎</b>
              <el-tag size="small" :type="platform.info?.docker.available ? 'success' : 'warning'">{{ !platform.info ? '加载中' : platform.info.docker.available ? '可用' : '不可用' }}</el-tag>
            </div>
            <p>{{ platform.info?.docker.available ? `Docker ${platform.info.docker.version}` : platform.info?.docker.error || '正在检测构建环境' }}</p>
            <span class="hint mono">{{ platform.info?.docker.arch || '—' }}</span>
          </div>
          <div class="environment-item">
            <div class="environment-title"><el-icon><Connection /></el-icon><b>镜像仓库</b>
              <el-tag size="small" :type="platform.info?.registry.configured ? 'success' : 'info'">{{ platform.info?.registry.configured ? '已配置' : '待配置' }}</el-tag>
            </div>
            <p class="mono">{{ platform.info?.registry.address || '尚未配置仓库地址' }}</p>
            <span class="hint">默认组织：{{ platform.info?.registry.organization || '—' }}</span>
          </div>
          <el-button v-if="session.canGlobal('settings.manage')" class="environment-button" :icon="Setting" @click="router.push({ name: 'settings' })">管理连接设置</el-button>
        </el-card>
        <el-card>
          <template #header><span class="card-title">交付流程</span></template>
          <ol class="workflow">
            <li><span>1</span><div><b>项目与应用</b><p>在项目中配置镜像仓库与构建模板</p></div></li>
            <li><span>2</span><div><b>构建镜像</b><p>上传制品，自动生成新版本并推送</p></div></li>
            <li><span>3</span><div><b>服务与发布</b><p>选择成功构建发布，按环境审批</p></div></li>
          </ol>
        </el-card>
      </div>
    </div>
  </div>
</template>

<style scoped>
.section-block { margin-bottom: 18px; }
.stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); gap: 16px; margin-bottom: 20px; min-height: 80px; }
.dashboard-grid { display: grid; grid-template-columns: minmax(0, 1fr) 304px; gap: 20px; align-items: start; }
.primary-column, .secondary-column { min-width: 0; }
.primary-column > .el-card + .el-card, .secondary-column > .el-card + .el-card { margin-top: 18px; }
.gap { margin-left: 6px; }
.card-title > span { display: inline-flex; align-items: center; gap: 8px; }
.shortcuts { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px 20px; }
.shortcut { display: flex; align-items: center; gap: 14px; color: var(--hi-text-2); padding: 12px 8px; border-radius: 6px; }
.shortcut:hover { background: var(--hi-bg-hover); color: var(--el-color-primary); }
.shortcut > .el-icon:first-child { color: var(--el-color-primary); }
.shortcut b { font-size: 13px; font-weight: 500; }
.shortcut small { display: block; color: var(--hi-text-3); font-size: 11px; margin-top: 4px; }
.arrow { margin-left: auto; color: var(--hi-text-3); }
.environment-item + .environment-item { border-top: 1px solid var(--hi-border-light); padding-top: 20px; margin-top: 20px; }
.environment-title { display: flex; align-items: center; gap: 8px; font-size: 12px; }
.environment-title .el-icon { color: var(--hi-text-3); }
.environment-title .el-tag { margin-left: auto; }
.environment-item p { margin: 10px 0 4px; font-size: 12px; color: var(--hi-text-2); overflow-wrap: anywhere; }
.environment-button { margin-top: 20px; width: 100%; }
.workflow { margin: 0; padding: 0; list-style: none; }
.workflow li { display: flex; gap: 12px; position: relative; padding-bottom: 22px; }
.workflow li:last-child { padding-bottom: 0; }
.workflow li:not(:last-child)::before { content: ''; position: absolute; left: 12px; top: 28px; height: calc(100% - 32px); border-left: 1px dashed var(--hi-border); }
.workflow li > span { display: grid; place-items: center; width: 26px; height: 26px; border-radius: 50%; background: var(--el-color-primary-light-9); color: var(--el-color-primary); font-size: 11px; flex: none; }
.workflow b { font-size: 12px; font-weight: 500; }
.workflow p { color: var(--hi-text-3); font-size: 11px; margin: 4px 0 0; }
@media (max-width: 1200px) {
  .dashboard-grid { grid-template-columns: minmax(0, 1fr); }
  .secondary-column { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 18px; }
  .secondary-column > .el-card + .el-card { margin-top: 0; }
}
@media (max-width: 600px) { .shortcuts, .secondary-column { grid-template-columns: minmax(0, 1fr); } }
</style>
