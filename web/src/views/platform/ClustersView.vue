<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { clusterApi } from '@/api'
import type { Cluster, ClusterDiscovery } from '@/api/types'
import { notifyError, notifySuccess, useAction } from '@/composables/useAction'
import { Close, Upload } from '@element-plus/icons-vue'
import TablePager from '@/components/TablePager.vue'
import { usePagination } from '@/composables/usePagination'

const clusters = ref<Cluster[]>([])
const pager = usePagination(() => clusters.value)
const loading = ref(false)
const testing = ref<number | null>(null)
const discovery = ref<ClusterDiscovery | null>(null)
const discoveryOpen = ref(false)
const discovering = ref(false)
const discoveryError = ref('')
async function inspect(cluster: Cluster) {
  discoveryOpen.value = true
  discovering.value = true
  discovery.value = null
  discoveryError.value = ''
  try { discovery.value = await clusterApi.discover(cluster.id) }
  catch (e) { discoveryError.value = e instanceof Error ? e.message : '获取集群资源失败' }
  finally { discovering.value = false }
}
const { busy, run } = useAction()
const clientCertFile = ref<HTMLInputElement | null>(null)
const clientKeyFile = ref<HTMLInputElement | null>(null)
const caFile = ref<HTMLInputElement | null>(null)
const form = reactive({
  visible: false, id: null as number | null, name: '', server: '', cce: '',
  authMode: 'config' as 'config' | 'token' | 'client_cert', originalAuthType: '', config: '', token: '', ca: '',
  clientCertPem: '', clientKeyPem: '', clientCertName: '', clientKeyName: '', caName: '',
})

async function load() {
  loading.value = true
  try {
    clusters.value = (await clusterApi.list()).items
  } catch (e) {
    notifyError(e)
  } finally {
    loading.value = false
  }
}

function open(cluster: Cluster | null) {
  Object.assign(form, {
    visible: true, id: cluster?.id ?? null, name: cluster?.name ?? '', server: cluster?.server ?? '',
    cce: cluster?.cce_cluster_id ?? '', authMode: cluster?.auth_type === 'sa_token' ? 'token'
      : cluster?.auth_type === 'client_cert' ? 'client_cert' : 'config',
    originalAuthType: cluster?.auth_type ?? '', config: '', token: '', ca: '',
    clientCertPem: '', clientKeyPem: '', clientCertName: '', clientKeyName: '', caName: '',
  })
}

async function save() {
  const base = { name: form.name.trim(), cce_cluster_id: form.cce.trim() }
  let payload: Parameters<typeof clusterApi.create>[0]
  if (form.authMode === 'config') {
    if (!form.config.trim() && (!form.id || form.originalAuthType !== 'client_cert')) {
      notifyError(new Error('请粘贴 kubeconfig YAML'))
      return
    }
    payload = form.config.trim() ? { ...base, config: form.config } : base
  } else if (form.authMode === 'token') {
    if (!form.token && (!form.id || form.originalAuthType !== 'sa_token')) {
      notifyError(new Error('请输入 ServiceAccount Token'))
      return
    }
    payload = {
      ...base, server: form.server.trim(), auth_type: 'sa_token',
      ...(form.token ? { credential: { token: form.token, ca_pem: form.ca } } : {}),
    }
  } else {
    const hasCert = !!form.clientCertPem
    const hasKey = !!form.clientKeyPem
    const hasCredentialChange = hasCert || hasKey || !!form.ca
    if (hasCert !== hasKey || (hasCredentialChange && !hasCert) ||
        (!hasCert && (!form.id || form.originalAuthType !== 'client_cert'))) {
      notifyError(new Error('请同时上传客户端证书和私钥'))
      return
    }
    payload = {
      ...base, server: form.server.trim(),
      ...(hasCert ? { auth_type: 'client_cert' as const,
        credential: { client_cert_pem: form.clientCertPem, client_key_pem: form.clientKeyPem, ca_pem: form.ca } } : {}),
    }
  }
  const ok = await run(async () => {
    const saved = form.id
      ? await clusterApi.update(form.id, payload)
      : await clusterApi.create(payload)
    const result = await clusterApi.test(saved.id)
    notifySuccess(`已连接 Kubernetes ${result.version}`)
  })
  if (ok) {
    form.visible = false
    await load()
  } else if (!form.id) {
    await load() // 创建成功但连接测试失败时，列表里已经有这个集群
  }
}

function authLabel(type: string) {
  if (type === 'client_cert') return '客户端证书'
  if (type === 'sa_token') return 'SA Token'
  return type
}

function clearCredentials() {
  form.config = ''
  form.token = ''
  form.ca = ''
  form.clientCertPem = ''
  form.clientKeyPem = ''
  form.clientCertName = ''
  form.clientKeyName = ''
  form.caName = ''
  if (clientCertFile.value) clientCertFile.value.value = ''
  if (clientKeyFile.value) clientKeyFile.value.value = ''
  if (caFile.value) caFile.value.value = ''
}

function removePem(kind: 'clientCertPem' | 'clientKeyPem' | 'ca') {
  if (kind === 'clientCertPem') {
    form.clientCertPem = ''
    form.clientCertName = ''
    if (clientCertFile.value) clientCertFile.value.value = ''
  } else if (kind === 'clientKeyPem') {
    form.clientKeyPem = ''
    form.clientKeyName = ''
    if (clientKeyFile.value) clientKeyFile.value.value = ''
  } else {
    form.ca = ''
    form.caName = ''
    if (caFile.value) caFile.value.value = ''
  }
}

async function readPem(event: Event, kind: 'clientCertPem' | 'clientKeyPem' | 'ca') {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  try {
    if (file.size > 1024 * 1024) throw new Error('证书文件不能超过 1 MiB')
    const value = await file.text()
    if (kind === 'clientCertPem' && !value.includes('-----BEGIN CERTIFICATE-----')) throw new Error('客户端证书必须是 PEM 格式')
    if (kind === 'clientKeyPem' && !/-----BEGIN (?:RSA |EC )?PRIVATE KEY-----/.test(value)) throw new Error('私钥必须是未加密 PEM 格式')
    if (kind === 'ca' && !value.includes('-----BEGIN CERTIFICATE-----')) throw new Error('CA 证书必须是 PEM 格式')
    form[kind] = value
    if (kind === 'clientCertPem') form.clientCertName = file.name
    if (kind === 'clientKeyPem') form.clientKeyName = file.name
    if (kind === 'ca') form.caName = file.name
  } catch (e) {
    input.value = ''
    notifyError(e)
  }
}

async function test(cluster: Cluster) {
  testing.value = cluster.id
  try {
    const r = await clusterApi.test(cluster.id)
    notifySuccess(`已连接 ${r.version}`)
    await load()
  } catch (e) {
    notifyError(e)
  } finally {
    testing.value = null
  }
}

onMounted(load)
</script>

<template>
  <el-card>
    <template #header>
      <div class="card-title"><span>集群</span><el-button type="primary" @click="open(null)">纳管集群</el-button></div>
    </template>
    <el-table v-loading="loading" :data="pager.rows.value" empty-text="暂无纳管集群">
      <el-table-column label="集群" min-width="180">
        <template #default="{ row }"><b>{{ row.name }}</b><div class="hint">{{ row.cce_cluster_id }}</div></template>
      </el-table-column>
      <el-table-column label="API Server" min-width="260"><template #default="{ row }"><span class="mono break-word">{{ row.server }}</span></template></el-table-column>
      <el-table-column label="认证" width="130"><template #default="{ row }">{{ authLabel(row.auth_type) }}</template></el-table-column>
      <el-table-column label="状态" width="110">
        <template #default="{ row }"><el-tag :type="row.status === 'ready' ? 'success' : 'info'">{{ row.status === 'ready' ? '可用' : '未验证' }}</el-tag></template>
      </el-table-column>
      <el-table-column label="操作" width="240">
        <template #default="{ row }">
          <el-button link type="primary" :loading="testing === row.id" @click="test(row)">连接测试</el-button>
          <el-button link type="primary" @click="open(row)">编辑</el-button>
          <el-button link type="primary" @click="inspect(row)">集群资源</el-button>
        </template>
      </el-table-column>
    </el-table>
    <TablePager v-model:page="pager.page.value" v-model:size="pager.size.value" :total="pager.total.value" />
  </el-card>

  <el-dialog v-model="discoveryOpen" title="集群资源" width="760px">
    <div v-loading="discovering">
      <el-alert v-if="discoveryError" type="error" :title="discoveryError" :closable="false" />
      <template v-if="discovery"><p>Kubernetes {{ discovery.version }} · {{ discovery.namespaces.length }} 个命名空间 · {{ discovery.nodes.length }} 个节点</p>
        <el-alert v-for="warning in discovery.warnings" :key="warning" type="warning" :title="warning" :closable="false" />
        <el-table :data="discovery.nodes"><el-table-column prop="name" label="节点" /><el-table-column prop="version" label="版本" /><el-table-column prop="architecture" label="架构" /><el-table-column label="状态"><template #default="{ row }">{{ row.ready ? '就绪' : '未就绪' }}</template></el-table-column></el-table>
        <el-table :data="discovery.namespaces" class="section-gap"><el-table-column prop="name" label="命名空间" /><el-table-column prop="status" label="状态" /><el-table-column label="纳管状态"><template #default="{ row }">{{ row.managed_id ? '已纳管' : '未纳管' }}</template></el-table-column></el-table>
      </template>
    </div>
  </el-dialog>
  <el-dialog v-model="form.visible" :title="form.id ? '编辑集群' : '纳管集群'" width="680px" @closed="clearCredentials">
    <el-form label-position="top">
      <div class="form-grid">
        <el-form-item label="名称"><el-input v-model="form.name" maxlength="80" /></el-form-item>
      </div>
      <el-form-item label="认证配置">
        <el-radio-group v-model="form.authMode" aria-label="集群认证方式">
          <el-radio-button value="config">kubeconfig Config</el-radio-button>
          <el-radio-button value="token">SA Token</el-radio-button>
          <el-radio-button value="client_cert">客户端证书</el-radio-button>
        </el-radio-group>
      </el-form-item>
      <template v-if="form.authMode === 'config'">
        <el-form-item label="kubeconfig YAML">
          <el-input v-model="form.config" type="textarea" :rows="10" class="mono" spellcheck="false"
            maxlength="1048576" show-word-limit autocomplete="off"
            :placeholder="form.id ? '粘贴新 Config 以更换认证；留空保留现有配置' : 'apiVersion: v1\nkind: Config\ncurrent-context: ...\n...'" />
        </el-form-item>
        <el-alert type="info" :closable="false" title="读取 current-context；支持静态 Token 或内嵌客户端证书/私钥。exec 插件和外部证书文件不会执行或读取。" />
      </template>
      <template v-else-if="form.authMode === 'token'">
        <el-form-item label="Kubernetes API Server（HTTPS）"><el-input v-model="form.server" placeholder="https://kube.example:5443" /></el-form-item>
        <el-form-item label="ServiceAccount Token">
          <el-input v-model="form.token" type="password" show-password autocomplete="new-password" :placeholder="form.id ? '留空保持不变' : ''" />
        </el-form-item>
        <el-form-item label="CA 证书 PEM（可选）">
          <el-input v-model="form.ca" type="textarea" :rows="4" class="mono" spellcheck="false" :placeholder="form.id ? '输入新 Token 时可同步提交 CA' : ''" />
          <div class="pem-upload ca-upload-row">
            <input ref="caFile" class="visually-hidden" type="file" accept=".pem,.crt" @change="readPem($event, 'ca')" />
            <el-button :icon="Upload" @click="caFile?.click()">上传 CA 证书</el-button>
            <span class="hint">{{ form.caName || '也可粘贴 PEM 内容' }}</span>
          </div>
        </el-form-item>
      </template>
      <template v-else>
        <el-form-item label="Kubernetes API Server（HTTPS）"><el-input v-model="form.server" placeholder="https://kube.example:6443" /></el-form-item>
        <el-form-item label="客户端证书 PEM">
          <div class="pem-upload">
            <input ref="clientCertFile" class="visually-hidden" type="file" accept=".pem,.crt" @change="readPem($event, 'clientCertPem')" />
            <el-button :icon="Upload" @click="clientCertFile?.click()">上传证书</el-button>
            <span class="hint">{{ form.clientCertName || '选择 .pem 或 .crt 文件，最大 1 MiB' }}</span>
            <el-button v-if="form.clientCertName" text circle :icon="Close" aria-label="移除客户端证书" @click="removePem('clientCertPem')" />
          </div>
        </el-form-item>
        <el-form-item label="客户端私钥 PEM">
          <div class="pem-upload">
            <input ref="clientKeyFile" class="visually-hidden" type="file" accept=".pem,.key" @change="readPem($event, 'clientKeyPem')" />
            <el-button :icon="Upload" @click="clientKeyFile?.click()">上传私钥</el-button>
            <span class="hint">{{ form.clientKeyName || '选择未加密 PEM 私钥，最大 1 MiB' }}</span>
            <el-button v-if="form.clientKeyName" text circle :icon="Close" aria-label="移除客户端私钥" @click="removePem('clientKeyPem')" />
          </div>
        </el-form-item>
        <el-form-item label="CA 证书 PEM（可选）">
          <div class="pem-upload">
            <input ref="caFile" class="visually-hidden" type="file" accept=".pem,.crt" @change="readPem($event, 'ca')" />
            <el-button :icon="Upload" @click="caFile?.click()">上传 CA</el-button>
            <span class="hint">{{ form.caName || '使用系统信任库时留空；最大 1 MiB' }}</span>
            <el-button v-if="form.caName" text circle :icon="Close" aria-label="移除 CA 证书" @click="removePem('ca')" />
          </div>
        </el-form-item>
        <el-alert type="info" :closable="false" title="客户端证书和私钥会加密保存；每个文件最大 1 MiB。" />
      </template>
      <el-alert type="info" :closable="false" title="保存后凭据会加密存储。纳管不会创建集群资源，也不会授予 Kubernetes 权限。" />
    </el-form>
    <template #footer>
      <el-button @click="form.visible = false">取消</el-button>
      <el-button type="primary" :loading="busy" @click="save">连接并保存</el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.pem-upload { display: flex; align-items: center; gap: 10px; min-width: 0; }
.ca-upload-row { margin-top: 8px; }
.pem-upload .hint { min-width: 0; overflow-wrap: anywhere; }
.visually-hidden { position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px; overflow: hidden; clip: rect(0, 0, 0, 0); white-space: nowrap; border: 0; }
</style>
