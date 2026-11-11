<script setup lang="ts">
/**
 * 托管服务规格表单：工作负载、健康检查、资源、Service、Ingress、HPA、配置挂载、日志目录、环境变量与注解。
 * 对象名、主机名、资源与副本的默认值来自环境档案，在输入框的占位中显示。
 */
import { Delete, Plus } from '@element-plus/icons-vue'
import { computed } from 'vue'
import type { EnvironmentProfile, FileSetOption, ManagedSpec, NamespacePolicy, NamingKey, ResourceKey } from '@/api/types'
import { PROBE_TYPES, PULL_POLICIES, SERVICE_TYPES } from '@/constants'

const props = defineProps<{
  serviceName: string
  namespaceName: string
  profile: EnvironmentProfile | null
  policy: NamespacePolicy | null
  fileSets: FileSetOption[]
  ingressAvailable: boolean
  readonly?: boolean
}>()
const spec = defineModel<ManagedSpec>({ required: true })

const NAME_ROLES: [NamingKey | 'hpa', string][] = [
  ['deployment', 'Deployment'], ['service', 'Service'], ['ingress', 'Ingress'], ['configmap', '环境变量 ConfigMap'],
  ['secret', '敏感变量 Secret'], ['properties', '服务配置文件 ConfigMap'], ['hpa', 'HPA'],
]
const RESOURCES: [ResourceKey, string][] = [
  ['cpu_request', 'CPU 请求'], ['cpu_limit', 'CPU 上限'], ['memory_request', '内存请求'], ['memory_limit', '内存上限'],
]
const URL_MODES: [string, string][] = [['', '使用入口默认值'], ['STARTS_WITH', '前缀匹配'], ['EQUAL_TO', '精确匹配'], ['REGEX', '正则匹配']]

const policyTypes = computed(() => {
  const allowed = (props.policy?.service_types as string[] | undefined) ?? ['ClusterIP']
  return SERVICE_TYPES.filter((t) => allowed.includes(t))
})
const ingressPolicy = computed(() => !!(props.policy?.ingress as { enabled?: boolean } | undefined)?.enabled)
const hpaPolicy = computed(() => !!props.policy?.hpa_enabled)
const base = computed(() => spec.value.base || props.serviceName || '<服务名>')

function fill(rule: string): string {
  return rule.split('{base}').join(base.value).split('{service}').join(props.serviceName || '<服务名>')
    .split('{namespace}').join(props.namespaceName)
}

function defaultName(role: NamingKey | 'hpa'): string {
  const naming = props.profile?.naming
  if (!naming) return ''
  return role === 'hpa' ? (spec.value.names.deployment || fill(naming.deployment)) : fill(naming[role])
}

const objectName = (role: NamingKey | 'hpa') => spec.value.names[role] || defaultName(role)
const hostDefault = computed(() => (props.profile?.host ? fill(props.profile.host) : ''))
const resourceDefault = (key: ResourceKey) => props.profile?.defaults[key] ?? ''
const setKeys = (config: string) => props.fileSets.find((s) => s.name === config)?.keys ?? []
const annotations = computed(() => Object.entries(spec.value.pod_annotations).map(([key, value]) => ({ key, value })))

function setName(role: NamingKey | 'hpa', value: string) {
  const names = { ...spec.value.names }
  if (value.trim()) names[role] = value.trim()
  else delete names[role]
  spec.value.names = names
}

function setResource(key: ResourceKey, value: string) {
  const resources = { ...spec.value.resources }
  if (value.trim()) resources[key] = value.trim()
  else delete resources[key]
  spec.value.resources = resources
}

function addPort() {
  const used = spec.value.service.ports.map((p) => p.port)
  const port = used.length ? Math.max(...used) + 1 : 8080
  spec.value.service.ports.push({ name: `port-${spec.value.service.ports.length + 1}`, port, target_port: port })
}

function addMount() {
  const first = props.fileSets.find((s) => !spec.value.mounts.some((m) => m.config === s.name))
  spec.value.mounts.push({ config: first?.name ?? '', path: '/app/config', items: [], volume: '' })
}

function changeMountConfig(index: number) {
  spec.value.mounts[index].items = []
}

function setAnnotation(index: number, key: string, value: string) {
  const entries = annotations.value.map((a, i) => (i === index ? { key, value } : a))
  spec.value.pod_annotations = Object.fromEntries(entries.filter((a) => a.key).map((a) => [a.key, a.value]))
}

function addAnnotation() {
  spec.value.pod_annotations = { ...spec.value.pod_annotations, [`example.com/key-${annotations.value.length + 1}`]: '' }
}

function removeAnnotation(key: string) {
  const next = { ...spec.value.pod_annotations }
  delete next[key]
  spec.value.pod_annotations = next
}

const ingressPortOptions = computed(() => spec.value.service.ports.map((p) => p.port))
</script>

<template>
  <el-form label-position="top" :disabled="readonly" class="spec-form">
    <section class="group">
      <h4>工作负载</h4>
      <div class="form-grid">
        <el-form-item label="资源基础名（base）">
          <el-input v-model="spec.base" :placeholder="serviceName || '默认等于服务名'" maxlength="50" />
          <div class="hint">对象名按环境档案的命名规则生成：Deployment <span class="mono">{{ objectName('deployment') }}</span></div>
        </el-form-item>
        <el-form-item label="容器名">
          <el-input v-model="spec.container.name" :placeholder="objectName('deployment')" maxlength="63" />
        </el-form-item>
        <el-form-item label="副本数">
          <el-input-number :model-value="spec.replicas ?? undefined" :min="0" :max="Number(policy?.max_replicas ?? 10)"
            :placeholder="`环境默认 ${profile?.defaults.replicas ?? 1}`" :disabled="spec.hpa.enabled || readonly"
            controls-position="right" @update:model-value="(v?: number | null) => (spec.replicas = v ?? null)" />
          <div class="hint">{{ spec.hpa.enabled ? '已启用 HPA，副本数由 HPA 调整' : '留空使用环境默认值' }}</div>
        </el-form-item>
        <el-form-item label="镜像拉取策略">
          <el-select v-model="spec.container.image_pull_policy">
            <el-option v-for="p in PULL_POLICIES" :key="p" :label="p" :value="p" />
          </el-select>
        </el-form-item>
        <el-form-item label="优雅退出时间（秒）">
          <el-input-number v-model="spec.workload.termination_grace_period_seconds" :min="0" :max="3600" controls-position="right" />
        </el-form-item>
        <el-form-item label="注入 Downward API">
          <el-switch v-model="spec.container.downward_api" />
          <div class="hint">HOST_IP、POD_NAME、POD_NAMESPACE、POD_IP</div>
        </el-form-item>
      </div>
    </section>

    <section class="group">
      <h4>健康检查 <span class="hint">启动 6 秒 × 30 次、就绪每 15 秒、存活每 30 秒，超时 5 秒</span></h4>
      <div class="form-grid">
        <el-form-item label="方式">
          <el-radio-group v-model="spec.probe.type" size="small">
            <el-radio-button v-for="[value, label] in PROBE_TYPES" :key="value" :value="value">{{ label }}</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <el-form-item v-if="spec.probe.type === 'http'" label="协议">
          <el-radio-group v-model="spec.probe.scheme" size="small"><el-radio-button value="HTTP">HTTP</el-radio-button><el-radio-button value="HTTPS">HTTPS</el-radio-button></el-radio-group>
        </el-form-item>
        <el-form-item v-if="spec.probe.type === 'http'" label="路径"><el-input v-model="spec.probe.path" /></el-form-item>
        <el-form-item v-if="spec.probe.type !== 'none'" label="端口">
          <el-input-number v-model="spec.probe.port" :min="1" :max="65535" controls-position="right" />
        </el-form-item>
      </div>
    </section>

    <section class="group">
      <h4>资源 <span class="hint">留空使用环境默认值</span></h4>
      <div class="form-grid">
        <el-form-item v-for="[key, label] in RESOURCES" :key="key" :label="label">
          <el-input :model-value="spec.resources[key] ?? ''" :placeholder="resourceDefault(key)" @update:model-value="(v: string) => setResource(key, v)" />
        </el-form-item>
      </div>
    </section>

    <section class="group">
      <h4>Service <el-switch v-model="spec.service.enabled" class="gap-l" /></h4>
      <template v-if="spec.service.enabled">
        <el-form-item label="类型">
          <el-radio-group v-model="spec.service.type">
            <el-radio-button v-for="t in SERVICE_TYPES" :key="t" :value="t" :disabled="!policyTypes.includes(t)">{{ t }}</el-radio-button>
          </el-radio-group>
          <div class="hint">命名空间策略允许：{{ policyTypes.join('、') }}</div>
        </el-form-item>
        <el-table :data="spec.service.ports" size="small">
          <el-table-column label="名称" min-width="140"><template #default="{ row }"><el-input v-model="row.name" /></template></el-table-column>
          <el-table-column label="端口" width="140"><template #default="{ row }"><el-input-number v-model="row.port" :min="1" :max="65535" controls-position="right" /></template></el-table-column>
          <el-table-column label="容器端口" width="140"><template #default="{ row }"><el-input-number v-model="row.target_port" :min="1" :max="65535" controls-position="right" /></template></el-table-column>
          <el-table-column v-if="spec.service.type !== 'ClusterIP'" label="节点端口（可选）" width="160">
            <template #default="{ row }"><el-input-number :model-value="row.node_port ?? undefined" :min="1" :max="65535" controls-position="right"
              @update:model-value="(v?: number | null) => (row.node_port = v ?? null)" /></template>
          </el-table-column>
          <el-table-column width="56">
            <template #default="{ $index }"><el-button link type="danger" :icon="Delete" aria-label="删除端口" :disabled="spec.service.ports.length <= 1" @click="spec.service.ports.splice($index, 1)" /></template>
          </el-table-column>
        </el-table>
        <el-button size="small" :icon="Plus" class="section-gap" @click="addPort">添加端口</el-button>
      </template>
    </section>

    <section class="group">
      <h4>Ingress
        <el-switch v-model="spec.ingress.enabled" :disabled="readonly || !spec.service.enabled || !ingressPolicy || !ingressAvailable" class="gap-l" />
        <span v-if="!ingressPolicy" class="hint">命名空间策略未启用 Ingress</span>
        <span v-else-if="!ingressAvailable" class="hint">环境没有配置负载均衡入口</span>
      </h4>
      <div v-if="spec.ingress.enabled" class="form-grid">
        <el-form-item label="主机名"><el-input v-model="spec.ingress.host" :placeholder="hostDefault || '按环境的主机名规则生成'" /></el-form-item>
        <el-form-item label="路径"><el-input v-model="spec.ingress.path" /></el-form-item>
        <el-form-item label="转发到 Service 端口">
          <el-select :model-value="spec.ingress.port ?? undefined" clearable placeholder="第一个端口" @update:model-value="(v: unknown) => (spec.ingress.port = typeof v === 'number' ? v : null)">
            <el-option v-for="p in ingressPortOptions" :key="p" :label="String(p)" :value="p" />
          </el-select>
        </el-form-item>
        <el-form-item label="路径匹配方式（CCE）">
          <el-select v-model="spec.ingress.url_match_mode" placeholder="使用入口默认值">
            <el-option v-for="[value, label] in URL_MODES" :key="value" :label="label" :value="value" />
          </el-select>
        </el-form-item>
      </div>
    </section>

    <section class="group">
      <h4>弹性伸缩（HPA）<el-switch v-model="spec.hpa.enabled" :disabled="readonly || !hpaPolicy" class="gap-l" />
        <span v-if="!hpaPolicy" class="hint">命名空间策略未启用 HPA</span></h4>
      <div v-if="spec.hpa.enabled" class="form-grid">
        <el-form-item label="最少副本"><el-input-number v-model="spec.hpa.min_replicas" :min="1" :max="spec.hpa.max_replicas" controls-position="right" /></el-form-item>
        <el-form-item label="最多副本"><el-input-number v-model="spec.hpa.max_replicas" :min="spec.hpa.min_replicas" :max="Number(policy?.max_replicas ?? 10)" controls-position="right" /></el-form-item>
        <el-form-item label="CPU 目标利用率（%）"><el-input-number v-model="spec.hpa.cpu_utilization" :min="1" :max="100" controls-position="right" /></el-form-item>
      </div>
    </section>

    <section class="group">
      <h4>配置挂载 <span class="hint">公共配置文件、证书目录、服务自己的 properties；不选文件时整体挂载为目录，选文件时逐个以 subPath 挂载</span></h4>
      <el-table :data="spec.mounts" size="small" empty-text="没有挂载">
        <el-table-column label="配置" min-width="200">
          <template #default="{ row, $index }">
            <el-select v-model="row.config" filterable placeholder="选择配置文件集" @change="changeMountConfig($index)">
              <el-option v-for="s in fileSets" :key="s.name" :label="`${s.name}（${s.scope}）`" :value="s.name" />
            </el-select>
          </template>
        </el-table-column>
        <el-table-column label="挂载目录" min-width="170"><template #default="{ row }"><el-input v-model="row.path" /></template></el-table-column>
        <el-table-column label="文件（留空 = 整个目录）" min-width="240">
          <template #default="{ row }">
            <el-select v-model="row.items" multiple collapse-tags collapse-tags-tooltip placeholder="整个目录">
              <el-option v-for="key in setKeys(row.config)" :key="key" :label="key" :value="key" />
            </el-select>
          </template>
        </el-table-column>
        <el-table-column label="卷名（可选）" width="150"><template #default="{ row }"><el-input v-model="row.volume" placeholder="自动" /></template></el-table-column>
        <el-table-column width="56">
          <template #default="{ $index }"><el-button link type="danger" :icon="Delete" aria-label="删除挂载" @click="spec.mounts.splice($index, 1)" /></template>
        </el-table-column>
      </el-table>
      <el-button size="small" :icon="Plus" class="section-gap" :disabled="!fileSets.length" @click="addMount">添加挂载</el-button>
      <span v-if="!fileSets.length" class="hint gap-l">平台、项目、环境层还没有配置文件或敏感文件</span>
      <div class="form-grid section-gap">
        <el-form-item label="挂载环境的日志目录">
          <el-switch v-model="spec.log.enabled" :disabled="readonly || !profile?.log.enabled" />
          <div class="hint">{{ profile?.log.enabled ? `${profile.log.host_path} → ${profile.log.mount_path}` : '环境档案没有启用日志目录' }}</div>
        </el-form-item>
        <el-form-item v-if="spec.log.enabled" label="日志卷名"><el-input v-model="spec.log.volume" /></el-form-item>
      </div>
    </section>

    <section class="group">
      <h4>容器环境变量 <span class="hint">直接写在容器上的少量变量；大多数参数请放在服务或环境的“环境变量”配置中</span></h4>
      <el-table :data="spec.container.env" size="small" empty-text="没有">
        <el-table-column label="名称" min-width="180"><template #default="{ row }"><el-input v-model="row.name" /></template></el-table-column>
        <el-table-column label="值" min-width="240"><template #default="{ row }"><el-input v-model="row.value" /></template></el-table-column>
        <el-table-column width="56"><template #default="{ $index }"><el-button link type="danger" :icon="Delete" aria-label="删除变量" @click="spec.container.env.splice($index, 1)" /></template></el-table-column>
      </el-table>
      <el-button size="small" :icon="Plus" class="section-gap" @click="spec.container.env.push({ name: '', value: '' })">添加变量</el-button>
    </section>

    <section class="group">
      <h4>Pod 注解</h4>
      <div v-for="(item, index) in annotations" :key="index" class="annotation">
        <el-input :model-value="item.key" placeholder="键" @change="(v: string) => setAnnotation(index, v, item.value)" />
        <el-input :model-value="item.value" placeholder="值" @change="(v: string) => setAnnotation(index, item.key, v)" />
        <el-button link type="danger" :icon="Delete" aria-label="删除注解" @click="removeAnnotation(item.key)" />
      </div>
      <el-button size="small" :icon="Plus" @click="addAnnotation">添加注解</el-button>
    </section>

    <el-collapse class="group">
      <el-collapse-item title="高级：对象名覆盖（导入命名不规范的已有服务时使用）" name="names">
        <div class="form-grid">
          <el-form-item v-for="[role, label] in NAME_ROLES" :key="role" :label="label">
            <el-input :model-value="spec.names[role] ?? ''" :placeholder="defaultName(role)" @update:model-value="(v: string) => setName(role, v)" />
          </el-form-item>
        </div>
        <p class="hint">Deployment 的 selector：<span class="mono">{{ Object.keys(spec.selector).length ? JSON.stringify(spec.selector) : `{"app": "${objectName('deployment')}"}` }}</span>（创建后不能修改）</p>
      </el-collapse-item>
    </el-collapse>
  </el-form>
</template>

<style scoped>
.group + .group { margin-top: 18px; }
.group h4 { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; margin: 0 0 10px; font-size: 14px; }
.gap-l { margin-left: 6px; }
.annotation { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr) 32px; gap: 8px; margin-bottom: 8px; }
.spec-form :deep(.el-input-number) { width: 100%; }
</style>
