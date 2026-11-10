<script setup lang="ts">
import { Plus, Refresh, Search } from '@element-plus/icons-vue'
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { appApi } from '@/api'
import type { App } from '@/api/types'
import { confirmAction, notifyError, useAction } from '@/composables/useAction'
import { MODE_TEXT } from '@/constants'
import { usePlatform } from '@/stores/platform'
import { useSession } from '@/stores/session'
import { fmtTime } from '@/utils/format'
import AppFormDialog from './AppFormDialog.vue'

const platform = usePlatform()
const session = useSession()
const router = useRouter()
const route = useRoute()
const projectName = (id: number) => session.profile?.projects.find(p => p.id === id)?.name ?? `#${id}`
const editing = ref<App | null>(null)
const dialog = ref(false)
const loading = ref(false)
const keyword = ref('')
const mode = ref('')
const filtered = computed(() => platform.apps.filter(app =>
  (!route.query.project || app.project_id === Number(route.query.project)) &&
  (!route.query.app || app.id === Number(route.query.app)) &&
  (!mode.value || app.default_mode === mode.value) &&
  (!keyword.value.trim() || `${app.name} ${app.description} ${app.image_repo}`.toLowerCase().includes(keyword.value.trim().toLowerCase())),
))
const { run } = useAction()

async function load() {
  loading.value = true
  try {
    await platform.loadApps(true)
  } catch (e) {
    notifyError(e)
  } finally {
    loading.value = false
  }
}

function open(app: App | null) {
  editing.value = app
  dialog.value = true
}

async function remove(app: App) {
  if (!(await confirmAction(`确定删除应用“${app.name}”？不会删除 SWR 中的镜像和构建记录。`, '删除应用', true))) return
  await run(async () => {
    await appApi.remove(app.id)
    await load()
  }, '已删除')
}

function destination(app: App): string {
  return app.target_name ? `${app.target_dir.replace(/\/$/, '')}/${app.target_name}` : `${app.target_dir}/`
}

onMounted(load)
</script>

<template>
  <div class="page">
    <el-card>
      <div class="filter-bar"><el-input v-model="keyword" :prefix-icon="Search" placeholder="搜索应用名称、描述或仓库" clearable aria-label="搜索应用" /><el-select v-model="mode" placeholder="全部构建方式" clearable aria-label="按构建方式筛选"><el-option label="累加构建" value="incremental" /><el-option label="全量构建" value="full" /></el-select><el-button :icon="Refresh" :loading="loading" @click="load">刷新</el-button><span class="count">共 {{ filtered.length }} 个应用</span>
        <el-button v-if="session.can('app.manage')" type="primary" :icon="Plus" @click="open(null)">新建应用</el-button>
      </div>
      <el-table v-loading="loading" :data="filtered" :empty-text="keyword || mode ? '没有匹配的应用，请调整筛选条件' : '还没有应用，点击「新建应用」开始配置'">
        <el-table-column label="项目" min-width="150"><template #default="{ row }"><RouterLink :to="{ name: 'projects', query: { project: row.project_id } }">{{ projectName(row.project_id) }}</RouterLink></template></el-table-column>
        <el-table-column label="应用" min-width="200">
          <template #default="{ row }">
            <b>{{ row.name }}</b>
            <div v-if="row.description" class="hint">{{ row.description }}</div>
          </template>
        </el-table-column>
        <el-table-column label="镜像仓库" min-width="260"><template #default="{ row }"><span class="mono break-word">{{ row.image_repo }}</span></template></el-table-column>
        <el-table-column label="默认方式" width="110"><template #default="{ row }">{{ MODE_TEXT[row.default_mode] }}</template></el-table-column>
        <el-table-column label="镜像内路径" min-width="200"><template #default="{ row }"><span class="mono">{{ destination(row) }}</span></template></el-table-column>
        <el-table-column label="更新时间" width="170"><template #default="{ row }">{{ fmtTime(row.updated_at) }}</template></el-table-column>
        <el-table-column label="操作" width="275">
          <template #default="{ row }">
            <el-button v-if="session.can('app.manage')" link type="primary" @click="open(row)">编辑</el-button>
            <el-button v-if="session.can('build.run')" link type="primary" @click="router.push({ name: 'build', query: { app: row.id } })">去构建</el-button>
            <el-button link type="primary" @click="router.push({ name: 'services', query: { project: row.project_id, app: row.id } })">关联服务</el-button>
            <el-button v-if="session.deployEnabled" link @click="router.push({ name: 'deploy', query: { app: row.id } })">部署</el-button>
            <el-button v-if="session.can('app.manage')" link type="danger" @click="remove(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>
    <AppFormDialog v-model="dialog" :app="editing" :project-id="Number(route.query.project) || undefined" @saved="load" />
  </div>
</template>
