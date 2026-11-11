<script setup lang="ts">
/**
 * 服务详情：运行状态（Pod、事件、访问地址）、配置（生效配置与服务层配置）、对象清单（预览、服务端校验、发布快照）、
 * 发布记录（审批、日志、回滚）与修订历史。托管服务与纳管服务共用，按服务方式显示可用的操作。
 */
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { deploymentApi, environmentApi, serviceApi } from '@/api'
import { idempotencyKey } from '@/api/http'
import type { Deployment, EnvironmentDetail, FileSetOption, ManagedSpec, Revision, RevisionDiff, Service, ServiceRuntime } from '@/api/types'
import ConfigLayerPanel from '@/components/config/ConfigLayerPanel.vue'
import EffectiveConfigTable from '@/components/config/EffectiveConfigTable.vue'
import DeployDialog from '@/components/DeployDialog.vue'
import DeploymentLogDialog from '@/components/DeploymentLogDialog.vue'
import PageHeader from '@/components/PageHeader.vue'
import ServiceRuntimeView from '@/components/runtime/ServiceRuntime.vue'
import ManagedSpecForm from '@/components/service/ManagedSpecForm.vue'
import ManifestPanel from '@/components/service/ManifestPanel.vue'
import StatusTag from '@/components/StatusTag.vue'
import TablePager from '@/components/TablePager.vue'
import { confirmAction, notifyError, useAction } from '@/composables/useAction'
import { usePagination, useServerPage } from '@/composables/usePagination'
import { usePolling } from '@/composables/usePolling'
import {
  CONFIG_CHANGE_TEXT, CONFIG_KIND_TEXT, CONFIG_SCOPE_TEXT, DEPLOYMENT_ACTIVE, DEPLOYMENT_KIND_TEXT, RUNTIME_STATE_TAG,
  RUNTIME_STATE_TEXT, SERVICE_MODE_HINT, SERVICE_MODE_TEXT, TIER_TAG, TIER_TEXT,
} from '@/constants'
import { useSession } from '@/stores/session'
import { fmtAgo, fmtTime, shortDigest, shortImage } from '@/utils/format'

const props = defineProps<{ id: number }>()
const session = useSession()
const router = useRouter()
const route = useRoute()
const service = ref<Service | null>(null)
const environment = ref<EnvironmentDetail | null>(null)
const revisions = ref<Revision[]>([])
const deployments = ref<Deployment[]>([])
const lastSuccess = ref<Deployment | null>(null)
const deploymentPage = useServerPage(10)
const revisionPage = usePagination(() => revisions.value, { size: 10 })
const loading = ref(false)
const { busy, run } = useAction()
const deployOpen = ref(false)
const logId = ref<number | null>(null)
const compare = reactive({ from: null as number | null, to: null as number | null, result: null as RevisionDiff | null })
const editor = reactive({ visible: false, advanced: false, name: '', container: '', json: '', spec: null as ManagedSpec | null })
const fileSets = ref<FileSetOption[]>([])
const secret = reactive({ visible: false, key: '', value: '' })
const runtime = ref<ServiceRuntime | null>(null)
const runtimeLoading = ref(false)
const runtimeError = ref('')

const TABS = ['overview', 'config', 'manifest', 'deployments', 'revisions'] as const
type Tab = (typeof TABS)[number]
const tab = computed<Tab>({
  get: () => {
    const value = String(route.query.tab ?? 'overview') as Tab
    return TABS.includes(value) && (value !== 'manifest' || managed.value) ? value : 'overview'
  },
  set: (value) => { void router.replace({ query: { ...route.query, tab: value } }) },
})

const managed = computed(() => service.value?.mode === 'managed')
const can = (permission: string) => !!service.value && (service.value.permissions?.includes(permission)
  ?? session.canNamespace(service.value.namespace_id, permission))
const imageTarget = computed(() => (service.value?.spec.mode === 'image_only' ? service.value.spec.target : null))
const secretKeys = computed(() => Object.entries(revisions.value[0]?.secret_versions ?? {}))
const pendingRevision = computed(() => !!service.value?.deployed_revision_id
  && service.value.deployed_revision_id !== service.value.current_revision_id)
const configStatus = computed(() => service.value?.config_status)
const summary = computed(() => runtime.value?.summary)
const neverDeployed = computed(() => !service.value?.deployed_revision_id)
const canAdopt = computed(() => managed.value && neverDeployed.value && !!runtime.value?.found && can('service.deploy'))
const restartsTone = computed(() => ((summary.value?.restarts ?? 0) > 0 ? 'warning' : ''))
const changeText = (change: string) => CONFIG_CHANGE_TEXT[change] ?? change
const scopeText = (scope?: string) => (scope ? CONFIG_SCOPE_TEXT[scope as keyof typeof CONFIG_SCOPE_TEXT] ?? scope : '')
const kindText = (kind: string) => CONFIG_KIND_TEXT[kind as keyof typeof CONFIG_KIND_TEXT] ?? kind

const { run: pollRuntime } = usePolling(async (isCurrent) => {
  runtimeLoading.value = true
  runtimeError.value = ''
  try {
    const result = await serviceApi.runtime(props.id)
    if (!isCurrent()) return false
    runtime.value = result
    return result.summary.state === 'progressing' || deployments.value.some((d) => DEPLOYMENT_ACTIVE.has(d.status))
  } catch (e) {
    if (isCurrent()) runtimeError.value = e instanceof Error ? e.message : '集群信息获取失败'
    return false
  } finally {
    if (isCurrent()) runtimeLoading.value = false
  }
}, 5000)

async function loadDeployments() {
  try {
    const [page, success] = await Promise.all([
      deploymentApi.list({ service_id: props.id, limit: deploymentPage.pageSize.value, offset: deploymentPage.offset.value }),
      deploymentApi.list({ service_id: props.id, status: 'success', limit: 1 }),
    ])
    deployments.value = page.items
    deploymentPage.total.value = page.total
    lastSuccess.value = success.items[0] ?? null
  } catch {
    deployments.value = []
  }
}

async function load() {
  loading.value = true
  try {
    const [s, r] = await Promise.all([serviceApi.get(props.id), serviceApi.revisions(props.id)])
    service.value = s
    revisions.value = r.items
    if (r.items.length >= 2 && compare.from === null) {
      compare.to = r.items[0].id
      compare.from = r.items[1].id
    }
    await loadDeployments()
    void pollRuntime()
    if (s.mode === 'managed' && !environment.value) {
      environment.value = await environmentApi.get(s.namespace_id).catch(() => null)
    }
  } catch (e) {
    notifyError(e)
    await router.replace({ name: 'services' })
  } finally {
    loading.value = false
  }
}

async function runDiff() {
  if (compare.from === null || compare.to === null) return
  const [from, to] = [compare.from, compare.to]
  await run(async () => {
    compare.result = await serviceApi.diff(props.id, from, to)
  })
}

async function openEditor() {
  const current = service.value
  if (!current) return
  if (current.spec.mode === 'managed') {
    try {
      const effective = await serviceApi.effective(current.id)
      fileSets.value = effective.files.map((f) => ({ name: f.name, kind: f.kind, keys: f.keys, scope: scopeText(f.origin.scope_type) }))
    } catch (e) {
      notifyError(e)
    }
    Object.assign(editor, { visible: true, advanced: false, json: JSON.stringify(current.spec, null, 2),
      spec: JSON.parse(JSON.stringify(current.spec)) as ManagedSpec })
    return
  }
  Object.assign(editor, {
    visible: true, advanced: false, name: current.spec.target.name, container: current.spec.target.container,
    json: JSON.stringify(current.spec, null, 2), spec: null,
  })
}

async function saveRevision() {
  const current = service.value
  if (!current) return
  const ok = await run(async () => {
    let spec: unknown
    if (editor.advanced) {
      try {
        spec = JSON.parse(editor.json)
      } catch {
        throw new Error('配置不是有效的 JSON')
      }
    } else if (editor.spec) {
      spec = editor.spec
    } else {
      spec = { mode: 'image_only', target: { kind: 'Deployment', name: editor.name.trim(), container: editor.container.trim() } }
    }
    await serviceApi.updateSpec(props.id, current.current_revision_id, spec)
  }, '已保存新的配置修订；发布后生效')
  if (ok) {
    editor.visible = false
    await load()
  }
}

async function saveSecret() {
  const ok = await run(() => serviceApi.setSecret(props.id, secret.key.trim(), secret.value), '密钥已更新，生成了新的配置修订')
  if (ok) {
    Object.assign(secret, { visible: false, key: '', value: '' })
    await load()
  }
}

async function submitted(message: string, action: () => Promise<unknown>) {
  if (await run(action, message)) {
    await load()
    tab.value = 'deployments'
  }
}

async function rollback(d: Deployment) {
  if (!(await confirmAction(`回滚到发布 #${d.id}（${d.job_id ? `构建 #${d.job_id}` : '线上镜像'}，修订 ${d.revision_id}）？${managed.value ? '配置按该次发布的快照恢复。' : ''}`, '回滚服务', true))) return
  await submitted('回滚任务已提交', () => serviceApi.rollback(props.id, idempotencyKey(), { target_deployment_id: d.id, remark: '' }))
}

async function restart() {
  if (!(await confirmAction('以最近一次成功发布的镜像和配置原样重新发布（Pod 会滚动重启）？', '重新发布'))) return
  await submitted('重新发布任务已提交', () => serviceApi.restart(props.id, idempotencyKey(), ''))
}

async function publishLatestConfig() {
  const last = lastSuccess.value
  if (!last) return
  if (!(await confirmAction('沿用当前镜像，按最新的服务修订与各层配置发布？', '发布最新配置'))) return
  await submitted('配置发布任务已提交', () => (last.job_id
    ? serviceApi.deploy(props.id, idempotencyKey(), { job_id: last.job_id, use_latest_config: true, remark: '发布最新配置' })
    : serviceApi.adopt(props.id, idempotencyKey(), '发布最新配置')))
}

async function adopt() {
  if (!(await confirmAction('由平台接管集群中的现有对象：镜像沿用正在运行的 digest，配置按平台中的版本下发。下发前会先做服务端校验。',
    '接管发布'))) return
  await submitted('接管发布已提交', () => serviceApi.adopt(props.id, idempotencyKey(), '接管已有服务'))
}

async function decide(d: Deployment, approve: boolean) {
  if (!approve && !(await confirmAction('拒绝此服务发布？', '拒绝发布', true))) return
  if (await run(() => (approve ? deploymentApi.approve(d.id) : deploymentApi.reject(d.id)), approve ? '审批已提交' : '发布已拒绝')) await load()
}

async function remove() {
  const text = managed.value ? '删除服务记录与服务层配置？集群中的对象保留不删除。' : '删除服务记录？不会删除集群中的 Deployment。'
  if (!(await confirmAction(`${text}存在进行中的发布时无法删除。`, '删除服务', true))) return
  if (await run(() => serviceApi.remove(props.id), '服务记录已删除')) await router.replace({ name: 'services' })
}

watch(() => props.id, load)
onMounted(load)
</script>

<template>
  <div class="page" v-loading="loading && !service">
    <template v-if="service">
      <PageHeader :title="service.name">
        <template #meta>
          <div class="meta">
            <el-tooltip :content="SERVICE_MODE_HINT[service.mode]"><el-tag size="small" effect="dark" :type="managed ? 'primary' : 'info'">{{ SERVICE_MODE_TEXT[service.mode] }}</el-tag></el-tooltip>
            <el-tag v-if="summary" size="small" :type="RUNTIME_STATE_TAG[summary.state]">{{ RUNTIME_STATE_TEXT[summary.state] }}</el-tag>
            <el-tag size="small" :type="TIER_TAG[service.tier]" effect="plain">{{ TIER_TEXT[service.tier] }}</el-tag>
            <span class="hint">
              <RouterLink :to="{ name: 'project-detail', params: { id: service.project_id } }">{{ service.project_name }}</RouterLink>
              / <RouterLink :to="{ name: 'jobs', query: { app: service.app_id } }">{{ service.app_name }}</RouterLink>
              · <RouterLink :to="{ name: 'environment-detail', params: { id: service.namespace_id } }">{{ service.cluster_name }} / {{ service.namespace_name }}</RouterLink>
              · <span class="mono">{{ service.workload }}</span>
            </span>
          </div>
        </template>
        <el-button v-if="canAdopt" type="warning" @click="adopt">接管发布</el-button>
        <el-button v-if="session.deployEnabled && can('service.deploy')" type="primary" @click="deployOpen = true">发布</el-button>
        <el-button v-if="session.deployEnabled && can('service.operate') && lastSuccess" @click="restart">重新发布</el-button>
        <el-button v-if="can('service.config')" @click="openEditor">修改配置</el-button>
        <el-button v-if="!managed && can('secret.manage')" @click="secret.visible = true">设置密钥</el-button>
        <el-button v-if="can('service.delete')" type="danger" plain @click="remove">删除</el-button>
      </PageHeader>

      <div class="cards">
        <div class="card">
          <span class="label">运行状态</span>
          <b :class="`tone-${summary ? RUNTIME_STATE_TAG[summary.state] : 'info'}`">{{ summary ? RUNTIME_STATE_TEXT[summary.state] : runtimeError ? '无法读取' : '读取中…' }}</b>
          <span class="hint">{{ summary?.message || runtimeError }}</span>
        </div>
        <div class="card">
          <span class="label">就绪副本</span>
          <b class="num">{{ summary ? `${summary.ready} / ${summary.desired}` : '-' }}</b>
          <span class="hint">已更新 {{ summary?.updated ?? '-' }} · 可用 {{ summary?.available ?? '-' }}</span>
        </div>
        <div class="card">
          <span class="label">重启次数</span>
          <b class="num" :class="restartsTone && 'tone-warning'">{{ summary?.restarts ?? '-' }}</b>
          <span class="hint">{{ summary ? `${summary.pods} 个 Pod · ${summary.warning_events} 条警告事件` : '' }}</span>
        </div>
        <div class="card wide">
          <span class="label">当前镜像</span>
          <b class="mono break-word">{{ shortImage(summary?.image) || '-' }}</b>
          <span class="hint mono">{{ summary?.running_images.length ? summary.running_images.map((i) => shortDigest(i)).join('、') : '' }}</span>
        </div>
        <div class="card">
          <span class="label">最近一次成功发布</span>
          <b>{{ lastSuccess ? `#${lastSuccess.id} ${DEPLOYMENT_KIND_TEXT[lastSuccess.kind] ?? lastSuccess.kind}` : '尚未发布' }}</b>
          <span class="hint">{{ lastSuccess ? `${fmtAgo(lastSuccess.finished_at || lastSuccess.created_at)} · ${lastSuccess.operator}` : '' }}</span>
        </div>
      </div>

      <el-alert v-if="managed && neverDeployed && runtime?.found" type="warning" :closable="false" show-icon class="section-gap"
        title="集群中已有同名对象，尚未由平台接管">
        <template #default>导入的服务第一次下发请使用“接管发布”：镜像沿用正在运行的版本，平台按配置接管 Deployment、Service、Ingress 等对象。可以先在“对象清单”中做服务端校验，查看与线上的差异。</template>
      </el-alert>
      <el-alert v-if="pendingRevision || configStatus?.pending" type="info" :closable="false" show-icon class="section-gap"
        title="有尚未发布的配置变更">
        <template #default>
          <div v-if="pendingRevision">服务配置：当前修订 {{ service.current_revision_id }}，已部署修订 {{ service.deployed_revision_id }}。</div>
          <div v-for="c in configStatus?.changes ?? []" :key="`${c.kind}-${c.name}-${c.set_id ?? ''}`">
            {{ scopeText(c.scope_type) }}层 {{ kindText(c.kind) }} <span class="mono">{{ c.name }}</span>：{{ changeText(c.change) }}
            <span v-if="c.version != null || c.current != null" class="hint">（已发布 v{{ c.version ?? '-' }} → 当前 v{{ c.current ?? '-' }}）</span>
          </div>
          <el-button v-if="session.deployEnabled && can('service.deploy') && lastSuccess" size="small" type="primary" class="section-gap"
            @click="publishLatestConfig">沿用当前镜像发布最新配置</el-button>
        </template>
      </el-alert>

      <el-card class="section-gap">
        <el-tabs v-model="tab">
          <el-tab-pane label="运行状态" name="overview">
            <ServiceRuntimeView :data="runtime" :loading="runtimeLoading" :error="runtimeError" :managed="managed" @refresh="pollRuntime" />
          </el-tab-pane>

          <el-tab-pane label="配置" name="config" lazy>
            <template v-if="managed">
              <h3 class="tab-title">生效的配置</h3>
              <EffectiveConfigTable :namespace-id="service.namespace_id" :project-ids="[service.project_id]" :service-id="service.id" />
              <h3 class="tab-title">服务层配置</h3>
              <p class="hint">只对本服务生效，覆盖平台、项目、环境层的同名键。保存后生成新版本，发布后生效。</p>
              <ConfigLayerPanel scope-type="service" :scope-id="service.id" :namespace-id="service.namespace_id" @changed="load" />
            </template>
            <template v-else>
              <el-descriptions :column="2" border size="small">
                <el-descriptions-item label="Deployment"><span class="mono">{{ imageTarget?.name }}</span></el-descriptions-item>
                <el-descriptions-item label="容器"><span class="mono">{{ imageTarget?.container }}</span></el-descriptions-item>
                <el-descriptions-item label="密钥" :span="2">
                  <span v-for="[key, version] in secretKeys" :key="key" class="mono secret">{{ key }} v{{ version }}</span>
                  <span v-if="!secretKeys.length" class="hint">无</span>
                </el-descriptions-item>
              </el-descriptions>
              <el-alert type="info" :closable="false" show-icon class="section-gap"
                title="纳管服务只更新镜像；配置、证书、Service 与 Ingress 仍在集群中维护。">
                <template #default>
                  需要由平台统一维护配置时，可以在环境的
                  <RouterLink :to="{ name: 'environment-import', params: { id: service.namespace_id } }">导入已有服务</RouterLink>
                  中把它转为托管服务。
                </template>
              </el-alert>
            </template>
          </el-tab-pane>

          <el-tab-pane v-if="managed" label="对象清单" name="manifest" lazy>
            <ManifestPanel :service="service" />
          </el-tab-pane>

          <el-tab-pane :label="`发布记录（${deploymentPage.total.value}）`" name="deployments">
            <el-table :data="deployments" empty-text="暂无发布">
              <el-table-column label="#" width="80"><template #default="{ row }"><span class="mono">#{{ row.id }}</span></template></el-table-column>
              <el-table-column label="类型" width="90"><template #default="{ row }">{{ DEPLOYMENT_KIND_TEXT[row.kind] ?? row.kind }}</template></el-table-column>
              <el-table-column label="构建 / 修订" width="130"><template #default="{ row }">{{ row.job_id ? `#${row.job_id}` : '线上镜像' }} / {{ row.revision_id }}</template></el-table-column>
              <el-table-column label="镜像" min-width="220"><template #default="{ row }"><span class="mono break-word">{{ shortImage(row.image.split('@')[0]) }}</span><div class="hint mono">{{ shortDigest(row.digest) }}</div></template></el-table-column>
              <el-table-column label="状态" min-width="170">
                <template #default="{ row }">
                  <StatusTag :status="row.status" />
                  <div v-if="row.status === 'pending_approval'" class="hint">{{ row.require_admin_approval ? '需要平台管理员确认' : '等待独立审批' }}</div>
                  <div v-else class="hint break-word">{{ row.message || row.error }}</div>
                </template>
              </el-table-column>
              <el-table-column label="提交" width="160"><template #default="{ row }">{{ fmtTime(row.created_at) }}<div class="hint">{{ row.operator }}</div></template></el-table-column>
              <el-table-column label="操作" width="170" fixed="right">
                <template #default="{ row }">
                  <el-button v-if="row.can_approve" link type="primary" :disabled="busy" @click="decide(row, true)">批准</el-button>
                  <el-button v-if="row.can_reject" link type="danger" :disabled="busy" @click="decide(row, false)">拒绝</el-button>
                  <el-button link @click="logId = row.id">详情</el-button>
                  <el-button v-if="row.status === 'success' && can('service.rollback')" link type="warning" @click="rollback(row)">回滚到此</el-button>
                </template>
              </el-table-column>
            </el-table>
            <TablePager v-model:page="deploymentPage.page.value" v-model:size="deploymentPage.pageSize.value"
              :total="deploymentPage.total.value" @change="loadDeployments" />
          </el-tab-pane>

          <el-tab-pane :label="`修订历史（${revisions.length}）`" name="revisions" lazy>
            <div class="toolbar">
              <el-select v-model="compare.from" placeholder="旧修订" style="width: 140px">
                <el-option v-for="r in revisions" :key="r.id" :label="`修订 ${r.number}`" :value="r.id" />
              </el-select>
              <span>对比</span>
              <el-select v-model="compare.to" placeholder="新修订" style="width: 140px">
                <el-option v-for="r in revisions" :key="r.id" :label="`修订 ${r.number}`" :value="r.id" />
              </el-select>
              <el-button :disabled="compare.from === null || compare.to === null" @click="runDiff">查看差异</el-button>
            </div>
            <el-table v-if="compare.result" :data="compare.result.changes" size="small" empty-text="两个修订没有差异" class="section-gap">
              <el-table-column label="字段" width="240"><template #default="{ row }"><span class="mono">{{ row.path }}</span></template></el-table-column>
              <el-table-column label="旧值"><template #default="{ row }"><span class="mono break-word">{{ JSON.stringify(row.old) }}</span></template></el-table-column>
              <el-table-column label="新值"><template #default="{ row }"><span class="mono break-word">{{ JSON.stringify(row.new) }}</span></template></el-table-column>
            </el-table>
            <el-table :data="revisionPage.rows.value" class="section-gap">
              <el-table-column label="修订" width="80"><template #default="{ row }"><span class="mono">{{ row.number }}</span></template></el-table-column>
              <el-table-column label="ID" width="80"><template #default="{ row }"><span class="mono">{{ row.id }}</span></template></el-table-column>
              <el-table-column label="状态" width="120">
                <template #default="{ row }">
                  <el-tag v-if="row.id === service.deployed_revision_id" size="small" type="success" effect="plain">已部署</el-tag>
                  <el-tag v-else-if="row.id === service.current_revision_id" size="small" type="warning" effect="plain">当前</el-tag>
                </template>
              </el-table-column>
              <el-table-column label="创建时间" width="170"><template #default="{ row }">{{ fmtTime(row.created_at) }}</template></el-table-column>
              <el-table-column label="配置">
                <template #default="{ row }">
                  <el-popover trigger="click" width="640" placement="left">
                    <template #reference><el-button link type="primary">查看 JSON</el-button></template>
                    <pre class="code-block">{{ JSON.stringify(row.spec, null, 2) }}</pre>
                  </el-popover>
                </template>
              </el-table-column>
            </el-table>
            <TablePager v-model:page="revisionPage.page.value" v-model:size="revisionPage.size.value" :total="revisionPage.total.value" />
          </el-tab-pane>
        </el-tabs>
      </el-card>
    </template>

    <el-dialog v-model="editor.visible" :title="managed ? '修改服务配置（托管）' : '修改服务配置'" :width="managed ? '960px' : '680px'"
      top="4vh" :close-on-click-modal="false">
      <div class="editor-mode"><el-switch v-model="editor.advanced" active-text="编辑 JSON" inactive-text="表单" /></div>
      <template v-if="!editor.advanced">
        <ManagedSpecForm v-if="editor.spec && service" v-model="editor.spec" :service-name="service.name"
          :namespace-name="service.namespace_name" :profile="environment?.profile ?? null" :policy="environment?.policy ?? null"
          :file-sets="fileSets" :ingress-available="!!environment?.ingress_profile_id" />
        <el-form v-else label-position="top" class="form-grid">
          <el-form-item label="Deployment 名称"><el-input v-model="editor.name" /></el-form-item>
          <el-form-item label="容器名称"><el-input v-model="editor.container" /></el-form-item>
        </el-form>
      </template>
      <el-input v-else v-model="editor.json" type="textarea" :rows="22" class="mono" spellcheck="false" />
      <p class="hint">基于当前修订 {{ service?.current_revision_id }} 创建新修订，旧修订保持不变；期间如有人修改了配置，保存时会提示冲突。发布后生效。</p>
      <template #footer>
        <el-button @click="editor.visible = false">取消</el-button>
        <el-button type="primary" :loading="busy" @click="saveRevision">保存修订</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="secret.visible" title="设置服务密钥" width="520px" :close-on-click-modal="false">
      <el-form label-position="top">
        <el-form-item label="Key"><el-input v-model="secret.key" maxlength="128" placeholder="如 DB_PASSWORD" /></el-form-item>
        <el-form-item label="Value"><el-input v-model="secret.value" type="password" show-password autocomplete="new-password" /></el-form-item>
        <p class="hint">密钥加密保存，不会回显；每次设置都会生成新的配置修订。</p>
      </el-form>
      <template #footer>
        <el-button @click="secret.visible = false">取消</el-button>
        <el-button type="primary" :loading="busy" :disabled="!secret.key || !secret.value" @click="saveSecret">保存</el-button>
      </template>
    </el-dialog>

    <DeployDialog v-model="deployOpen" :service="service" :prefer-latest="pendingRevision || !!configStatus?.pending"
      @submitted="() => { void load(); tab = 'deployments' }" />
    <DeploymentLogDialog v-model="logId" />
  </div>
</template>

<style scoped>
.meta { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-top: 6px; }
.cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); gap: 12px; }
.card { display: flex; flex-direction: column; gap: 4px; padding: 14px 16px; background: var(--hi-bg-card); border: 1px solid var(--hi-border); border-radius: 10px; min-width: 0; }
.card.wide { grid-column: span 2; }
.card .label { font-size: 12px; color: var(--hi-text-3); }
.card b { font-size: 18px; font-weight: 650; }
.card b.mono { font-size: 14px; }
.tone-success { color: var(--el-color-success); }
.tone-warning { color: var(--el-color-warning); }
.tone-danger { color: var(--el-color-danger); }
.tone-primary { color: var(--el-color-primary); }
.tab-title { margin: 6px 0 10px; font-size: 15px; }
.tab-title + .hint { margin-top: -4px; }
.secret { margin-right: 8px; }
.editor-mode { margin-bottom: 14px; }
@media (max-width: 767px) { .card.wide { grid-column: auto; } }
</style>
