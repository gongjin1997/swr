<script setup lang="ts">
/** 项目工作台：项目内的应用、服务、构建、发布与成员集中在一处，各标签页复用对应的列表页面。 */
import { Edit, Plus } from '@element-plus/icons-vue'
import { computed, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { namespaceApi, projectApi } from '@/api'
import type { Namespace, Project } from '@/api/types'
import MembersPanel from '@/components/access/MembersPanel.vue'
import PageHeader from '@/components/PageHeader.vue'
import { confirmAction, errorText, notifyError, notifySuccess, useAction } from '@/composables/useAction'
import { TIER_TAG, TIER_TEXT } from '@/constants'
import { useSession } from '@/stores/session'
import AppListView from '@/views/apps/AppListView.vue'
import JobListView from '@/views/jobs/JobListView.vue'
import ReleasesView from '@/views/releases/ReleasesView.vue'
import ServicesView from '@/views/services/ServicesView.vue'
import ProjectFormDialog from './ProjectFormDialog.vue'

const props = defineProps<{ id: number }>()
const session = useSession()
const route = useRoute()
const router = useRouter()
const project = ref<Project | null>(null)
const namespaces = ref<Namespace[]>([])
const loading = ref(false)
const editing = ref(false)
const { busy, run } = useAction()
const assign = reactive({ visible: false, selected: [] as number[] })

const TABS = ['overview', 'apps', 'services', 'jobs', 'releases', 'members'] as const
type Tab = typeof TABS[number]
const tab = computed<Tab>({
  get: () => (TABS as readonly string[]).includes(String(route.query.tab)) ? route.query.tab as Tab : 'overview',
  set: (value) => void router.replace({ query: { tab: value === 'overview' ? undefined : value } }),
})
const manage = computed(() => session.canGlobal('project.manage'))
const showMembers = computed(() => session.canProject(props.id, 'project.member.manage') || session.canGlobal('user.manage'))
const assigned = computed(() => namespaces.value.filter((n) => project.value?.namespace_ids.includes(n.id)))
const namespaceOptions = computed(() => namespaces.value.map((n) => ({
  key: n.id, label: `${n.cluster_name} / ${n.name}（${TIER_TEXT[n.tier]}）`,
})))

async function load() {
  loading.value = true
  try {
    const [p, n] = await Promise.all([projectApi.get(props.id),
      namespaceApi.list().catch(() => ({ items: [] as Namespace[], total: 0 }))])
    project.value = p
    namespaces.value = n.items
  } catch (e) {
    notifyError(e)
    await router.replace({ name: 'projects' })
  } finally {
    loading.value = false
  }
}

function openAssign() {
  assign.selected = [...(project.value?.namespace_ids ?? [])]
  assign.visible = true
}

/** 分配按命名空间逐个提交；部分失败时提示失败项，成功的保留。 */
async function saveAssign() {
  const current = project.value
  if (!current) return
  const before = new Set(current.namespace_ids)
  const after = new Set(assign.selected)
  const label = (id: number) => namespaceOptions.value.find((o) => o.key === id)?.label ?? `#${id}`
  const failures: string[] = []
  busy.value = true
  try {
    for (const id of after) {
      if (!before.has(id)) await projectApi.assignNamespace(current.id, id).catch((e) => failures.push(`${label(id)}：${errorText(e)}`))
    }
    for (const id of before) {
      if (!after.has(id)) await projectApi.unassignNamespace(current.id, id).catch((e) => failures.push(`${label(id)}：${errorText(e)}`))
    }
  } finally {
    busy.value = false
  }
  if (failures.length) notifyError(new Error('部分命名空间未能更新：' + failures.join('；')))
  else notifySuccess('命名空间分配已更新')
  assign.visible = false
  await load()
}

async function unassign(ns: Namespace) {
  if (!(await confirmAction(`取消把 ${ns.cluster_name} / ${ns.name} 分配给本项目？项目在其中仍有服务时无法取消。`, '取消分配', true))) return
  if (await run(() => projectApi.unassignNamespace(props.id, ns.id), '已取消分配')) await load()
}

async function remove() {
  const current = project.value
  if (!current || !(await confirmAction(`删除项目“${current.name}”？仍有关联应用、构建、发布记录或成员授权时无法删除。`, '删除项目', true))) return
  if (await run(async () => {
    await projectApi.remove(current.id)
    await session.load()
  }, '项目已删除')) await router.replace({ name: 'projects' })
}

watch(() => props.id, load, { immediate: true })
</script>

<template>
  <div class="page" v-loading="loading && !project">
    <template v-if="project">
      <PageHeader :title="project.name">
        <template #meta>
          <p class="hint"><span class="mono">{{ project.slug }}</span> · 你在本项目：
            {{ session.canProject(project.id, 'project.member.manage') ? '可管理成员' : session.canProject(project.id, 'app.manage') ? '可维护应用' : '只读' }}</p>
        </template>
        <template v-if="manage">
          <el-button :icon="Edit" @click="editing = true">编辑项目</el-button>
          <el-button v-if="project.slug !== 'default'" type="danger" plain @click="remove">删除</el-button>
        </template>
      </PageHeader>

      <el-tabs v-model="tab" class="project-tabs">
        <el-tab-pane label="概览" name="overview">
          <div class="grid-2">
            <el-card>
              <template #header><span class="card-title">SWR 授权</span></template>
              <el-table :data="project.registry_orgs" empty-text="未授权任何 SWR 组织，项目内的应用无法推送镜像">
                <el-table-column label="组织" min-width="140"><template #default="{ row }"><span class="mono">{{ row.namespace }}</span></template></el-table-column>
                <el-table-column label="仓库前缀" min-width="200">
                  <template #default="{ row }">{{ row.base_prefixes.length ? row.base_prefixes.join('、') : '全部仓库' }}</template>
                </el-table-column>
              </el-table>
            </el-card>
            <el-card>
              <template #header>
                <div class="card-title">
                  <span>命名空间</span>
                  <el-button v-if="manage" size="small" :icon="Plus" @click="openAssign">分配命名空间</el-button>
                </div>
              </template>
              <el-table :data="assigned" empty-text="还没有分配命名空间，项目内的应用无法创建服务">
                <el-table-column label="命名空间" min-width="180"><template #default="{ row }">{{ row.cluster_name }} / {{ row.name }}</template></el-table-column>
                <el-table-column label="环境" width="130"><template #default="{ row }"><el-tag size="small" :type="TIER_TAG[row.tier]" effect="plain">{{ TIER_TEXT[row.tier] }}</el-tag></template></el-table-column>
                <el-table-column v-if="manage" label="操作" width="90"><template #default="{ row }"><el-button link type="danger" @click="unassign(row)">取消</el-button></template></el-table-column>
              </el-table>
              <p v-if="project.namespace_ids.length > assigned.length" class="hint">另有 {{ project.namespace_ids.length - assigned.length }} 个命名空间你没有查看权限。</p>
            </el-card>
          </div>
        </el-tab-pane>
        <el-tab-pane label="应用" name="apps" lazy><AppListView :project-id="project.id" embedded /></el-tab-pane>
        <el-tab-pane label="服务" name="services" lazy><ServicesView :project-id="project.id" embedded /></el-tab-pane>
        <el-tab-pane label="构建记录" name="jobs" lazy><JobListView :project-id="project.id" embedded /></el-tab-pane>
        <el-tab-pane v-if="session.deployEnabled" label="发布记录" name="releases" lazy><ReleasesView :project-id="project.id" embedded /></el-tab-pane>
        <el-tab-pane v-if="showMembers" label="成员" name="members" lazy>
          <el-card><MembersPanel scope-type="project" :scope-id="project.id" /></el-card>
        </el-tab-pane>
      </el-tabs>
    </template>

    <ProjectFormDialog v-model="editing" :project="project" @saved="load" />
    <el-dialog v-model="assign.visible" :title="`分配命名空间 · ${project?.name ?? ''}`" width="720px">
      <el-transfer v-model="assign.selected" :data="namespaceOptions" :titles="['可分配', '已分配']" filterable />
      <template #footer>
        <el-button @click="assign.visible = false">取消</el-button>
        <el-button type="primary" :loading="busy" @click="saveAssign">保存分配</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.project-tabs :deep(.el-tabs__header) { margin-bottom: 16px; }
</style>
