<script setup lang="ts">
/** 应用列表。独立页面可按项目筛选；嵌入项目工作台时固定为该项目。 */
import { Plus, Refresh, Search } from '@element-plus/icons-vue'
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { appApi } from '@/api'
import type { App } from '@/api/types'
import PageHeader from '@/components/PageHeader.vue'
import { confirmAction, notifyError, useAction } from '@/composables/useAction'
import { MODE_TEXT } from '@/constants'
import { usePlatform } from '@/stores/platform'
import { useSession } from '@/stores/session'
import { fmtTime } from '@/utils/format'
import AppFormDialog from './AppFormDialog.vue'

const props = defineProps<{ projectId?: number; embedded?: boolean }>()
const platform = usePlatform()
const session = useSession()
const router = useRouter()
const route = useRoute()
const editing = ref<App | null>(null)
const dialog = ref(false)
const loading = ref(false)
const keyword = ref('')
const mode = ref('')
const project = ref<number | null>(props.projectId ?? (Number(route.query.project) || null))
const { run } = useAction()

const filtered = computed(() => platform.apps.filter((app) =>
  (!project.value || app.project_id === project.value) &&
  (!route.query.app || props.embedded || app.id === Number(route.query.app)) &&
  (!mode.value || app.default_mode === mode.value) &&
  (!keyword.value.trim() || `${app.name} ${app.description} ${app.image_repo}`.toLowerCase().includes(keyword.value.trim().toLowerCase()))))
const canCreate = computed(() => project.value ? session.canProject(project.value, 'app.manage') : session.can('app.manage'))

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
  if (!(await confirmAction(`删除应用“${app.name}”？不会删除 SWR 中的镜像和历史构建记录；仍有关联服务时无法删除。`, '删除应用', true))) return
  if (await run(() => appApi.remove(app.id), '应用已删除')) await load()
}

function destination(app: App): string {
  return app.target_name ? `${app.target_dir.replace(/\/$/, '')}/${app.target_name}` : `${app.target_dir}/`
}

watch(project, (id) => {
  if (!props.embedded) void router.replace({ query: { ...route.query, project: id ? String(id) : undefined } })
})
onMounted(load)
</script>

<template>
  <div :class="{ page: !embedded }">
    <PageHeader v-if="!embedded" title="应用" description="一个应用对应 SWR 中的一个镜像仓库，并保存构建模板。">
      <el-button :icon="Refresh" :loading="loading" @click="load">刷新</el-button>
      <el-button v-if="canCreate" type="primary" :icon="Plus" @click="open(null)">新建应用</el-button>
    </PageHeader>
    <el-card>
      <div class="filter-bar">
        <el-input v-model="keyword" :prefix-icon="Search" placeholder="搜索应用名称、描述或仓库" clearable aria-label="搜索应用" />
        <el-select v-if="!embedded" v-model="project" clearable filterable placeholder="全部项目" aria-label="按项目筛选">
          <el-option v-for="p in session.profile?.projects ?? []" :key="p.id" :label="p.name" :value="p.id" />
        </el-select>
        <el-select v-model="mode" placeholder="全部构建方式" clearable aria-label="按构建方式筛选">
          <el-option :label="MODE_TEXT.incremental" value="incremental" /><el-option :label="MODE_TEXT.full" value="full" />
        </el-select>
        <span class="count">共 {{ filtered.length }} 个应用</span>
        <el-button v-if="embedded && canCreate" type="primary" :icon="Plus" @click="open(null)">新建应用</el-button>
      </div>
      <el-table v-loading="loading" :data="filtered" :empty-text="keyword || mode ? '没有匹配的应用' : '还没有应用'">
        <el-table-column label="应用" min-width="200">
          <template #default="{ row }">
            <b>{{ row.name }}</b>
            <div v-if="row.description" class="hint">{{ row.description }}</div>
          </template>
        </el-table-column>
        <el-table-column v-if="!embedded" label="项目" min-width="140">
          <template #default="{ row }"><RouterLink :to="{ name: 'project-detail', params: { id: row.project_id } }">{{ session.projectName(row.project_id) }}</RouterLink></template>
        </el-table-column>
        <el-table-column label="镜像仓库" min-width="260"><template #default="{ row }"><span class="mono break-word">{{ row.image_repo }}</span></template></el-table-column>
        <el-table-column label="默认方式" width="100"><template #default="{ row }">{{ MODE_TEXT[row.default_mode] }}</template></el-table-column>
        <el-table-column label="镜像内路径" min-width="180"><template #default="{ row }"><span class="mono">{{ destination(row) }}</span></template></el-table-column>
        <el-table-column label="更新时间" width="170"><template #default="{ row }">{{ fmtTime(row.updated_at) }}</template></el-table-column>
        <el-table-column label="操作" width="250" fixed="right">
          <template #default="{ row }">
            <el-button v-if="session.canProject(row.project_id, 'build.run')" link type="primary"
              @click="router.push({ name: 'build', query: { app: row.id } })">构建</el-button>
            <el-button link type="primary" @click="router.push({ name: 'jobs', query: { app: row.id } })">记录</el-button>
            <el-button link type="primary" @click="router.push({ name: 'services', query: { app: row.id } })">服务</el-button>
            <template v-if="session.canProject(row.project_id, 'app.manage')">
              <el-button link type="primary" @click="open(row)">编辑</el-button>
              <el-button link type="danger" @click="remove(row)">删除</el-button>
            </template>
          </template>
        </el-table-column>
      </el-table>
    </el-card>
    <AppFormDialog v-model="dialog" :app="editing" :project-id="project ?? undefined" @saved="load" />
  </div>
</template>
