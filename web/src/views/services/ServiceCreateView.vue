<script setup lang="ts">
/**
 * 新建服务向导。
 * - 托管：选择模板 → 工作负载 → 服务层配置（可见继承来的配置）→ 预览清单与服务端校验 → 创建；
 * - 纳管：选择集群中已有的 Deployment 与容器 → 创建（发布时只更新镜像）。
 */
import { Delete, Plus } from '@element-plus/icons-vue'
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { environmentApi, namespaceApi, projectApi, serviceApi } from '@/api'
import type { ServiceCreate } from '@/api'
import type {
  ConfigEntryInput, EnvironmentDetail, FileSetOption, ManagedSpec, Namespace, NamespaceResources, Project, RenderResult,
  ServiceTemplate,
} from '@/api/types'
import EffectiveConfigTable from '@/components/config/EffectiveConfigTable.vue'
import PageHeader from '@/components/PageHeader.vue'
import ManagedSpecForm from '@/components/service/ManagedSpecForm.vue'
import { notifyError, useAction } from '@/composables/useAction'
import { APPLY_ACTION_TAG, APPLY_ACTION_TEXT, CONFIG_SCOPE_TEXT, TIER_TEXT } from '@/constants'
import { usePlatform } from '@/stores/platform'
import { useSession } from '@/stores/session'

type Mode = 'managed' | 'image_only'
const route = useRoute()
const router = useRouter()
const session = useSession()
const platform = usePlatform()
const { busy, run } = useAction()
const step = ref(0)
const projects = ref<Project[]>([])
const namespaces = ref<Namespace[]>([])
const templates = ref<ServiceTemplate[]>([])
const environment = ref<EnvironmentDetail | null>(null)
const inherited = ref<FileSetOption[]>([])
const spec = ref<ManagedSpec | null>(null)
const preview = ref<RenderResult | null>(null)
const discovered = ref<NamespaceResources | null>(null)
const form = reactive({
  mode: 'managed' as Mode, name: '', template: 'web',
  projectId: (Number(route.query.project) || null) as number | null,
  appId: (Number(route.query.app) || null) as number | null,
  namespaceId: (Number(route.query.namespace) || null) as number | null,
  deployment: '', container: '',
})
const config = reactive({
  env: [] as { key: string; value: string }[],
  secret: [] as { key: string; value: string }[],
  files: [] as { key: string; value: string }[],
  bulk: '',
})

const permission = computed(() => (form.mode === 'managed' ? 'service.create' : 'service.config'))
const projectOptions = computed(() => projects.value.filter((p) => p.namespace_ids.some((id) => session.canNamespace(id, permission.value))))
const appOptions = computed(() => platform.apps.filter((a) => a.project_id === form.projectId))
const namespaceOptions = computed(() => {
  const project = projects.value.find((p) => p.id === form.projectId)
  return namespaces.value.filter((n) => project?.namespace_ids.includes(n.id) && session.canNamespace(n.id, permission.value))
})
const steps = computed(() => (form.mode === 'managed' ? ['方式与归属', '工作负载', '服务配置', '预览与创建'] : ['方式与归属', '选择工作负载']))
const containers = computed(() => discovered.value?.deployments.find((d) => d.name === form.deployment)?.containers ?? [])
const fileSets = computed<FileSetOption[]>(() => {
  const own = config.files.filter((f) => f.key.trim())
  const options = inherited.value.filter((s) => s.name !== 'properties' || !own.length)
  return own.length ? [...options, { name: 'properties', kind: 'files', keys: own.map((f) => f.key.trim()), scope: '服务' }] : options
})
const basicsReady = computed(() => !!(form.name.trim() && form.projectId && form.appId && form.namespaceId))

function entries(rows: { key: string; value: string }[], files = false): ConfigEntryInput[] {
  return rows.filter((r) => r.key.trim()).map((r): ConfigEntryInput => (files
    ? { key: r.key.trim(), value: r.value, format: r.key.endsWith('.properties') ? 'properties' : 'text' }
    : { key: r.key.trim(), value: r.value }))
}

function payload(): ServiceCreate {
  if (!form.projectId || !form.appId || !form.namespaceId) throw new Error('请选择项目、应用和环境')
  const base = { name: form.name.trim(), project_id: form.projectId, namespace_id: form.namespaceId, app_id: form.appId }
  if (form.mode === 'image_only') {
    return { ...base, spec: { mode: 'image_only', target: { kind: 'Deployment', name: form.deployment, container: form.container } } }
  }
  if (!spec.value) throw new Error('请先填写工作负载')
  return { ...base, spec: spec.value, config: { env: entries(config.env), secret_env: entries(config.secret), files: entries(config.files, true) } }
}

async function load() {
  try {
    const empty = { items: [], total: 0 }
    const [p, n, t] = await Promise.all([projectApi.list(), namespaceApi.list().catch(() => empty), serviceApi.templates(),
      platform.loadApps()])
    projects.value = p.items
    namespaces.value = n.items as Namespace[]
    templates.value = t.items
    if (!form.projectId || !projectOptions.value.some((x) => x.id === form.projectId)) form.projectId = projectOptions.value[0]?.id ?? null
  } catch (e) {
    notifyError(e)
  }
}

/** 进入工作负载步骤：读取环境档案与可挂载的配置，按环境设置模板默认值。 */
async function prepareWorkload() {
  const namespaceId = form.namespaceId
  if (!namespaceId) return
  const [env, effective] = await Promise.all([environmentApi.get(namespaceId), environmentApi.effective(namespaceId, form.projectId)])
  environment.value = env
  inherited.value = effective.files.map((f) => ({ name: f.name, kind: f.kind, keys: f.keys,
    scope: CONFIG_SCOPE_TEXT[f.origin.scope_type] }))
  if (!spec.value) {
    const template = templates.value.find((t) => t.code === form.template) ?? templates.value[0]
    const draft = JSON.parse(JSON.stringify(template.defaults)) as ManagedSpec
    const ingressPolicy = !!(env.policy.ingress as { enabled?: boolean } | undefined)?.enabled
    draft.ingress.enabled = ingressPolicy && !!env.ingress_profile_id
    draft.log.enabled = env.profile.log.enabled
    spec.value = draft
  }
}

async function discover() {
  discovered.value = null
  form.deployment = ''
  if (!form.namespaceId) return
  try {
    discovered.value = await namespaceApi.resources(form.namespaceId)
  } catch (e) {
    notifyError(e)
  }
}

async function next() {
  if (step.value === 0) {
    if (!basicsReady.value) return notifyError(new Error('请填写服务名称，并选择项目、应用和环境'))
    if (!/^[a-z0-9]([-a-z0-9]{0,61}[a-z0-9])?$/.test(form.name.trim())) return notifyError(new Error('服务名称只能包含小写字母、数字和 -'))
    const ok = await run(() => (form.mode === 'managed' ? prepareWorkload() : discover()))
    if (ok) step.value = 1
    return
  }
  if (form.mode === 'managed' && step.value === 2) {
    attachProperties()
    step.value = 3
    await renderPreview(false)
    return
  }
  step.value += 1
}

/** 填写了服务配置文件但还没有挂载时，自动挂载到 /app/config（可在上一步修改）。 */
function attachProperties() {
  const files = config.files.filter((f) => f.key.trim()).map((f) => f.key.trim())
  const current = spec.value
  if (!current || !files.length) return
  const mount = current.mounts.find((m) => m.config === 'properties')
  if (!mount) current.mounts.push({ config: 'properties', path: '/app/config', items: files, volume: '' })
}

async function renderPreview(dryRun: boolean) {
  await run(async () => {
    preview.value = await serviceApi.previewNew({ ...payload(), dry_run: dryRun })
  }, dryRun ? '服务端校验完成' : undefined)
}

async function create() {
  const result: { id?: number } = {}
  const ok = await run(async () => {
    result.id = (await serviceApi.create(payload())).id
  }, '服务已创建')
  if (ok && result.id) await router.push({ name: 'service-detail', params: { id: result.id }, query: { tab: form.mode === 'managed' ? 'manifest' : undefined } })
}

function bulkEnv() {
  for (const raw of config.bulk.split(/\r?\n/)) {
    const line = raw.trim()
    const index = line.search(/[=:]/)
    if (!line || line.startsWith('#') || index <= 0) continue
    const key = line.slice(0, index).trim()
    const value = line.slice(index + 1).trim()
    const found = config.env.find((r) => r.key === key)
    if (found) found.value = value
    else config.env.push({ key, value })
  }
  config.bulk = ''
}

watch(() => form.projectId, () => {
  if (!appOptions.value.some((a) => a.id === form.appId)) form.appId = appOptions.value[0]?.id ?? null
  if (!namespaceOptions.value.some((n) => n.id === form.namespaceId)) form.namespaceId = namespaceOptions.value[0]?.id ?? null
})
watch(() => [form.namespaceId, form.mode], () => {
  spec.value = null
  preview.value = null
  environment.value = null
})
watch(() => form.deployment, () => { form.container = containers.value[0]?.name ?? '' })
watch(() => form.name, (name) => {
  if (spec.value && !spec.value.base && name) preview.value = null
})
onMounted(load)
</script>

<template>
  <div class="page">
    <PageHeader title="新建服务" description="托管服务按模板生成全部对象并由平台维护配置；纳管服务关联集群中已有的 Deployment，只更新镜像。">
      <el-button @click="router.back()">返回</el-button>
    </PageHeader>
    <el-card>
      <el-steps :active="step" finish-status="success" simple class="steps">
        <el-step v-for="title in steps" :key="title" :title="title" />
      </el-steps>

      <section v-show="step === 0">
        <el-form label-position="top">
          <el-form-item label="方式">
            <el-radio-group v-model="form.mode" class="modes">
              <el-radio value="managed" border><b>托管（按模板新建）</b><span class="hint">生成 Deployment、Service、Ingress、HPA 与配置对象，配置按平台 → 项目 → 环境 → 服务分层维护</span></el-radio>
              <el-radio value="image_only" border><b>纳管（关联已有 Deployment）</b><span class="hint">对象仍在集群中维护，平台发布时只更新容器镜像</span></el-radio>
            </el-radio-group>
          </el-form-item>
          <div class="form-grid">
            <el-form-item label="项目" required>
              <el-select v-model="form.projectId" filterable placeholder="需要已分配环境、且你有权限的项目">
                <el-option v-for="p in projectOptions" :key="p.id" :label="p.name" :value="p.id" />
              </el-select>
            </el-form-item>
            <el-form-item label="应用（镜像仓库）" required>
              <el-select v-model="form.appId" filterable placeholder="项目内的应用">
                <el-option v-for="a in appOptions" :key="a.id" :label="`${a.name}（${a.image_repo}）`" :value="a.id" />
              </el-select>
            </el-form-item>
            <el-form-item label="环境" required>
              <el-select v-model="form.namespaceId" placeholder="项目已分配的环境">
                <el-option v-for="n in namespaceOptions" :key="n.id" :label="`${n.cluster_name} / ${n.name}（${TIER_TEXT[n.tier]}）`" :value="n.id" />
              </el-select>
            </el-form-item>
            <el-form-item label="服务名称" required>
              <el-input v-model="form.name" maxlength="63" placeholder="小写字母、数字和 -，例如 order-api" />
            </el-form-item>
          </div>
          <el-form-item v-if="form.mode === 'managed'" label="模板">
            <div class="templates">
              <div v-for="t in templates" :key="t.code" class="template" :class="{ active: form.template === t.code }" role="button"
                tabindex="0" @click="form.template = t.code" @keydown.enter="form.template = t.code">
                <b>{{ t.name }}</b> <span class="hint">v{{ t.version }}</span>
                <p class="hint">{{ t.description }}</p>
                <div class="objects"><el-tag v-for="o in t.objects" :key="o" size="small" effect="plain">{{ o }}</el-tag></div>
              </div>
            </div>
          </el-form-item>
        </el-form>
      </section>

      <section v-if="step === 1 && form.mode === 'image_only'">
        <el-alert v-for="w in discovered?.warnings ?? []" :key="w" type="warning" :title="w" :closable="false" />
        <el-form label-position="top" class="form-grid">
          <el-form-item label="Deployment">
            <el-select v-model="form.deployment" filterable placeholder="从集群读取的工作负载">
              <el-option v-for="d in discovered?.deployments ?? []" :key="d.name" :label="`${d.name}（${d.ready_replicas}/${d.replicas} 就绪）`" :value="d.name" />
            </el-select>
          </el-form-item>
          <el-form-item label="容器">
            <el-select v-model="form.container" :disabled="!form.deployment">
              <el-option v-for="c in containers" :key="c.name" :label="c.name" :value="c.name" />
            </el-select>
          </el-form-item>
        </el-form>
        <p v-if="form.container" class="hint mono break-word">当前镜像：{{ containers.find((c) => c.name === form.container)?.image }}</p>
      </section>

      <section v-if="step === 1 && form.mode === 'managed' && spec">
        <el-alert v-if="environment && !environment.profile_saved" type="info" :closable="false" show-icon class="gap-b"
          title="该环境还没有保存环境档案，对象命名、调度与资源默认值使用平台默认值。" />
        <ManagedSpecForm v-model="spec" :service-name="form.name.trim()" :namespace-name="environment?.name ?? ''"
          :profile="environment?.profile ?? null" :policy="environment?.policy ?? null" :file-sets="fileSets"
          :ingress-available="!!environment?.ingress_profile_id" />
      </section>

      <section v-if="step === 2 && form.mode === 'managed' && form.namespaceId">
        <h3 class="title">继承的配置</h3>
        <p class="hint">平台、项目、环境层已有的配置，服务会自动获得；需要覆盖时在下面填写同名的键。</p>
        <EffectiveConfigTable :namespace-id="form.namespaceId" :project-ids="form.projectId ? [form.projectId] : []" />
        <h3 class="title">服务自己的环境变量</h3>
        <el-table :data="config.env" size="small" empty-text="没有">
          <el-table-column label="键" min-width="200"><template #default="{ row }"><el-input v-model="row.key" placeholder="KEY" /></template></el-table-column>
          <el-table-column label="值" min-width="300"><template #default="{ row }"><el-input v-model="row.value" /></template></el-table-column>
          <el-table-column width="56"><template #default="{ $index }"><el-button link type="danger" :icon="Delete" aria-label="删除" @click="config.env.splice($index, 1)" /></template></el-table-column>
        </el-table>
        <div class="toolbar section-gap">
          <el-button size="small" :icon="Plus" @click="config.env.push({ key: '', value: '' })">添加</el-button>
          <el-input v-model="config.bulk" type="textarea" :rows="2" placeholder="批量粘贴：每行 KEY=VALUE" class="bulk" />
          <el-button size="small" :disabled="!config.bulk.trim()" @click="bulkEnv">导入</el-button>
        </div>
        <h3 class="title">服务自己的敏感变量</h3>
        <el-table :data="config.secret" size="small" empty-text="没有">
          <el-table-column label="键" min-width="200"><template #default="{ row }"><el-input v-model="row.key" placeholder="DB_PASSWORD" /></template></el-table-column>
          <el-table-column label="值" min-width="300"><template #default="{ row }"><el-input v-model="row.value" type="password" show-password autocomplete="new-password" /></template></el-table-column>
          <el-table-column width="56"><template #default="{ $index }"><el-button link type="danger" :icon="Delete" aria-label="删除" @click="config.secret.splice($index, 1)" /></template></el-table-column>
        </el-table>
        <el-button size="small" :icon="Plus" class="section-gap" @click="config.secret.push({ key: '', value: '' })">添加</el-button>
        <h3 class="title">服务配置文件 <span class="hint">渲染为 &lt;服务&gt;-properties，未挂载时自动挂载到 /app/config</span></h3>
        <div v-for="(file, index) in config.files" :key="index" class="file-row">
          <el-input v-model="file.key" placeholder="service.properties" />
          <el-input v-model="file.value" type="textarea" :rows="6" class="mono" placeholder="文件内容" />
          <el-button link type="danger" :icon="Delete" aria-label="删除文件" @click="config.files.splice(index, 1)" />
        </div>
        <el-button size="small" :icon="Plus" @click="config.files.push({ key: '', value: '' })">添加文件</el-button>
      </section>

      <section v-if="step === 3 && form.mode === 'managed'" v-loading="busy && !preview">
        <div class="toolbar gap-b">
          <el-button :loading="busy" @click="renderPreview(false)">重新预览</el-button>
          <el-button type="primary" plain :loading="busy" @click="renderPreview(true)">服务端校验（dry-run）</el-button>
          <span class="hint">镜像在发布时选择；预览中敏感值已隐藏。</span>
        </div>
        <template v-if="preview">
          <el-alert v-for="e in preview.errors" :key="e" type="error" :title="e" :closable="false" show-icon class="gap-b" />
          <el-alert v-for="w in preview.warnings" :key="w" type="warning" :title="w" :closable="false" show-icon class="gap-b" />
          <el-table v-if="preview.dry_run" :data="preview.dry_run" size="small" class="gap-b">
            <el-table-column label="对象" min-width="240"><template #default="{ row }"><span class="hint">{{ row.kind }}</span> <span class="mono">{{ row.name }}</span></template></el-table-column>
            <el-table-column label="结果" width="110"><template #default="{ row }"><el-tag :type="APPLY_ACTION_TAG[row.action]" size="small">{{ APPLY_ACTION_TEXT[row.action] }}</el-tag></template></el-table-column>
            <el-table-column prop="message" label="说明" min-width="260" show-overflow-tooltip />
          </el-table>
          <el-collapse>
            <el-collapse-item v-for="obj in preview.objects" :key="obj.key" :name="obj.key">
              <template #title><span class="hint kind">{{ obj.kind }}</span><span class="mono">{{ obj.name }}</span></template>
              <pre class="code-block">{{ obj.yaml }}</pre>
            </el-collapse-item>
          </el-collapse>
        </template>
      </section>

      <div class="actions">
        <el-button v-if="step > 0" @click="step -= 1">上一步</el-button>
        <el-button v-if="step < steps.length - 1" type="primary" :loading="busy" @click="next">下一步</el-button>
        <el-button v-else type="primary" :loading="busy"
          :disabled="form.mode === 'image_only' ? !form.container : !!preview?.errors.length" @click="create">创建服务</el-button>
      </div>
    </el-card>
  </div>
</template>

<style scoped>
.steps { margin-bottom: 22px; }
.modes { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 12px; width: 100%; }
.modes :deep(.el-radio) { height: auto; padding: 12px 14px; margin-right: 0; align-items: flex-start; white-space: normal; }
.modes :deep(.el-radio__label) { display: flex; flex-direction: column; gap: 4px; }
.templates { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 12px; width: 100%; }
.template { border: 1px solid var(--hi-border); border-radius: 10px; padding: 14px 16px; cursor: pointer; }
.template.active { border-color: var(--el-color-primary); box-shadow: 0 0 0 1px var(--el-color-primary); }
.template p { margin: 6px 0 8px; }
.objects { display: flex; flex-wrap: wrap; gap: 4px; }
.title { margin: 18px 0 8px; font-size: 15px; }
.bulk { flex: 1; min-width: 240px; }
.file-row { display: grid; grid-template-columns: 260px minmax(0, 1fr) 32px; gap: 8px; margin-bottom: 10px; align-items: start; }
.actions { display: flex; justify-content: flex-end; gap: 10px; margin-top: 22px; }
.gap-b { margin-bottom: 10px; }
.kind { display: inline-block; min-width: 170px; }
@media (max-width: 767px) { .file-row { grid-template-columns: minmax(0, 1fr); } }
</style>
