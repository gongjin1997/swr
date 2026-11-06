<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { deploymentApi, serviceApi } from '@/api'
import { idempotencyKey } from '@/api/http'
import type { Deployment, Revision, RevisionDiff, Service } from '@/api/types'
import DeployDialog from '@/components/DeployDialog.vue'
import DeploymentLogDialog from '@/components/DeploymentLogDialog.vue'
import StatusTag from '@/components/StatusTag.vue'
import { confirmAction, notifyError, useAction } from '@/composables/useAction'
import { usePlatform } from '@/stores/platform'
import { useSession } from '@/stores/session'
import { fmtTime } from '@/utils/format'

const props = defineProps<{ id: number }>()
const session = useSession()
const platform = usePlatform()
const service = ref<Service | null>(null)
const revisions = ref<Revision[]>([])
const deployments = ref<Deployment[]>([])
const loading = ref(false)
const { busy, run } = useAction()
const deployOpen = ref(false)
const logId = ref<number | null>(null)
const compare = reactive({ from: null as number | null, to: null as number | null, result: null as RevisionDiff | null })
const editor = reactive({ visible: false, advanced: false, name: '', container: '', json: '' })
const secret = reactive({ visible: false, key: '', value: '' })

const imageOnly = computed(() => service.value?.spec && (service.value.spec as { mode?: string }).mode === 'image_only')
const target = computed(() => (service.value?.spec as { target?: { name: string; container: string } } | undefined)?.target)
const secretKeys = computed(() => Object.entries(revisions.value[0]?.secret_versions ?? {}))

async function load() {
  loading.value = true
  try {
    const [s, r, d] = await Promise.all([
      serviceApi.get(props.id),
      serviceApi.revisions(props.id),
      deploymentApi.list({ limit: 100 }).catch(() => ({ items: [] as Deployment[], total: 0 })),
      platform.loadApps(),
    ])
    service.value = s
    revisions.value = r.items
    deployments.value = d.items.filter((x) => x.service_id === props.id)
    if (r.items.length >= 2 && compare.from === null) {
      compare.to = r.items[0].id
      compare.from = r.items[1].id
    }
  } catch (e) {
    notifyError(e)
  } finally {
    loading.value = false
  }
}

async function runDiff() {
  if (compare.from === null || compare.to === null) return
  await run(async () => {
    compare.result = await serviceApi.diff(props.id, compare.from!, compare.to!)
  })
}

function openEditor() {
  if (!service.value) return
  Object.assign(editor, {
    visible: true, advanced: !imageOnly.value, name: target.value?.name ?? '', container: target.value?.container ?? '',
    json: JSON.stringify(service.value.spec, null, 2),
  })
}

async function saveRevision() {
  if (!service.value) return
  const ok = await run(async () => {
    let spec: unknown
    if (editor.advanced) {
      try {
        spec = JSON.parse(editor.json)
      } catch {
        throw new Error('Spec 不是有效 JSON')
      }
    } else {
      spec = { mode: 'image_only', target: { kind: 'Deployment', name: editor.name.trim(), container: editor.container.trim() } }
    }
    await serviceApi.updateSpec(props.id, service.value!.current_revision_id, spec)
  }, '新修订已保存')
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

async function rollback(d: Deployment) {
  if (!(await confirmAction(`回滚到发布 #${d.id}（构建 #${d.job_id}，修订 ${d.revision_id}）？`, '回滚服务', true))) return
  const ok = await run(() => serviceApi.rollback(props.id, idempotencyKey(), { target_deployment_id: d.id, remark: '' }), '回滚任务已提交')
  if (ok) await load()
}

async function restart() {
  if (!(await confirmAction('以最近一次成功发布的镜像和配置重新发布？', '重新发布'))) return
  const ok = await run(() => serviceApi.restart(props.id, idempotencyKey(), ''), '重新发布任务已提交')
  if (ok) await load()
}

async function decide(d: Deployment, approve: boolean) {
  if (!approve && !(await confirmAction('拒绝此服务发布？', '拒绝发布', true))) return
  const ok = await run(() => (approve ? deploymentApi.approve(d.id) : deploymentApi.reject(d.id)), approve ? '审批已提交' : '发布已拒绝')
  if (ok) await load()
}

async function remove() {
  if (!(await confirmAction('删除服务记录？不会删除集群中的 Deployment。存在进行中的发布时无法删除。', '删除服务', true))) return
  const ok = await run(() => serviceApi.remove(props.id), '服务记录已删除')
  if (ok) history.back()
}

watch(() => props.id, load)
onMounted(load)
</script>

<template>
  <div v-loading="loading">
    <el-card v-if="service">
      <template #header>
        <div class="card-title">
          <span>服务 {{ service.name }}</span>
          <div class="toolbar">
            <el-button v-if="session.can('service.deploy')" type="primary" @click="deployOpen = true">发布</el-button>
            <el-button v-if="session.can('service.operate')" @click="restart">重新发布</el-button>
            <el-button v-if="session.can('service.config')" @click="openEditor">创建配置修订</el-button>
            <el-button v-if="session.can('secret.manage')" @click="secret.visible = true">设置密钥</el-button>
            <el-button v-if="session.can('service.delete')" type="danger" plain @click="remove">删除</el-button>
          </div>
        </div>
      </template>
      <el-descriptions :column="3" border size="small">
        <el-descriptions-item label="应用">{{ platform.appName(service.app_id) }}</el-descriptions-item>
        <el-descriptions-item label="当前配置修订"><span class="mono">{{ service.current_revision_id }}</span></el-descriptions-item>
        <el-descriptions-item label="已部署修订"><span class="mono">{{ service.deployed_revision_id ?? '尚未发布' }}</span></el-descriptions-item>
        <el-descriptions-item v-if="target" label="Deployment"><span class="mono">{{ target.name }}</span></el-descriptions-item>
        <el-descriptions-item v-if="target" label="容器"><span class="mono">{{ target.container }}</span></el-descriptions-item>
        <el-descriptions-item label="密钥">
          <span v-for="[key, version] in secretKeys" :key="key" class="mono secret">{{ key }} v{{ version }}</span>
          <span v-if="!secretKeys.length" class="hint">无</span>
        </el-descriptions-item>
      </el-descriptions>
      <pre class="code-block section-gap">{{ JSON.stringify(service.spec, null, 2) }}</pre>
    </el-card>

    <el-card>
      <template #header><span class="card-title">发布记录</span></template>
      <el-table :data="deployments" empty-text="暂无发布">
        <el-table-column label="#" width="80"><template #default="{ row }"><span class="mono">#{{ row.id }}</span></template></el-table-column>
        <el-table-column prop="kind" label="类型" width="90" />
        <el-table-column label="构建 / 修订" width="130"><template #default="{ row }">#{{ row.job_id }} / {{ row.revision_id }}</template></el-table-column>
        <el-table-column label="镜像摘要" min-width="240"><template #default="{ row }"><span class="mono break-word">{{ row.digest }}</span></template></el-table-column>
        <el-table-column label="状态" min-width="160">
          <template #default="{ row }"><StatusTag :status="row.status" /><div class="hint">{{ row.message || row.error }}</div></template>
        </el-table-column>
        <el-table-column label="提交" width="170"><template #default="{ row }">{{ fmtTime(row.created_at) }}<div class="hint">{{ row.operator }}</div></template></el-table-column>
        <el-table-column label="操作" width="160">
          <template #default="{ row }">
            <template v-if="row.status === 'pending_approval' && session.can('deploy.approve')">
              <el-button link type="primary" :disabled="busy" @click="decide(row, true)">批准</el-button>
              <el-button link type="danger" :disabled="busy" @click="decide(row, false)">拒绝</el-button>
            </template>
            <el-button v-if="row.status !== 'pending_approval'" link @click="logId = row.id">日志</el-button>
            <el-button v-if="row.status === 'success' && session.can('service.rollback')" link type="warning" @click="rollback(row)">回滚到此</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-card>
      <template #header><span class="card-title">配置修订</span></template>
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
        <el-table-column prop="path" label="字段" width="220"><template #default="{ row }"><span class="mono">{{ row.path }}</span></template></el-table-column>
        <el-table-column label="旧值"><template #default="{ row }"><span class="mono">{{ JSON.stringify(row.old) }}</span></template></el-table-column>
        <el-table-column label="新值"><template #default="{ row }"><span class="mono">{{ JSON.stringify(row.new) }}</span></template></el-table-column>
      </el-table>
      <el-table :data="revisions" class="section-gap">
        <el-table-column label="修订" width="80"><template #default="{ row }"><span class="mono">{{ row.number }}</span></template></el-table-column>
        <el-table-column label="ID" width="80"><template #default="{ row }"><span class="mono">{{ row.id }}</span></template></el-table-column>
        <el-table-column label="创建时间" width="170"><template #default="{ row }">{{ fmtTime(row.created_at) }}</template></el-table-column>
        <el-table-column label="配置"><template #default="{ row }"><span class="mono break-word">{{ JSON.stringify(row.spec) }}</span></template></el-table-column>
      </el-table>
    </el-card>

    <el-dialog v-model="editor.visible" title="创建服务配置修订" width="680px">
      <el-form label-position="top">
        <el-form-item><el-switch v-model="editor.advanced" active-text="编辑 JSON" inactive-text="表单" /></el-form-item>
        <template v-if="!editor.advanced">
          <div class="form-grid">
            <el-form-item label="Deployment 名称"><el-input v-model="editor.name" /></el-form-item>
            <el-form-item label="容器名称"><el-input v-model="editor.container" /></el-form-item>
          </div>
        </template>
        <el-form-item v-else label="Spec JSON">
          <el-input v-model="editor.json" type="textarea" :rows="12" class="mono" spellcheck="false" />
        </el-form-item>
        <p class="hint">基于当前修订 {{ service?.current_revision_id }} 创建，旧修订保持不变。若期间有人创建了新修订，保存会提示冲突。</p>
      </el-form>
      <template #footer>
        <el-button @click="editor.visible = false">取消</el-button>
        <el-button type="primary" :loading="busy" @click="saveRevision">保存修订</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="secret.visible" title="设置服务密钥" width="520px">
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

    <DeployDialog v-model="deployOpen" :service="service" @submitted="load" />
    <DeploymentLogDialog v-model="logId" />
  </div>
</template>

<style scoped>
.secret {
  margin-right: 8px;
}
</style>
