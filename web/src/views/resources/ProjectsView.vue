<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useRoute } from 'vue-router'
import { namespaceApi, projectApi } from '@/api'
import type { Namespace, Project, RegistryOrg } from '@/api/types'
import RegistryOrgsEditor from '@/components/RegistryOrgsEditor.vue'
import { confirmAction, errorText, notifyError, notifySuccess, useAction } from '@/composables/useAction'
import { useSession } from '@/stores/session'

const session = useSession()
const route = useRoute()
const visibleProjects = computed(() => projects.value.filter(p => !route.query.project || p.id === Number(route.query.project)))
const projects = ref<Project[]>([])
const namespaces = ref<Namespace[]>([])
const loading = ref(false)
const { busy, run } = useAction()

const editor = reactive({ visible: false, id: null as number | null, slug: '', name: '', orgs: [] as RegistryOrg[], isDefault: false })
const assign = reactive({ visible: false, project: null as Project | null, selected: [] as number[] })

const namespaceOptions = computed(() =>
  namespaces.value.map((n) => ({ key: n.id, label: `${n.cluster_name} / ${n.name}（${n.tier}）` })),
)

async function load() {
  loading.value = true
  try {
    const [p, n] = await Promise.all([projectApi.list(), namespaceApi.list().catch(() => ({ items: [], total: 0 }))])
    projects.value = p.items
    namespaces.value = n.items
  } catch (e) {
    notifyError(e)
  } finally {
    loading.value = false
  }
}

function namespaceLabel(id: number): string {
  const n = namespaces.value.find((x) => x.id === id)
  return n ? `${n.cluster_name} / ${n.name}` : `#${id}`
}

function openEditor(project: Project | null) {
  Object.assign(editor, {
    visible: true,
    id: project?.id ?? null,
    slug: project?.slug ?? '',
    name: project?.name ?? '',
    orgs: (project?.registry_orgs ?? []).map((o) => ({ namespace: o.namespace, base_prefixes: [...o.base_prefixes] })),
    isDefault: project?.slug === 'default',
  })
}

async function save() {
  const data = {
    slug: editor.slug.trim().toLowerCase(),
    name: editor.name.trim(),
    registry_orgs: editor.orgs.filter((o) => o.namespace.trim()).map((o) => ({ namespace: o.namespace.trim(), base_prefixes: o.base_prefixes })),
  }
  const ok = await run(() => (editor.id ? projectApi.update(editor.id, data) : projectApi.create(data)), '项目已保存')
  if (ok) {
    editor.visible = false
    await load()
  }
}

async function remove(project: Project) {
  if (!(await confirmAction(`删除项目“${project.name}”？仍有关联应用或任务时无法删除。`, '删除项目', true))) return
  if (await run(() => projectApi.remove(project.id), '项目已删除')) await load()
}

function openAssign(project: Project) {
  Object.assign(assign, { visible: true, project, selected: [...project.namespace_ids] })
}

/** 分配是逐个调用接口完成的；部分失败时提示失败项，已成功的保留。 */
async function saveAssign() {
  const project = assign.project
  if (!project) return
  const before = new Set(project.namespace_ids)
  const after = new Set(assign.selected)
  const failures: string[] = []
  busy.value = true
  for (const id of after) {
    if (!before.has(id)) await projectApi.assignNamespace(project.id, id).catch((e) => failures.push(`${namespaceLabel(id)}：${errorText(e)}`))
  }
  for (const id of before) {
    if (!after.has(id)) await projectApi.unassignNamespace(project.id, id).catch((e) => failures.push(`${namespaceLabel(id)}：${errorText(e)}`))
  }
  busy.value = false
  if (failures.length) notifyError(new Error('部分命名空间未能更新：' + failures.join('；')))
  else notifySuccess('命名空间分配已更新')
  assign.visible = false
  await load()
}

onMounted(load)
</script>

<template>
  <el-card>
    <template #header>
      <div class="card-title">
        <span>项目</span>
        <el-button v-if="session.can('project.manage')" type="primary" @click="openEditor(null)">新建项目</el-button>
      </div>
    </template>
    <el-table v-loading="loading" :data="visibleProjects" empty-text="暂无可见项目">
      <el-table-column label="项目" min-width="180">
        <template #default="{ row }"><b>{{ row.name }}</b><div class="hint mono">{{ row.slug }}</div></template>
      </el-table-column>
      <el-table-column label="SWR 授权" min-width="260">
        <template #default="{ row }">
          <div v-for="o in row.registry_orgs" :key="o.namespace">
            <span class="mono">{{ o.namespace }}</span>：{{ o.base_prefixes.length ? o.base_prefixes.join(', ') : '全部仓库' }}
          </div>
          <span v-if="!row.registry_orgs.length" class="hint">未授权</span>
        </template>
      </el-table-column>
      <el-table-column label="命名空间" min-width="200">
        <template #default="{ row }">
          <div v-for="id in row.namespace_ids" :key="id">{{ namespaceLabel(id) }}</div>
          <span v-if="!row.namespace_ids.length" class="hint">未分配</span>
        </template>
      </el-table-column>
      <el-table-column label="关联资源" min-width="200"><template #default="{ row }"><div class="toolbar"><RouterLink :to="{ name: 'apps', query: { project: row.id } }">应用</RouterLink><RouterLink :to="{ name: 'services', query: { project: row.id } }">服务</RouterLink><RouterLink v-if="session.deployEnabled" :to="{ name: 'deploy', query: { project: row.id } }">部署与审批</RouterLink></div></template></el-table-column>
      <el-table-column label="操作" width="230">
        <template #default="{ row }">
          <template v-if="session.can('project.manage')">
            <el-button link type="primary" @click="openEditor(row)">编辑</el-button>
            <el-button link type="primary" @click="openAssign(row)">分配命名空间</el-button>
            <el-button v-if="row.slug !== 'default'" link type="danger" @click="remove(row)">删除</el-button>
          </template>
          <span v-else class="hint">只读</span>
        </template>
      </el-table-column>
    </el-table>
  </el-card>

  <el-dialog v-model="editor.visible" :title="editor.id ? '编辑项目' : '新建项目'" width="720px">
    <el-form label-position="top">
      <div class="form-grid">
        <el-form-item label="项目 slug">
          <el-input v-model="editor.slug" :disabled="editor.isDefault" maxlength="63" placeholder="小写字母、数字和 -" />
        </el-form-item>
        <el-form-item label="项目名称"><el-input v-model="editor.name" maxlength="64" /></el-form-item>
      </div>
      <el-form-item label="SWR 组织和镜像仓库前缀"><RegistryOrgsEditor v-model="editor.orgs" /></el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="editor.visible = false">取消</el-button>
      <el-button type="primary" :loading="busy" @click="save">保存</el-button>
    </template>
  </el-dialog>

  <el-dialog v-model="assign.visible" :title="`分配命名空间 · ${assign.project?.name ?? ''}`" width="720px">
    <el-transfer v-model="assign.selected" :data="namespaceOptions" :titles="['可分配', '已分配']" filterable />
    <template #footer>
      <el-button @click="assign.visible = false">取消</el-button>
      <el-button type="primary" :loading="busy" @click="saveAssign">保存分配</el-button>
    </template>
  </el-dialog>
</template>
