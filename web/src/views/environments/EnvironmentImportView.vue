<script setup lang="ts">
/**
 * 导入已有服务：上传 kubectl 导出（或直接读取集群）→ 平台给出托管方案（环境档案、负载均衡入口、环境层共有配置、
 * 公共配置文件、每个服务的规格与服务层配置、不一致与接管后的差异）→ 确认写入平台 → 逐个“接管发布”。
 * 导入只写平台数据，不修改集群；接管发布沿用线上镜像，下发前做服务端校验。
 */
import { Upload } from '@element-plus/icons-vue'
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { environmentApi, projectApi, serviceApi } from '@/api'
import { idempotencyKey } from '@/api/http'
import type { EnvironmentDetail, ImportReport, ImportResult, ImportServiceRow, Project } from '@/api/types'
import PageHeader from '@/components/PageHeader.vue'
import TablePager from '@/components/TablePager.vue'
import { notifyError, notifySuccess, useAction } from '@/composables/useAction'
import { usePagination } from '@/composables/usePagination'
import { CONFIG_KIND_TEXT, CONFIG_SCOPE_TEXT, IMPORT_ACTION_TAG, IMPORT_ACTION_TEXT, TIER_TEXT } from '@/constants'
import { usePlatform } from '@/stores/platform'
import { useSession } from '@/stores/session'
import { copyText } from '@/utils/format'

interface Choice {
  selected: boolean
  name: string
  app: number | 'create' | null
}

const props = defineProps<{ id: number }>()
const route = useRoute()
const router = useRouter()
const platform = usePlatform()
const session = useSession()
const { busy, run } = useAction()
const env = ref<EnvironmentDetail | null>(null)
/** 接管发布需要部署中心开启，并且有该环境的服务发布权限。 */
const canAdopt = computed(() => session.deployEnabled && session.canNamespace(props.id, 'service.deploy'))
const projects = ref<Project[]>([])
const step = ref(0)
const report = ref<ImportReport | null>(null)
const result = ref<ImportResult | null>(null)
const choices = reactive<Record<string, Choice>>({})
const adopted = reactive<Record<number, string>>({})
const source = reactive({ kind: 'yaml' as 'yaml' | 'cluster', yaml: '', projectId: (Number(route.query.project) || null) as number | null,
  fileName: '' })
const options = reactive({ saveProfile: true, ingress: 'create' as 'create' | 'reuse' | 'none', saveConfig: true,
  overwriteFiles: false, useCertificates: true })
const fileInput = ref<HTMLInputElement | null>(null)

const projectOptions = computed(() => projects.value.filter((p) => p.namespace_ids.includes(props.id)))
const apps = computed(() => platform.apps.filter((a) => a.project_id === source.projectId))
const services = computed(() => report.value?.services ?? [])
const servicePage = usePagination(() => services.value, { size: 10 })
const selected = computed(() => services.value.filter((s) => choices[s.deployment]?.selected))
const envRows = computed(() => Object.entries(report.value?.env.keys ?? {}).map(([key, value]) => ({ key, value })))
const envPage = usePagination(() => envRows.value, { size: 10 })
const command = computed(() => `kubectl get deploy,svc,ing,cm,secret,hpa,pod -n ${env.value?.name ?? '<命名空间>'} -o yaml > export.yaml`)
const created = computed(() => (result.value?.services ?? []).filter((s) => s.service_id && (s.status === 'created' || s.status === 'converted')))
const selectable = (row: ImportServiceRow) => row.action === 'create' || row.action === 'convert'
const kindText = (kind: string) => CONFIG_KIND_TEXT[kind as keyof typeof CONFIG_KIND_TEXT] ?? kind
const RESULT_TEXT: Record<string, string> = { created: '已新建', converted: '已转为托管', skipped: '跳过', error: '失败' }
const resultText = (status: string) => RESULT_TEXT[status] ?? status
const EFFECTIVE_KINDS = ['env', 'secret_env'] as const
function hasDiff(row: ImportServiceRow): boolean {
  return EFFECTIVE_KINDS.some((kind) => {
    const diff = row.effective?.[kind]
    return !!diff && (diff.added.length > 0 || diff.changed.length > 0 || diff.locked.length > 0)
  })
}
const scopeText = (scope: string) => CONFIG_SCOPE_TEXT[scope as keyof typeof CONFIG_SCOPE_TEXT] ?? scope

async function load() {
  try {
    const [detail, list] = await Promise.all([environmentApi.get(props.id),
      projectApi.list().catch(() => ({ items: [] as Project[], total: 0 })), platform.loadApps().catch(() => [])])
    env.value = detail
    projects.value = list.items
    if (!source.projectId || !projectOptions.value.some((p) => p.id === source.projectId)) source.projectId = projectOptions.value[0]?.id ?? null
  } catch (e) {
    notifyError(e)
  }
}

function pickFile(event: Event) {
  const file = (event.target as HTMLInputElement).files?.[0]
  if (!file) return
  if (file.size > 20 * 1024 * 1024) return notifyError(new Error('文件超过 20 MiB'))
  const reader = new FileReader()
  reader.onload = () => {
    source.yaml = String(reader.result ?? '')
    source.fileName = file.name
  }
  reader.onerror = () => notifyError(new Error(`读取 ${file.name} 失败`))
  reader.readAsText(file)
  if (fileInput.value) fileInput.value.value = ''
}

function request() {
  return { source: source.kind, yaml: source.kind === 'yaml' ? source.yaml : undefined, project_id: source.projectId }
}

async function analyze() {
  if (!source.projectId) return notifyError(new Error('请选择服务所属的项目（环境需要已分配给该项目）'))
  if (source.kind === 'yaml' && !source.yaml.trim()) return notifyError(new Error('请上传或粘贴 kubectl 导出的 YAML'))
  const ok = await run(async () => {
    const data = await environmentApi.analyzeImport(props.id, request())
    report.value = data
    for (const key of Object.keys(choices)) delete choices[key]
    for (const row of data.services) {
      choices[row.deployment] = { selected: selectable(row), name: row.name,
        app: row.app?.id ?? (row.app?.create ? (data.permissions.app_manage ? 'create' : null) : null) }
    }
    options.saveProfile = data.permissions.environment_config && !data.profile.same
    options.saveConfig = data.permissions.environment_config
    options.ingress = data.ingress.reuse ? 'reuse' : data.ingress.proposed && data.permissions.certificate_manage ? 'create' : 'none'
    options.useCertificates = data.permissions.certificate_manage
  })
  if (ok) step.value = 1
}

async function apply() {
  const rows = selected.value
  if (!rows.length) return notifyError(new Error('请至少选择一个服务'))
  const missing = rows.filter((r) => r.action === 'create' && !choices[r.deployment].app)
  if (missing.length) return notifyError(new Error(`请为 ${missing.map((r) => r.deployment).join('、')} 选择应用`))
  const ok = await run(async () => {
    result.value = await environmentApi.applyImport(props.id, {
      ...request(), save_profile: options.saveProfile, ingress: options.ingress, save_config: options.saveConfig,
      overwrite_files: options.overwriteFiles, use_certificates: options.useCertificates,
      services: rows.map((r) => {
        const choice = choices[r.deployment]
        return { deployment: r.deployment, name: choice.name.trim() || r.name,
          app_id: typeof choice.app === 'number' ? choice.app : null, create_app: choice.app === 'create' }
      }),
    })
  }, '导入完成')
  if (ok) step.value = 2
}

async function adopt(serviceId: number, name: string) {
  await run(async () => {
    const ticket = await serviceApi.adopt(serviceId, idempotencyKey(), '导入后接管')
    adopted[serviceId] = ticket.status === 'pending_approval' ? '等待审批' : '已排队'
  }, `${name} 的接管发布已提交`)
}

async function copyCommand() {
  try {
    await copyText(command.value)
    notifySuccess('已复制')
  } catch (e) {
    notifyError(e)
  }
}

watch(() => source.projectId, () => {
  report.value = null
  if (step.value > 0) step.value = 0
})
onMounted(load)
</script>

<template>
  <div class="page">
    <PageHeader :title="`导入已有服务${env ? ' · ' + env.name : ''}`"
      description="分析命名空间中现有的工作负载与配置，转为平台托管：环境共有的参数上提到环境层，服务只保留自己的差异。">
      <template v-if="env" #meta><p class="hint">{{ env.cluster_name }} / {{ env.name }} · {{ TIER_TEXT[env.tier] }}</p></template>
      <el-button @click="router.push({ name: 'environment-detail', params: { id } })">返回环境</el-button>
    </PageHeader>

    <el-card>
      <el-steps :active="step" finish-status="success" simple class="steps">
        <el-step title="选择来源" /><el-step title="确认方案" /><el-step title="接管发布" />
      </el-steps>

      <section v-if="step === 0">
        <el-form label-position="top">
          <div class="form-grid">
            <el-form-item label="服务所属项目" required>
              <el-select v-model="source.projectId" placeholder="已分配该环境的项目">
                <el-option v-for="p in projectOptions" :key="p.id" :label="p.name" :value="p.id" />
              </el-select>
              <div v-if="!projectOptions.length" class="hint">
                {{ env?.project_ids.length ? '你没有该环境所属项目的查看权限；导入需要选择服务所属的项目（以及项目的应用权限）。'
                  : '该环境还没有分配给任何项目，请先在项目中分配环境。' }}</div>
            </el-form-item>
            <el-form-item label="来源">
              <el-radio-group v-model="source.kind">
                <el-radio-button value="yaml">上传或粘贴导出文件</el-radio-button>
                <el-radio-button value="cluster">直接读取集群</el-radio-button>
              </el-radio-group>
            </el-form-item>
          </div>
          <template v-if="source.kind === 'yaml'">
            <el-form-item label="kubectl 导出">
              <div class="upload-row">
                <el-button :icon="Upload" @click="fileInput?.click()">选择文件</el-button>
                <input ref="fileInput" type="file" accept=".yaml,.yml,.json,.txt" hidden @change="pickFile" />
                <span class="hint">{{ source.fileName || '支持多文档 YAML、kind: List 与 JSON，最大 20 MiB' }}</span>
              </div>
              <el-input v-model="source.yaml" type="textarea" :rows="12" class="mono section-gap" spellcheck="false"
                placeholder="也可以直接粘贴 YAML" />
            </el-form-item>
            <p class="hint">导出命令（包含 Secret 时需要相应的读取权限）：<span class="mono">{{ command }}</span>
              <el-button link size="small" @click="copyCommand">复制</el-button></p>
          </template>
          <el-alert v-else type="info" :closable="false" show-icon
            title="平台用集群凭据读取 Deployment、Service、Ingress、ConfigMap、Secret、HPA 与 Pod；凭据需要这些资源的 list 权限。" />
        </el-form>
        <div class="actions"><el-button type="primary" :loading="busy" @click="analyze">分析</el-button></div>
      </section>

      <section v-if="step === 1 && report">
        <el-alert v-for="b in report.blockers" :key="b" type="error" :title="b" :closable="false" show-icon class="gap-b" />
        <el-descriptions :column="4" border size="small" class="gap-b">
          <el-descriptions-item label="来源命名空间">{{ report.source_namespace }}</el-descriptions-item>
          <el-descriptions-item label="工作负载">{{ report.counts.Deployment ?? 0 }} 个 Deployment</el-descriptions-item>
          <el-descriptions-item label="配置对象">{{ report.counts.ConfigMap ?? 0 }} 个 ConfigMap · {{ report.counts.Secret ?? 0 }} 个 Secret</el-descriptions-item>
          <el-descriptions-item label="未处理的类型">{{ Object.entries(report.ignored).map(([k, v]) => `${k} ${v}`).join('、') || '无' }}</el-descriptions-item>
        </el-descriptions>
        <el-collapse v-if="report.warnings.length" class="gap-b">
          <el-collapse-item :title="`发现的问题与不一致（${report.warnings.length}）`" name="warnings">
            <ul class="list"><li v-for="w in report.warnings" :key="w">{{ w }}</li></ul>
          </el-collapse-item>
        </el-collapse>

        <h3 class="title">服务 <span class="hint">已选择 {{ selected.length }} 个</span></h3>
        <el-table :data="servicePage.rows.value" size="small" row-key="deployment">
          <el-table-column type="expand">
            <template #default="{ row }">
              <div class="expand">
                <div v-if="row.reason" class="hint">{{ row.reason }}</div>
                <div><span class="hint">接管的对象：</span><el-tag v-for="o in row.objects" :key="`${o.kind}/${o.name}`" size="small" effect="plain" class="tag">{{ o.kind }} {{ o.name }}</el-tag></div>
                <div v-if="row.config"><span class="hint">服务层配置：</span>环境变量 {{ row.config.env.length }} 个、敏感变量 {{ row.config.secret_env.length }} 个、配置文件 {{ row.config.files.join('、') || '无' }}</div>
                <div v-if="row.image"><span class="hint">线上镜像：</span><span class="mono">{{ row.image }}</span><span v-if="row.digest" class="hint mono"> · {{ row.digest.slice(0, 19) }}…</span></div>
                <ul v-if="row.warnings.length" class="list warn"><li v-for="w in row.warnings" :key="w">{{ w }}</li></ul>
              </div>
            </template>
          </el-table-column>
          <el-table-column width="50">
            <template #default="{ row }"><el-checkbox v-if="choices[row.deployment]" v-model="choices[row.deployment].selected" :disabled="!selectable(row)" :aria-label="`选择 ${row.deployment}`" /></template>
          </el-table-column>
          <el-table-column label="工作负载" min-width="180"><template #default="{ row }"><span class="mono">{{ row.deployment }}</span></template></el-table-column>
          <el-table-column label="处理" width="150"><template #default="{ row }"><el-tag size="small" :type="IMPORT_ACTION_TAG[row.action]">{{ IMPORT_ACTION_TEXT[row.action] }}</el-tag></template></el-table-column>
          <el-table-column label="服务名称" min-width="170">
            <template #default="{ row }">
              <el-input v-if="row.action === 'create' && choices[row.deployment]" v-model="choices[row.deployment].name" size="small" />
              <span v-else>{{ row.existing_service?.name ?? row.name }}</span>
              <div v-if="row.name_conflict" class="hint warn-text">已有同名服务，请改名</div>
            </template>
          </el-table-column>
          <el-table-column label="应用" min-width="200">
            <template #default="{ row }">
              <el-select v-if="row.action === 'create' && choices[row.deployment]" v-model="choices[row.deployment].app" size="small" placeholder="选择应用">
                <el-option v-if="row.app?.create && report.permissions.app_manage" :label="`按镜像新建 ${row.app.create.organization}/${row.app.create.repository}`" value="create" />
                <el-option v-for="a in apps" :key="a.id" :label="a.name" :value="a.id" />
              </el-select>
              <span v-else>{{ row.app?.name ?? '—' }}</span>
            </template>
          </el-table-column>
          <el-table-column label="接管后与线上的差异" min-width="220">
            <template #default="{ row }">
              <template v-if="row.effective">
                <div v-for="kind in EFFECTIVE_KINDS" :key="kind">
                  <span v-if="row.effective[kind].added.length || row.effective[kind].changed.length || row.effective[kind].locked.length" class="warn-text">
                    {{ kind === 'env' ? '环境变量' : '敏感变量' }}：
                    <template v-if="row.effective[kind].added.length">新增 {{ row.effective[kind].added.join('、') }} </template>
                    <template v-if="row.effective[kind].changed.length">变化 {{ row.effective[kind].changed.join('、') }} </template>
                    <template v-if="row.effective[kind].locked.length">被上层锁定 {{ row.effective[kind].locked.join('、') }}</template>
                  </span>
                </div>
                <span v-if="!hasDiff(row)" class="hint">生效配置与线上一致</span>
              </template>
              <span v-else class="hint">—</span>
            </template>
          </el-table-column>
          <el-table-column label="提示" width="80"><template #default="{ row }"><span :class="{ 'warn-text': row.warnings.length }">{{ row.warnings.length }}</span></template></el-table-column>
        </el-table>
        <TablePager v-model:page="servicePage.page.value" v-model:size="servicePage.size.value" :total="servicePage.total.value" />

        <div class="grid-2 section-gap">
          <el-card shadow="never">
            <template #header>
              <div class="card-title"><span>环境档案</span>
                <el-checkbox v-model="options.saveProfile" :disabled="!report.permissions.environment_config">保存为环境档案</el-checkbox></div>
            </template>
            <div v-if="report.profile.same || report.profile.saved" class="gap-b">
              <el-tag v-if="report.profile.same" size="small" type="success" effect="plain">与当前环境档案一致</el-tag>
              <el-tag v-else size="small" type="warning" effect="plain">与当前环境档案（v{{ report.profile.version }}）不同，保存会生成新版本</el-tag>
            </div>
            <el-descriptions :column="1" size="small">
              <el-descriptions-item label="对象命名"><span class="mono">{{ Object.values(report.profile.proposed.naming).join('、') }}</span></el-descriptions-item>
              <el-descriptions-item label="主机名规则"><span class="mono">{{ report.profile.proposed.host || '—' }}</span></el-descriptions-item>
              <el-descriptions-item label="调度">节点池 {{ report.profile.proposed.scheduling.node_pool || '不限' }}
                · {{ report.profile.proposed.scheduling.anti_affinity ? '同服务反亲和' : '不反亲和' }}
                · {{ report.profile.proposed.scheduling.zone_spread ? '跨可用区打散' : '不打散' }}</el-descriptions-item>
              <el-descriptions-item label="镜像拉取密钥">{{ report.profile.proposed.image_pull_secret || '—' }}</el-descriptions-item>
              <el-descriptions-item label="日志目录">{{ report.profile.proposed.log.enabled ? `${report.profile.proposed.log.host_path} → ${report.profile.proposed.log.mount_path}` : '不挂载' }}</el-descriptions-item>
              <el-descriptions-item label="默认资源">请求 {{ report.profile.proposed.defaults.cpu_request }} / {{ report.profile.proposed.defaults.memory_request }}
                · 上限 {{ report.profile.proposed.defaults.cpu_limit }} / {{ report.profile.proposed.defaults.memory_limit }}
                · 副本 {{ report.profile.proposed.defaults.replicas }}</el-descriptions-item>
            </el-descriptions>
          </el-card>
          <el-card shadow="never">
            <template #header><div class="card-title"><span>负载均衡入口</span></div></template>
            <template v-if="report.ingress.proposed">
              <el-descriptions :column="1" size="small">
                <el-descriptions-item label="IngressClass / ELB 类型">{{ report.ingress.proposed.ingress_class }} / {{ report.ingress.proposed.elb_class }}</el-descriptions-item>
                <el-descriptions-item label="ELB 实例"><span class="mono">{{ report.ingress.proposed.elb_id }}</span> · 端口 {{ report.ingress.proposed.port }}</el-descriptions-item>
                <el-descriptions-item label="证书 ID"><span class="mono break-word">{{ report.ingress.proposed.certificate_ids.join('、') || '无' }}</span></el-descriptions-item>
                <el-descriptions-item label="路径匹配">{{ report.ingress.proposed.url_match_mode || '默认' }}</el-descriptions-item>
              </el-descriptions>
              <el-radio-group v-model="options.ingress" class="section-gap">
                <el-radio v-if="report.ingress.reuse" value="reuse">使用已有的“{{ report.ingress.reuse.name }}”</el-radio>
                <el-radio value="create" :disabled="!report.permissions.certificate_manage">新建负载均衡入口</el-radio>
                <el-radio value="none">不设置</el-radio>
              </el-radio-group>
              <el-alert v-if="!report.permissions.certificate_manage && !report.ingress.reuse && !report.ingress.current_id"
                type="warning" :closable="false" show-icon class="section-gap"
                title="新建负载均衡入口需要证书管理权限。不设置入口时，导入的服务暂不托管 Ingress，集群中的 Ingress 保持不变。" />
            </template>
            <p v-else class="hint">没有 Ingress。</p>
          </el-card>
        </div>

        <el-card shadow="never" class="section-gap">
          <template #header>
            <div class="card-title"><span>环境层配置 <span class="hint">所有服务取值都相同的参数</span></span>
              <div class="toolbar">
                <el-checkbox v-model="options.saveConfig" :disabled="!report.permissions.environment_config">导入环境层配置</el-checkbox>
                <el-checkbox v-model="options.overwriteFiles" :disabled="!options.saveConfig">内容不同时用线上内容更新</el-checkbox>
                <el-checkbox v-model="options.useCertificates" :disabled="!options.saveConfig || !report.permissions.certificate_manage">证书文件存入证书库</el-checkbox>
              </div>
            </div>
          </template>
          <p class="hint">环境变量 {{ envRows.length }} 个<template v-if="report.env.existing">（环境层已有配置：追加 {{ report.env.additions.length }} 个<template v-if="report.env.conflicts.length">，{{ report.env.conflicts.join('、') }} 与已有值不同，保留已有值，服务层覆盖</template>）</template>；
            敏感变量 {{ report.secret_env.keys.length }} 个：<span class="mono">{{ report.secret_env.keys.join('、') || '无' }}</span>（值不显示）</p>
          <el-table :data="envPage.rows.value" size="small" empty-text="没有所有服务都相同的环境变量">
            <el-table-column label="键" min-width="220"><template #default="{ row }"><span class="mono">{{ row.key }}</span></template></el-table-column>
            <el-table-column label="值" min-width="320"><template #default="{ row }"><span class="mono break-word">{{ row.value }}</span></template></el-table-column>
          </el-table>
          <TablePager v-model:page="envPage.page.value" v-model:size="envPage.size.value" :total="envPage.total.value" compact />
          <h4>公共配置文件与证书目录</h4>
          <el-table :data="report.files" size="small" empty-text="没有被挂载的公共配置">
            <el-table-column label="名称" min-width="180"><template #default="{ row }"><span class="mono">{{ row.name }}</span></template></el-table-column>
            <el-table-column label="类型" width="100"><template #default="{ row }">{{ kindText(row.kind) }}</template></el-table-column>
            <el-table-column label="文件" min-width="240"><template #default="{ row }"><span class="mono break-word">{{ row.keys.join('、') }}</span></template></el-table-column>
            <el-table-column label="使用的服务" width="100"><template #default="{ row }">{{ row.used_by.length }}</template></el-table-column>
            <el-table-column label="平台中" min-width="170">
              <template #default="{ row }">
                <span v-if="!row.existing" class="hint">新建</span>
                <el-tag v-else-if="row.existing.same" size="small" type="success" effect="plain">{{ scopeText(row.existing.scope_type) }}层已有，内容相同</el-tag>
                <el-tag v-else size="small" type="warning" effect="plain">{{ scopeText(row.existing.scope_type) }}层已有 v{{ row.existing.version }}，内容不同</el-tag>
              </template>
            </el-table-column>
          </el-table>
        </el-card>

        <div class="actions">
          <el-button @click="step = 0">上一步</el-button>
          <el-button type="primary" :loading="busy" :disabled="!selected.length" @click="apply">导入 {{ selected.length }} 个服务</el-button>
        </div>
      </section>

      <section v-if="step === 2 && result">
        <el-alert v-for="e in result.errors" :key="e" type="error" :title="e" :closable="false" show-icon class="gap-b" />
        <el-descriptions :column="2" border size="small" class="gap-b">
          <el-descriptions-item label="环境档案">{{ result.profile ? `已保存 v${result.profile.version}` : '未修改' }}</el-descriptions-item>
          <el-descriptions-item label="负载均衡入口">{{ result.ingress_profile ? `已新建“${result.ingress_profile.name}”` : '未新建' }}</el-descriptions-item>
          <el-descriptions-item label="配置">{{ result.config_sets.map((c) => `${c.name} v${c.version}（${c.action === 'create' ? '新建' : c.action === 'update' ? '更新' : '不变'}）`).join('、') || '无' }}</el-descriptions-item>
          <el-descriptions-item label="新建的应用">{{ result.apps.map((a) => a.name).join('、') || '无' }}</el-descriptions-item>
        </el-descriptions>
        <ul v-if="result.notes.length" class="list hint"><li v-for="n in result.notes" :key="n">{{ n }}</li></ul>
        <el-table :data="result.services" size="small">
          <el-table-column label="工作负载" min-width="180"><template #default="{ row }"><span class="mono">{{ row.deployment }}</span></template></el-table-column>
          <el-table-column label="结果" min-width="220">
            <template #default="{ row }">
              <el-tag size="small" :type="row.status === 'error' ? 'danger' : row.status === 'skipped' ? 'info' : 'success'">
                {{ resultText(row.status) }}</el-tag>
              <span class="hint gap-l">{{ row.message }}</span>
            </template>
          </el-table-column>
          <el-table-column label="服务" min-width="150">
            <template #default="{ row }">
              <RouterLink v-if="row.service_id" :to="{ name: 'service-detail', params: { id: row.service_id }, query: { tab: 'manifest' } }">{{ row.name }}</RouterLink>
            </template>
          </el-table-column>
          <el-table-column label="接管发布" width="200">
            <template #default="{ row }">
              <template v-if="row.service_id && (row.status === 'created' || row.status === 'converted')">
                <span v-if="adopted[row.service_id]" class="hint">{{ adopted[row.service_id] }}</span>
                <el-button v-else-if="canAdopt" link type="primary" :disabled="busy" @click="adopt(row.service_id, row.name)">接管发布</el-button>
                <span v-else class="hint">{{ session.deployEnabled ? '没有发布权限' : '部署中心已关闭' }}</span>
              </template>
            </template>
          </el-table-column>
        </el-table>
        <el-alert v-if="created.length && !session.deployEnabled" type="warning" :closable="false" show-icon class="section-gap"
          title="部署中心已关闭，暂时不能接管发布；开启后在服务详情中点“接管发布”。在此之前平台不会修改集群中的这些对象。" />
        <el-alert v-else-if="created.length" type="info" :closable="false" show-icon class="section-gap"
          title="建议先在服务的“对象清单”中做服务端校验，确认与线上的差异后再接管发布；接管发布按环境的审批策略执行。" />
        <div class="actions">
          <el-button @click="step = 0">继续导入</el-button>
          <el-button type="primary" @click="router.push({ name: 'services', query: { namespace: String(id) } })">查看服务</el-button>
        </div>
      </section>
    </el-card>
  </div>
</template>

<style scoped>
.steps { margin-bottom: 22px; }
.upload-row { display: flex; align-items: center; gap: 12px; width: 100%; }
.actions { display: flex; justify-content: flex-end; gap: 10px; margin-top: 22px; }
.gap-b { margin-bottom: 10px; }
.gap-l { margin-left: 6px; }
.title { margin: 18px 0 8px; font-size: 15px; }
h4 { margin: 18px 0 8px; }
.list { margin: 4px 0; padding-left: 18px; line-height: 1.8; }
.list.warn, .warn-text { color: var(--el-color-warning-dark-2, #b88230); }
.expand { padding: 4px 16px 4px 48px; display: grid; gap: 6px; }
.tag { margin: 2px 4px 2px 0; }
/* 并排的卡片标题等高（一个带复选框、一个不带） */
.card-title { min-height: 32px; }
</style>
