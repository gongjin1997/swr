<script setup lang="ts">
import { Plus, Refresh, Search } from '@element-plus/icons-vue'
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { namespaceApi, projectApi } from '@/api'
import type { Namespace, Project, TagType } from '@/api/types'
import PageHeader from '@/components/PageHeader.vue'
import { confirmAction, notifyError, useAction } from '@/composables/useAction'
import { TIER_TAG, TIER_TEXT } from '@/constants'
import { useSession } from '@/stores/session'
import { fmtTime } from '@/utils/format'
import ProjectFormDialog from './ProjectFormDialog.vue'
import TablePager from '@/components/TablePager.vue'
import { usePagination } from '@/composables/usePagination'

const session = useSession()
const router = useRouter()
const projects = ref<Project[]>([])
const namespaces = ref<Map<number, Namespace>>(new Map())
const loading = ref(false)
const keyword = ref('')
const editing = ref<Project | null>(null)
const dialog = ref(false)
const { run } = useAction()

const filtered = computed(() => projects.value.filter((p) =>
  !keyword.value.trim() || `${p.name} ${p.slug}`.toLowerCase().includes(keyword.value.trim().toLowerCase())))
const pager = usePagination(() => filtered.value, { resetOn: () => keyword.value })

function namespaceTag(id: number): { label: string; type: TagType } {
  const ns = namespaces.value.get(id)
  if (!ns) return { label: `命名空间 #${id}`, type: 'info' }
  return { label: `${ns.cluster_name} / ${ns.name} · ${TIER_TEXT[ns.tier]}`, type: TIER_TAG[ns.tier] ?? 'info' }
}

/** 当前账号在项目内的身份概括。 */
function access(project: Project): string {
  if (session.canProject(project.id, 'project.member.manage')) return '可管理成员'
  if (session.canProject(project.id, 'app.manage')) return '可维护应用'
  if (session.canProject(project.id, 'build.run')) return '可构建'
  return '只读'
}

async function load() {
  loading.value = true
  try {
    const [p, n] = await Promise.all([projectApi.list(), namespaceApi.list().catch(() => ({ items: [] as Namespace[], total: 0 }))])
    projects.value = p.items
    namespaces.value = new Map(n.items.map((ns) => [ns.id, ns]))
  } catch (e) {
    notifyError(e)
  } finally {
    loading.value = false
  }
}

function open(project: Project | null) {
  editing.value = project
  dialog.value = true
}

async function remove(project: Project) {
  if (!(await confirmAction(`删除项目“${project.name}”？仍有关联应用、构建、发布记录或成员授权时无法删除。`, '删除项目', true))) return
  if (await run(async () => {
    await projectApi.remove(project.id)
    await session.load()
  }, '项目已删除')) await load()
}

onMounted(load)
</script>

<template>
  <div class="page">
    <PageHeader title="项目" description="项目是应用、构建、服务与成员的归属单位；进入项目可查看其全部资源。">
      <el-button :icon="Refresh" :loading="loading" @click="load">刷新</el-button>
      <el-button v-if="session.canGlobal('project.manage')" type="primary" :icon="Plus" @click="open(null)">新建项目</el-button>
    </PageHeader>
    <el-card>
      <div class="filter-bar">
        <el-input v-model="keyword" :prefix-icon="Search" placeholder="搜索项目名称或标识" clearable aria-label="搜索项目" />
        <span class="count">共 {{ filtered.length }} 个项目</span>
      </div>
      <el-table v-loading="loading" :data="pager.rows.value" empty-text="暂无可见项目">
        <el-table-column label="项目" min-width="150">
          <template #default="{ row }">
            <RouterLink :to="{ name: 'project-detail', params: { id: row.id } }"><b>{{ row.name }}</b></RouterLink>
            <div class="hint mono">{{ row.slug }}</div>
          </template>
        </el-table-column>
        <el-table-column label="SWR 授权" min-width="190">
          <template #default="{ row }">
            <div v-for="o in row.registry_orgs" :key="o.namespace" class="hint">
              <span class="mono">{{ o.namespace }}</span>：{{ o.base_prefixes.length ? o.base_prefixes.join('、') : '全部仓库' }}
            </div>
            <span v-if="!row.registry_orgs.length" class="hint">未授权</span>
          </template>
        </el-table-column>
        <el-table-column label="命名空间" min-width="190">
          <template #default="{ row }">
            <div class="tags">
              <el-tag v-for="id in row.namespace_ids" :key="id" size="small" :type="namespaceTag(id).type" effect="plain">
                {{ namespaceTag(id).label }}
              </el-tag>
              <span v-if="!row.namespace_ids.length" class="hint">未分配</span>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="我的权限" width="100"><template #default="{ row }">{{ access(row) }}</template></el-table-column>
        <el-table-column label="更新时间" width="150"><template #default="{ row }">{{ fmtTime(row.updated_at) }}</template></el-table-column>
        <el-table-column label="操作" width="150" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" @click="router.push({ name: 'project-detail', params: { id: row.id } })">进入</el-button>
            <template v-if="session.canGlobal('project.manage')">
              <el-button link type="primary" @click="open(row)">编辑</el-button>
              <el-button v-if="row.slug !== 'default'" link type="danger" @click="remove(row)">删除</el-button>
            </template>
          </template>
        </el-table-column>
      </el-table>
      <TablePager v-model:page="pager.page.value" v-model:size="pager.size.value" :total="pager.total.value" />
    </el-card>
    <ProjectFormDialog v-model="dialog" :project="editing" @saved="load" />
  </div>
</template>

<style scoped>
.tags { display: flex; flex-wrap: wrap; gap: 4px; }
</style>
