<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { deploymentApi, legacyDeployApi } from '@/api'
import type { DeployProvider, DeployTarget, Deployment } from '@/api/types'
import DeploymentLogDialog from '@/components/DeploymentLogDialog.vue'
import StatusTag from '@/components/StatusTag.vue'
import { confirmAction, notifyError, useAction } from '@/composables/useAction'
import { usePlatform } from '@/stores/platform'
import { useSession } from '@/stores/session'
import { fmtTime } from '@/utils/format'

const session = useSession()
const platform = usePlatform()
const providers = ref<DeployProvider[]>([])
const targets = ref<DeployTarget[]>([])
const records = ref<Deployment[]>([])
const loading = ref(false)
const { busy, run } = useAction()
const logId = ref<number | null>(null)
// eslint-disable-next-line @typescript-eslint/no-explicit-any -- 适配器字段由后端元数据决定
const config = reactive<Record<string, any>>({})
const clearSecrets = reactive<Record<string, boolean>>({})
const form = reactive({ visible: false, target: null as DeployTarget | null, name: '', environment: '', type: '',
  appIds: [] as number[], enabled: true })

const canManage = computed(() => session.can('deploy.manage'))
const available = computed(() => providers.value.filter((p) => p.available))
const provider = computed(() => providers.value.find((p) => p.type === form.type))

async function load() {
  loading.value = true
  try {
    const [p, t, d] = await Promise.all([legacyDeployApi.providers(), legacyDeployApi.targets(), deploymentApi.list({ limit: 50 }),
      platform.loadApps()])
    providers.value = p
    targets.value = t.items
    records.value = d.items.filter((x) => !x.service_id)
  } catch (e) {
    notifyError(e)
  } finally {
    loading.value = false
  }
}

function fillConfig() {
  for (const key of Object.keys(config)) delete config[key]
  for (const key of Object.keys(clearSecrets)) delete clearSecrets[key]
  const t = form.target
  for (const f of provider.value?.fields ?? []) {
    const saved = t && t.type === form.type && t.config && f.key in t.config ? t.config[f.key] : f.default
    config[f.key] = f.type === 'bool' ? !!saved : f.type === 'int' ? Number(saved ?? 0) : (saved ?? '')
    clearSecrets[f.key] = false
  }
}

function open(target: DeployTarget | null) {
  Object.assign(form, { visible: true, target, name: target?.name ?? '', environment: target?.environment ?? '',
    type: target?.type ?? available.value[0]?.type ?? '', appIds: [...(target?.app_ids ?? [])], enabled: target?.enabled ?? true })
  fillConfig()
}

async function save() {
  const p = provider.value
  if (!p) return
  const payloadConfig: Record<string, unknown> = { ...config }
  const clear = p.fields.filter((f) => f.type === 'secret' && clearSecrets[f.key]).map((f) => f.key)
  for (const key of clear) payloadConfig[key] = ''
  const ok = await run(() => legacyDeployApi.save(form.target?.id ?? null, {
    name: form.name.trim(), environment: form.environment.trim(), type: p.type, config: payloadConfig, clear_secrets: clear,
    app_ids: form.appIds, enabled: form.enabled, auto_deploy: false,
  }), '目标已保存')
  if (ok) {
    form.visible = false
    await load()
  }
}

async function test(target: DeployTarget) {
  if (target.type === 'webhook' && !(await confirmAction('测试会发送 ping 请求，请确认接收端支持该协议且不会误触发发布。继续？', '连接测试'))) return
  await run(async () => {
    const r = await legacyDeployApi.test(target.id)
    ElMessage({ type: 'success', message: r.message, duration: 7000 })
  })
}

async function remove(target: DeployTarget) {
  if (!(await confirmAction('删除目标？已保存的部署记录不会删除。', '删除目标', true))) return
  if (await run(() => legacyDeployApi.remove(target.id), '目标已删除')) await load()
}

function appNames(ids: number[]): string {
  return ids.length ? ids.map((id) => platform.appName(id)).join('、') : '未绑定'
}

onMounted(load)
</script>

<template>
  <div class="page" v-loading="loading">
    <div class="page-heading">
      <div>
        <h2>部署中心（旧部署目标）</h2>
        <div class="hint">旧目标仅用于迁移核对和连接测试；发布请到“资源管理 › 服务”从服务修订发起。</div>
      </div>
      <div class="toolbar">
        <el-button @click="load">刷新</el-button>
        <el-button v-if="canManage" type="primary" @click="open(null)">新增目标</el-button>
      </div>
    </div>
    <el-alert type="warning" :closable="false" show-icon
      title="旧版目标不会执行手工或自动发布。v1.2 起发布统一使用固定 digest、命名空间授权和审批队列。" />

    <el-card class="section-gap">
      <template #header><span class="card-title">部署目标</span></template>
      <el-table :data="targets" empty-text="暂无目标">
        <el-table-column prop="name" label="目标" min-width="160" />
        <el-table-column prop="type" label="类型" width="110" />
        <el-table-column label="环境" width="110"><template #default="{ row }">{{ row.environment || '-' }}</template></el-table-column>
        <el-table-column label="绑定应用" min-width="200"><template #default="{ row }">{{ appNames(row.app_ids) }}</template></el-table-column>
        <el-table-column label="状态" width="90"><template #default="{ row }">{{ row.enabled ? '启用' : '停用' }}</template></el-table-column>
        <el-table-column label="操作" width="200">
          <template #default="{ row }">
            <template v-if="canManage">
              <el-button link type="primary" @click="open(row)">编辑</el-button>
              <el-button link type="primary" :disabled="busy" @click="test(row)">连接测试</el-button>
              <el-button link type="danger" @click="remove(row)">删除</el-button>
            </template>
            <span v-else class="hint">只读</span>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-card>
      <template #header><span class="card-title">历史部署记录</span></template>
      <el-table :data="records" empty-text="暂无部署记录">
        <el-table-column label="#" width="80"><template #default="{ row }"><span class="mono">#{{ row.id }}</span></template></el-table-column>
        <el-table-column label="目标 / 应用" min-width="160"><template #default="{ row }">{{ row.target_name }}<div class="hint">{{ row.app_name }}</div></template></el-table-column>
        <el-table-column label="镜像" min-width="260"><template #default="{ row }"><span class="mono break-word">{{ row.image }}</span></template></el-table-column>
        <el-table-column label="状态" min-width="140"><template #default="{ row }"><StatusTag :status="row.status" /><div class="hint">{{ row.message || row.error }}</div></template></el-table-column>
        <el-table-column label="时间 / 操作人" width="170"><template #default="{ row }">{{ fmtTime(row.created_at) }}<div class="hint">{{ row.operator }}</div></template></el-table-column>
        <el-table-column label="操作" width="80"><template #default="{ row }"><el-button link @click="logId = row.id">日志</el-button></template></el-table-column>
      </el-table>
    </el-card>

    <el-card>
      <template #header><span class="card-title">适配器</span></template>
      <div class="adapters">
        <div v-for="p in providers" :key="p.type" class="adapter">
          <b>{{ p.label }}</b>
          <p class="hint">{{ p.description }}</p>
          <el-tag :type="p.available ? 'success' : 'info'">{{ p.available ? '可配置（需环境联调）' : '扩展预留' }}</el-tag>
        </div>
      </div>
    </el-card>

    <el-dialog v-model="form.visible" :title="form.target ? '编辑旧部署目标' : '新增旧部署目标'" width="720px" top="5vh">
      <el-alert type="info" :closable="false" title="旧部署目标仅供迁移核对和连接测试，不再执行发布或自动部署。" />
      <el-form label-position="top" class="section-gap">
        <div class="form-grid">
          <el-form-item label="目标名称"><el-input v-model="form.name" maxlength="80" /></el-form-item>
          <el-form-item label="环境标识"><el-input v-model="form.environment" maxlength="80" /></el-form-item>
          <el-form-item label="适配器类型">
            <el-select v-model="form.type" :disabled="!!form.target" @change="fillConfig">
              <el-option v-for="p in available" :key="p.type" :label="p.label" :value="p.type" />
            </el-select>
          </el-form-item>
        </div>
        <p v-if="provider" class="hint">{{ provider.description }}</p>
        <el-form-item v-for="f in provider?.fields ?? []" :key="f.key" :label="f.label + (f.required ? ' *' : '')">
          <el-switch v-if="f.type === 'bool'" v-model="config[f.key]" />
          <el-input-number v-else-if="f.type === 'int'" v-model="config[f.key]" :min="0" />
          <el-input
            v-else-if="f.key === 'headers' || f.key === 'ca_cert'"
            v-model="config[f.key]"
            type="textarea"
            :rows="4"
            class="mono"
            spellcheck="false"
            :placeholder="f.type === 'secret' && form.target?.secrets_set?.[f.key] ? '已设置，留空保持不变' : ''"
          />
          <el-input
            v-else
            v-model="config[f.key]"
            :type="f.type === 'secret' ? 'password' : 'text'"
            :show-password="f.type === 'secret'"
            :autocomplete="f.type === 'secret' ? 'new-password' : 'off'"
            :placeholder="f.type === 'secret' && form.target?.secrets_set?.[f.key] ? '已设置，留空保持不变' : ''"
          />
          <div v-if="f.help" class="hint">{{ f.help }}</div>
          <el-checkbox v-if="f.type === 'secret' && form.target" v-model="clearSecrets[f.key]" size="small">清空已保存的值</el-checkbox>
        </el-form-item>
        <el-form-item label="绑定应用">
          <el-checkbox-group v-model="form.appIds">
            <el-checkbox v-for="a in platform.apps" :key="a.id" :value="a.id">{{ a.name }}</el-checkbox>
          </el-checkbox-group>
        </el-form-item>
        <el-form-item><el-checkbox v-model="form.enabled">允许连接测试</el-checkbox></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="form.visible = false">取消</el-button>
        <el-button type="primary" :loading="busy" @click="save">保存</el-button>
      </template>
    </el-dialog>
    <DeploymentLogDialog v-model="logId" />
  </div>
</template>

<style scoped>
.adapters {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
  gap: 16px;
}
</style>
