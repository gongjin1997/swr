<script setup lang="ts">
/** 命名空间：纳管、环境、发布策略、权限自检、成员与所属项目。 */
import { Plus, Refresh, Search } from '@element-plus/icons-vue'
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { clusterApi, namespaceApi, projectApi } from '@/api'
import type { Cluster, ClusterDiscovery, Namespace, NamespacePolicy, Project, RbacReview, Tier } from '@/api/types'
import MembersPanel from '@/components/access/MembersPanel.vue'
import NamespacePolicyForm from '@/components/NamespacePolicyForm.vue'
import { notifyError, notifySuccess, useAction } from '@/composables/useAction'
import { TIER_TAG, TIER_TEXT, TIERS } from '@/constants'
import { useSession } from '@/stores/session'
import TablePager from '@/components/TablePager.vue'
import { usePagination } from '@/composables/usePagination'

const session = useSession()
const namespaces = ref<Namespace[]>([])
const clusters = ref<Cluster[]>([])
const projects = ref<Project[]>([])
const loading = ref(false)
const filters = reactive({ keyword: '', clusterId: null as number | null, tier: '' })
const { busy, run } = useAction()
const manage = computed(() => session.canGlobal('cluster.manage'))
const filtered = computed(() => namespaces.value.filter((n) =>
  (!filters.clusterId || n.cluster_id === filters.clusterId) && (!filters.tier || n.tier === filters.tier) &&
  (!filters.keyword.trim() || `${n.name} ${n.cluster_name}`.toLowerCase().includes(filters.keyword.trim().toLowerCase()))))
const pager = usePagination(() => filtered.value, { resetOn: () => [filters.keyword, filters.clusterId, filters.tier] })
const projectName = (id: number) => projects.value.find((p) => p.id === id)?.name ?? `项目 #${id}`

const defaultPolicy = (): NamespacePolicy => ({
  require_approval: true, require_admin_approval: false, min_approvals: 1, max_replicas: 10, allowed_registry_orgs: [],
  rollout_timeout: 600, freeze: { enabled: false, until: null, reason: '' }, deploy_windows: [],
})
const editor = reactive({ visible: false, namespace: null as Namespace | null, name: '', clusterId: null as number | null,
  tier: 'dev' as Tier, policy: defaultPolicy(), projectId: null as number | null })
const review = reactive({ visible: false, title: '', result: null as RbacReview | null, yaml: '', warning: '' })
const members = reactive({ visible: false, namespace: null as Namespace | null })
const discovery = ref<ClusterDiscovery | null>(null)
const discovering = ref(false)
const discoveryError = ref('')
let discoveryRequest = 0

async function load() {
  loading.value = true
  try {
    const empty = { items: [], total: 0 }
    const [n, c, p] = await Promise.all([
      namespaceApi.list(),
      manage.value ? clusterApi.list() : Promise.resolve(empty),
      projectApi.list().catch(() => empty),
    ])
    namespaces.value = n.items
    clusters.value = c.items as Cluster[]
    projects.value = p.items as Project[]
  } catch (e) {
    notifyError(e)
  } finally {
    loading.value = false
  }
}

function open(namespace: Namespace | null) {
  if (!namespace && !clusters.value.length) return notifyError(new Error('请先在"集群"中纳管并测试一个集群'))
  const source = namespace ? (JSON.parse(JSON.stringify(namespace.policy)) as NamespacePolicy) : defaultPolicy()
  Object.assign(editor, {
    visible: true, namespace, name: namespace?.name ?? '', clusterId: namespace?.cluster_id ?? clusters.value[0]?.id ?? null,
    tier: namespace?.tier ?? 'dev', projectId: null,
    policy: { ...defaultPolicy(), ...source, freeze: { ...defaultPolicy().freeze, ...source.freeze } },
  })
  if (!namespace) void discover()
}

async function discover() {
  const id = editor.clusterId
  const request = ++discoveryRequest
  discovery.value = null
  discoveryError.value = ''
  editor.name = ''
  if (!id) return
  discovering.value = true
  try {
    const result = await clusterApi.discover(id)
    if (request === discoveryRequest) discovery.value = result
  } catch (e) {
    if (request === discoveryRequest) discoveryError.value = e instanceof Error ? e.message : '读取集群命名空间失败'
  } finally {
    if (request === discoveryRequest) discovering.value = false
  }
}

watch(() => editor.tier, (tier) => {
  if (tier === 'staging' || tier === 'prod') editor.policy.require_approval = true
})
watch(() => editor.projectId, (id) => {
  if (!editor.namespace) editor.policy.allowed_registry_orgs = projects.value.find((p) => p.id === id)?.registry_orgs.map((o) => o.namespace) ?? []
})

function cleanedPolicy(): NamespacePolicy {
  const policy = JSON.parse(JSON.stringify(editor.policy)) as NamespacePolicy
  if (!policy.freeze.enabled) policy.freeze = { enabled: false, until: null, reason: '' }
  policy.freeze.reason = policy.freeze.reason.trim()
  policy.deploy_windows.forEach((w, i) => {
    if (!w.days.length) throw new Error(`第 ${i + 1} 个发布窗口至少选择一天`)
    if (!w.start || !w.end) throw new Error(`第 ${i + 1} 个发布窗口需要填写开始和结束时间`)
  })
  return policy
}

async function save() {
  const ok = await run(async () => {
    const policy = cleanedPolicy()
    if (editor.namespace) {
      await namespaceApi.update(editor.namespace.id, { tier: editor.tier, policy })
    } else {
      if (!editor.clusterId) throw new Error('请选择集群')
      await namespaceApi.adopt({ name: editor.name.trim(), cluster_id: editor.clusterId, tier: editor.tier,
        project_id: editor.projectId ?? undefined, policy: { ...policy, service_types: ['ClusterIP'] } })
      await session.load()
    }
  }, editor.namespace ? '命名空间策略已更新' : '命名空间已纳管')
  if (ok) {
    editor.visible = false
    await load()
  }
}

async function runReview(namespace: Namespace) {
  Object.assign(review, { visible: true, title: `${namespace.cluster_name} / ${namespace.name}`, result: null, yaml: '', warning: '' })
  await run(async () => {
    const [result, yaml] = await Promise.all([namespaceApi.review(namespace.id), namespaceApi.rbacYaml(namespace.id)])
    Object.assign(review, { result, yaml: yaml.yaml, warning: yaml.warning })
    if (result.ok) notifySuccess('平台所需的 RBAC 权限齐全')
  })
}

function openMembers(namespace: Namespace) {
  Object.assign(members, { visible: true, namespace })
}

function approvalText(n: Namespace): string {
  if (n.policy.require_admin_approval || n.tier === 'prod') return '平台管理员确认'
  if (n.policy.require_approval || n.tier === 'staging') return `需 ${n.policy.min_approvals} 人审批`
  return '无需审批'
}

function policySummary(n: Namespace): string {
  const p = n.policy
  const parts = [`组织：${p.allowed_registry_orgs?.join('、') || '未限定'}`, `副本 ≤ ${p.max_replicas}`]
  if (p.deploy_windows?.length) parts.push(`${p.deploy_windows.length} 个发布窗口`)
  if (p.freeze?.enabled) parts.push('已冻结')
  return parts.join(' · ')
}

onMounted(load)
</script>

<template>
  <el-card>
    <div class="filter-bar">
      <el-input v-model="filters.keyword" :prefix-icon="Search" placeholder="搜索命名空间或集群" clearable aria-label="搜索命名空间" />
      <el-select v-if="clusters.length" v-model="filters.clusterId" clearable placeholder="全部集群" aria-label="按集群筛选">
        <el-option v-for="c in clusters" :key="c.id" :label="c.name" :value="c.id" />
      </el-select>
      <el-select v-model="filters.tier" clearable placeholder="全部环境" aria-label="按环境筛选">
        <el-option v-for="[value, label] in TIERS" :key="value" :label="label" :value="value" />
      </el-select>
      <el-button :icon="Refresh" :loading="loading" @click="load">刷新</el-button>
      <span class="count">共 {{ filtered.length }} 个命名空间</span>
      <el-button v-if="manage" type="primary" :icon="Plus" @click="open(null)">纳管命名空间</el-button>
    </div>
    <el-table v-loading="loading" :data="pager.rows.value" empty-text="暂无可见命名空间">
      <el-table-column label="命名空间" min-width="190">
        <template #default="{ row }"><b>{{ row.name }}</b><div class="hint">{{ row.cluster_name }}</div></template>
      </el-table-column>
      <el-table-column label="环境" width="130">
        <template #default="{ row }"><el-tag size="small" :type="TIER_TAG[row.tier]" effect="plain">{{ TIER_TEXT[row.tier] }}</el-tag></template>
      </el-table-column>
      <el-table-column label="审批" width="140"><template #default="{ row }">{{ approvalText(row) }}</template></el-table-column>
      <el-table-column label="所属项目" min-width="160">
        <template #default="{ row }">
          <RouterLink v-for="id in row.project_ids" :key="id" :to="{ name: 'project-detail', params: { id } }" class="project-link">{{ projectName(id) }}</RouterLink>
          <span v-if="!row.project_ids.length" class="hint">未分配</span>
        </template>
      </el-table-column>
      <el-table-column label="策略" min-width="240"><template #default="{ row }"><span class="hint">{{ policySummary(row) }}</span></template></el-table-column>
      <el-table-column label="操作" width="200" fixed="right">
        <template #default="{ row }">
          <el-button v-if="session.canNamespace(row.id, 'namespace.member.manage') || session.canGlobal('user.manage')" link type="primary" @click="openMembers(row)">成员</el-button>
          <template v-if="manage">
            <el-button link type="primary" @click="open(row)">策略</el-button>
            <el-button link type="primary" @click="runReview(row)">权限自检</el-button>
          </template>
        </template>
      </el-table-column>
    </el-table>
    <TablePager v-model:page="pager.page.value" v-model:size="pager.size.value" :total="pager.total.value" />
  </el-card>

  <el-dialog v-model="editor.visible" :title="editor.namespace ? `命名空间策略 · ${editor.namespace.name}` : '纳管命名空间'" width="780px" top="5vh" :close-on-click-modal="false">
    <el-form label-position="top">
      <div class="form-grid">
        <template v-if="!editor.namespace">
          <el-form-item label="集群">
            <el-select v-model="editor.clusterId" @change="discover">
              <el-option v-for="c in clusters" :key="c.id" :label="`${c.name} · ${c.status === 'ready' ? '可用' : '未验证'}`" :value="c.id" />
            </el-select>
          </el-form-item>
          <el-form-item label="集群中的命名空间">
            <el-select v-model="editor.name" filterable :loading="discovering" placeholder="从集群自动读取">
              <el-option v-for="n in discovery?.namespaces ?? []" :key="n.name" :label="n.name + (n.managed_id ? '（已纳管）' : '')" :value="n.name"
                :disabled="!!n.managed_id || n.status === 'Terminating'" />
            </el-select>
            <el-button link @click="discover">重新读取</el-button>
          </el-form-item>
          <el-form-item v-if="session.canGlobal('project.manage')" label="分配给项目">
            <el-select v-model="editor.projectId" clearable placeholder="可稍后在项目中分配">
              <el-option v-for="p in projects" :key="p.id" :label="p.name" :value="p.id" />
            </el-select>
          </el-form-item>
        </template>
        <el-form-item label="环境">
          <el-select v-model="editor.tier">
            <el-option v-for="[value, label] in TIERS" :key="value" :label="label" :value="value" />
          </el-select>
        </el-form-item>
      </div>
      <el-alert v-if="discoveryError && !editor.namespace" type="error" :closable="false" :title="discoveryError" />
      <NamespacePolicyForm v-model="editor.policy" :tier="editor.tier" />
    </el-form>
    <template #footer>
      <el-button @click="editor.visible = false">取消</el-button>
      <el-button type="primary" :loading="busy" :disabled="!editor.namespace && (!editor.name || discovering)" @click="save">{{ editor.namespace ? '保存策略' : '连接并纳管' }}</el-button>
    </template>
  </el-dialog>

  <el-dialog v-model="review.visible" :title="`权限自检 · ${review.title}`" width="760px">
    <div v-loading="busy">
      <template v-if="review.result">
        <el-alert v-for="w in review.result.warnings" :key="w" type="warning" :title="w" :closable="false" />
        <el-table :data="review.result.checks" max-height="320" size="small">
          <el-table-column label="API Group" width="160"><template #default="{ row }">{{ row.group || 'core' }}</template></el-table-column>
          <el-table-column prop="resource" label="资源" />
          <el-table-column prop="verb" label="操作" width="100" />
          <el-table-column label="结果" width="90">
            <template #default="{ row }"><el-tag :type="row.allowed ? 'success' : 'danger'">{{ row.allowed ? '允许' : '缺失' }}</el-tag></template>
          </el-table-column>
        </el-table>
      </template>
      <template v-if="review.yaml">
        <h4>参考 Role（供集群管理员审核）</h4>
        <p class="hint">{{ review.warning }}</p>
        <pre class="code-block">{{ review.yaml }}</pre>
      </template>
    </div>
    <template #footer><el-button @click="review.visible = false">关闭</el-button></template>
  </el-dialog>

  <el-drawer v-model="members.visible" :title="`命名空间成员 · ${members.namespace ? `${members.namespace.cluster_name} / ${members.namespace.name}` : ''}`" size="min(820px, 100vw)" destroy-on-close>
    <MembersPanel v-if="members.namespace" scope-type="namespace" :scope-id="members.namespace.id" />
  </el-drawer>
</template>

<style scoped>
.project-link + .project-link { margin-left: 8px; }
</style>
