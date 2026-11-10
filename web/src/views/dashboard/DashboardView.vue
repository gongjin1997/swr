<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ArrowRight, Box, CircleCheck, Clock, Connection, Grid, Plus, Refresh, Setting } from '@element-plus/icons-vue'
import { jobApi } from '@/api'
import type { Job } from '@/api/types'
import { useSession } from '@/stores/session'
import { usePlatform } from '@/stores/platform'
import { notifyError } from '@/composables/useAction'
import { fmtDuration, fmtTime } from '@/utils/format'
import StatCard from '@/components/StatCard.vue'
import StatusTag from '@/components/StatusTag.vue'

const router = useRouter()
const session = useSession()
const platform = usePlatform()
const loading = ref(false)
const jobs = ref<Job[]>([])
const totals = ref<{ all: number; active: number; success: number } | null>(null)
const failed = ref(false)
const successRate = computed(() => totals.value?.all ? `${Math.round(totals.value.success / totals.value.all * 100)}%` : '—')
const shortcuts = computed(() => [
  { title: '构建镜像', desc: '上传制品，生成新版本', icon: Box, name: 'build', perm: 'build.run' },
  { title: '应用管理', desc: '维护仓库与构建模板', icon: Grid, name: 'apps', perm: 'app.view' },
  { title: '服务发布', desc: '管理配置与发布审批', icon: Connection, name: 'services', perm: 'namespace.view' },
  { title: '系统设置', desc: '连接仓库与构建环境', icon: Setting, name: 'settings', perm: 'settings.manage' },
].filter(item => session.can(item.perm)))
async function load() {
  if (loading.value) return
  loading.value = true
  failed.value = false
  const requests: Promise<unknown>[] = [platform.loadInfo()]
  if (session.can('app.view')) requests.push(platform.loadApps(true))
  if (session.can('job.view')) requests.push(Promise.all([
    jobApi.list({ limit: 6 }), jobApi.list({ status: 'running', limit: 1 }),
    jobApi.list({ status: 'queued', limit: 1 }), jobApi.list({ status: 'success', limit: 1 }),
  ]).then(([recent, running, queued, success]) => {
    jobs.value = recent.items
    totals.value = { all: recent.total, active: running.total + queued.total, success: success.total }
  }))
  const results = await Promise.allSettled(requests)
  const error = results.find(r => r.status === 'rejected')
  if (error?.status === 'rejected') { failed.value = true; notifyError(error.reason) }
  loading.value = false
}
onMounted(load)
</script>

<template>
  <div class="page dashboard">
    <el-alert v-if="failed" title="部分数据加载失败，请刷新重试。" type="warning" show-icon :closable="false" class="load-error" />
    <div class="stats" v-loading="loading">
      <StatCard v-if="session.can('app.view')" title="应用总数" :value="platform.apps.length" sub="当前账号可见的应用" :icon="Grid" />
      <StatCard v-if="session.can('job.view')" title="累计构建" :value="totals?.all ?? '—'" sub="全部历史构建任务" :icon="Box" />
      <StatCard v-if="session.can('job.view')" title="进行中的构建" :value="totals?.active ?? '—'" sub="运行中与排队中的任务" :icon="Clock" tone="warning" />
      <StatCard v-if="session.can('job.view')" title="构建成功率" :value="successRate" :sub="totals ? `${totals.success} 次成功 / ${totals.all} 次构建` : '暂无构建数据'" :icon="CircleCheck" tone="success" />
    </div>
    <div class="dashboard-grid">
      <div class="primary-column">
        <el-card v-if="session.can('job.view')" class="recent-card">
          <template #header>
            <div class="page-card-header">
              <div class="page-card-header-content"><div class="card-title"><span>最近构建 <small>最近 6 条任务</small></span><el-button link type="primary" @click="router.push('/jobs')">全部记录 <el-icon><ArrowRight /></el-icon></el-button></div></div>
              <div class="page-card-actions">
                <div class="toolbar"><el-button :icon="Refresh" :loading="loading" @click="load">刷新</el-button><el-button v-if="session.can('build.run')" type="primary" :icon="Plus" @click="router.push('/build')">新建构建</el-button></div>
              </div>
            </div>
          </template>
          <el-table :data="jobs" empty-text="还没有构建任务，创建应用后即可开始第一次构建。">
            <el-table-column label="应用 / 版本" min-width="190"><template #default="{ row }"><b>{{ row.app_name }}</b><div class="hint mono">#{{ row.id }} · {{ row.new_tag || row.requested_tag || '自动版本' }}</div></template></el-table-column>
            <el-table-column label="状态" width="100"><template #default="{ row }"><StatusTag :status="row.status" /></template></el-table-column>
            <el-table-column label="提交时间" min-width="155"><template #default="{ row }"><span class="hint">{{ fmtTime(row.created_at) }}</span><div class="hint">耗时 {{ fmtDuration(row.started_at, row.finished_at) }}</div></template></el-table-column>
            <el-table-column label="操作" width="75"><template #default="{ row }"><el-button link type="primary" @click="router.push({ path: '/jobs', query: { detail: row.id } })">详情</el-button></template></el-table-column>
          </el-table>
        </el-card>
        <el-card v-if="shortcuts.length" class="shortcuts-card"><template #header><span class="card-title">快捷入口</span></template>
          <div class="shortcuts"><RouterLink v-for="item in shortcuts" :key="item.name" :to="{ name: item.name }" class="shortcut"><el-icon :size="20"><component :is="item.icon" /></el-icon><span><b>{{ item.title }}</b><small>{{ item.desc }}</small></span><el-icon class="arrow"><ArrowRight /></el-icon></RouterLink></div>
        </el-card>
      </div>
      <div class="secondary-column">
        <el-card><template #header><span class="card-title">运行环境</span></template>
          <div class="environment-item"><div class="environment-title"><el-icon><Box /></el-icon><b>构建引擎</b><el-tag size="small" :type="platform.info?.docker.available ? 'success' : 'warning'">{{ !platform.info ? '加载中' : platform.info.docker.available ? '可用' : '不可用' }}</el-tag></div><p>{{ platform.info?.docker.available ? `Docker ${platform.info.docker.version}` : platform.info?.docker.error || '正在检测构建环境' }}</p><span class="hint mono">{{ platform.info?.docker.arch || '—' }}</span></div>
          <div class="environment-item"><div class="environment-title"><el-icon><Connection /></el-icon><b>镜像仓库</b><el-tag size="small" :type="platform.info?.registry.configured ? 'success' : 'info'">{{ platform.info?.registry.configured ? '已配置' : '待配置' }}</el-tag></div><p class="mono">{{ platform.info?.registry.address || '尚未配置仓库地址' }}</p><span class="hint">默认组织：{{ platform.info?.registry.organization || '—' }}</span></div>
          <el-button v-if="session.can('settings.manage')" class="environment-button" @click="router.push('/settings')" :icon="Setting">管理连接设置</el-button>
        </el-card>
        <el-card><template #header><span class="card-title">构建流程</span></template><ol class="workflow"><li><span>1</span><div><b>配置应用</b><p>指定镜像仓库与构建模板</p></div></li><li><span>2</span><div><b>上传并构建</b><p>选择制品，自动生成镜像版本</p></div></li><li><span>3</span><div><b>追溯与发布</b><p>查看日志，将成功构建发布到服务</p></div></li></ol></el-card>
      </div>
    </div>
  </div>
</template>

<style scoped>
.stats { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 18px; margin-bottom: 24px; }
.dashboard-grid { display: grid; grid-template-columns: minmax(0, 1fr) 304px; gap: 22px; align-items: start; }
.primary-column, .secondary-column { min-width: 0; }
.card-title small { color: var(--hi-text-3); font-size: 11px; font-weight: 400; margin-left: 8px; }
.recent-card :deep(.el-card__body) { padding: 8px 20px 16px; }
.shortcuts { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px 20px; }
.shortcut { display: flex; align-items: center; gap: 14px; color: var(--hi-text-2); padding: 14px 8px; border-radius: 6px; }
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
.workflow li { display: flex; gap: 12px; position: relative; padding-bottom: 24px; }
.workflow li:last-child { padding-bottom: 0; }
.workflow li:not(:last-child)::before { content: ''; position: absolute; left: 12px; top: 28px; height: calc(100% - 32px); border-left: 1px dashed var(--hi-border); }
.workflow li > span { display: grid; place-items: center; width: 26px; height: 26px; border-radius: 50%; background: var(--el-color-primary-light-9); color: var(--el-color-primary); font-size: 11px; flex: none; }
.workflow b { font-size: 12px; font-weight: 500; }
.workflow p { color: var(--hi-text-3); font-size: 11px; margin: 4px 0 0; }
.load-error { margin-bottom: 20px; }
@media (max-width: 1200px) { .dashboard-grid { grid-template-columns: minmax(0, 1fr); } .secondary-column { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 18px; } .secondary-column .el-card { margin: 0; } }
@media (max-width: 900px) { .stats { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
@media (max-width: 600px) { .stats { gap: 10px; } .shortcuts, .secondary-column { grid-template-columns: minmax(0, 1fr); } .card-title small { display: none; } }
</style>
