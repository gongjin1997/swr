<script setup lang="ts">
/**
 * 服务在集群中的运行状态：Deployment 与滚动进度、Pod（状态、就绪、重启、启动时长）、ReplicaSet 历史、
 * HPA、Service / Ingress 与访问地址、相关事件。数据来自 /api/services/{id}/runtime，不含环境变量值与凭据。
 *
 * 布局：描述列表按宽度 3 / 2 / 1 列；镜像只显示“组织/仓库:标签”或 digest 前缀，完整值在提示里并可复制；
 * Deployment 条件都正常时只显示一行标签，有异常时才展开为表格。
 */
import { Refresh } from '@element-plus/icons-vue'
import { computed, ref } from 'vue'
import type { DeploymentCondition, KubeServicePort, ServiceRuntime as RuntimeData } from '@/api/types'
import TablePager from '@/components/TablePager.vue'
import { useMediaQuery } from '@/composables/useMediaQuery'
import { usePagination } from '@/composables/usePagination'
import { CONTAINER_STATE_TEXT, LEVEL_TAG, RUNTIME_STATE_TAG, RUNTIME_STATE_TEXT } from '@/constants'
import { copyText, fmtAge, fmtAgo, fmtTime, shortDigest, shortImage } from '@/utils/format'
import { notifyError, notifySuccess } from '@/composables/useAction'

const props = defineProps<{ data: RuntimeData | null; loading: boolean; error: string; managed?: boolean }>()
defineEmits<{ refresh: [] }>()

const medium = useMediaQuery('(max-width: 1280px)')
const small = useMediaQuery('(max-width: 767px)')
const columns = computed(() => (small.value ? 1 : medium.value ? 2 : 3))
const hpaColumns = computed(() => (small.value ? 1 : medium.value ? 2 : 4))

const eventType = ref('')
const pods = usePagination(() => props.data?.pods ?? [], { size: 10 })
const events = computed(() => (props.data?.events ?? []).filter((e) => !eventType.value || e.type === eventType.value))
const eventPage = usePagination(() => events.value, { size: 10, resetOn: () => eventType.value })
const conditions = computed(() => props.data?.deployment?.conditions ?? [])
/** ReplicaFailure 为 True 表示异常，其余条件为 True 表示正常。 */
const conditionOk = (c: DeploymentCondition) => (c.type === 'ReplicaFailure' ? c.status !== 'True' : c.status === 'True')
const conditionsHealthy = computed(() => conditions.value.every(conditionOk))
const conditionRowClass = ({ row }: { row: DeploymentCondition }) => (conditionOk(row) ? '' : 'bad-row')

const ports = (items: KubeServicePort[]) => items.map((p) =>
  `${p.name ? p.name + ' ' : ''}${p.port}→${p.targetPort}${p.nodePort ? `（节点 ${p.nodePort}）` : ''}`).join('、')
const strategy = computed(() => {
  const s = props.data?.deployment?.strategy
  if (!s) return '-'
  return s.type === 'RollingUpdate' ? `滚动更新（最多超出 ${s.max_surge ?? '25%'}，最多不可用 ${s.max_unavailable ?? '25%'}）` : s.type
})
/** 镜像缩写：组织/仓库:版本号；按 digest 引用时为 组织/仓库@sha256:前 12 位…。 */
function imageShort(ref: string): string {
  const [name, digest] = ref.split('@')
  return digest ? `${shortImage(name)}@${shortDigest(digest)}` : shortImage(name)
}

async function copy(text: string) {
  try {
    await copyText(text)
    notifySuccess('已复制')
  } catch (e) {
    notifyError(e)
  }
}
</script>

<template>
  <div v-loading="loading">
    <div class="runtime-head">
      <span v-if="data" class="hint">{{ data.cluster_name }} / {{ data.namespace_name }} · 读取于 {{ fmtTime(data.fetched_at) }}</span>
      <el-button :icon="Refresh" :loading="loading" size="small" @click="$emit('refresh')">刷新</el-button>
    </div>
    <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon />
    <template v-if="data">
      <el-alert v-for="warning in data.warnings" :key="warning" :title="warning" type="warning" :closable="false" show-icon class="gap-b" />
      <el-alert v-if="!data.found" type="info" :closable="false" show-icon class="gap-b"
        :title="managed ? `集群中还没有 Deployment ${data.target.name}：发布后由平台创建` : `集群中没有 Deployment ${data.target.name}，请检查服务配置`" />

      <section v-if="data.deployment" class="block">
        <h3>工作负载</h3>
        <el-descriptions :column="columns" border size="small" class="workload">
          <el-descriptions-item label="Deployment"><span class="mono">{{ data.deployment.name }}</span></el-descriptions-item>
          <el-descriptions-item label="状态">
            <el-tag :type="RUNTIME_STATE_TAG[data.deployment.state]" size="small">{{ RUNTIME_STATE_TEXT[data.deployment.state] }}</el-tag>
            <span class="hint gap-l">{{ data.deployment.message }}</span>
          </el-descriptions-item>
          <el-descriptions-item label="版本（revision）">{{ data.deployment.revision || '-' }}</el-descriptions-item>
          <el-descriptions-item label="副本">
            期望 {{ data.deployment.replicas }} · 已更新 {{ data.deployment.updated_replicas }} · 就绪 {{ data.deployment.ready_replicas }}
            · 可用 {{ data.deployment.available_replicas }}
          </el-descriptions-item>
          <el-descriptions-item label="更新策略">{{ strategy }}</el-descriptions-item>
          <el-descriptions-item label="创建">
            <el-tooltip :content="fmtTime(data.deployment.created_at)"><span>{{ fmtAgo(data.deployment.created_at) }}</span></el-tooltip>
          </el-descriptions-item>
          <el-descriptions-item v-if="conditions.length && conditionsHealthy" label="条件" :span="columns">
            <span v-for="c in conditions" :key="c.type" class="condition">
              <el-tag size="small" type="success" effect="plain">{{ c.type }}</el-tag>
              <span v-if="c.reason" class="hint">{{ c.reason }}</span>
            </span>
          </el-descriptions-item>
          <el-descriptions-item v-for="c in data.deployment.containers" :key="c.name" :label="`容器 ${c.name}`" :span="columns">
            <div class="image-line">
              <el-tooltip :content="c.image" placement="top"><span class="mono">{{ imageShort(c.image) }}</span></el-tooltip>
              <el-button link size="small" @click="copy(c.image)">复制</el-button>
            </div>
            <div class="hint">
              请求 {{ c.resources.requests.cpu || '-' }} / {{ c.resources.requests.memory || '-' }} · 上限
              {{ c.resources.limits.cpu || '-' }} / {{ c.resources.limits.memory || '-' }}
            </div>
            <div v-for="(text, kind) in c.probes" :key="kind" class="hint">
              {{ kind === 'startup' ? '启动检查' : kind === 'readiness' ? '就绪检查' : '存活检查' }}：{{ text }}
            </div>
          </el-descriptions-item>
        </el-descriptions>
        <el-table v-if="conditions.length && !conditionsHealthy" :data="conditions" size="small" class="section-gap conditions"
          :row-class-name="conditionRowClass">
          <el-table-column prop="type" label="条件" width="130" />
          <el-table-column label="状态" width="80"><template #default="{ row }">{{ row.status === 'True' ? '是' : row.status === 'False' ? '否' : row.status }}</template></el-table-column>
          <el-table-column prop="reason" label="原因" min-width="170" />
          <el-table-column prop="message" label="说明" min-width="260" show-overflow-tooltip />
          <el-table-column label="最近变化" width="130"><template #default="{ row }">{{ fmtAgo(row.last_transition) }}</template></el-table-column>
        </el-table>
      </section>

      <section v-if="data.hpa" class="block">
        <h3>弹性伸缩（HPA {{ data.hpa.name }}）</h3>
        <el-descriptions :column="hpaColumns" border size="small" class="workload">
          <el-descriptions-item label="副本范围">{{ data.hpa.min_replicas }} – {{ data.hpa.max_replicas }}</el-descriptions-item>
          <el-descriptions-item label="当前 / 期望">{{ data.hpa.current_replicas ?? '-' }} / {{ data.hpa.desired_replicas ?? '-' }}</el-descriptions-item>
          <el-descriptions-item v-for="t in data.hpa.targets" :key="t.resource" :label="`${t.resource.toUpperCase()} 目标`">
            {{ t.utilization != null ? `${t.utilization}%` : t.value }}（当前 {{ data.hpa.current[t.resource] != null ? `${data.hpa.current[t.resource]}%` : '-' }}）
          </el-descriptions-item>
          <el-descriptions-item label="最近伸缩">{{ fmtAgo(data.hpa.last_scale_time) }}</el-descriptions-item>
        </el-descriptions>
      </section>

      <section class="block">
        <h3>Pod <span class="hint">共 {{ data.pods.length }} 个，重启 {{ data.summary.restarts }} 次</span></h3>
        <el-table :data="pods.rows.value" size="small" row-key="name" empty-text="没有 Pod">
          <el-table-column type="expand">
            <template #default="{ row }">
              <el-table :data="row.containers" size="small" class="nested">
                <el-table-column prop="name" label="容器" width="150" />
                <el-table-column label="状态" width="150">
                  <template #default="{ row: c }">
                    {{ CONTAINER_STATE_TEXT[c.state] ?? c.state }}<span v-if="c.reason">（{{ c.reason }}）</span>
                    <div v-if="c.message" class="hint break-word">{{ c.message }}</div>
                  </template>
                </el-table-column>
                <el-table-column label="就绪" width="70"><template #default="{ row: c }">{{ c.ready ? '是' : '否' }}</template></el-table-column>
                <el-table-column label="启动于" width="150"><template #default="{ row: c }">{{ fmtTime(c.started_at) }}</template></el-table-column>
                <el-table-column label="上次退出" min-width="200">
                  <template #default="{ row: c }">
                    <span v-if="c.last_state">{{ c.last_state.reason || '退出' }}，退出码 {{ c.last_state.exit_code ?? '-' }}，{{ fmtAgo(c.last_state.finished_at) }}</span>
                    <span v-else class="hint">—</span>
                  </template>
                </el-table-column>
                <el-table-column label="镜像" min-width="240">
                  <template #default="{ row: c }">
                    <el-tooltip :content="c.image" placement="top"><span class="mono">{{ imageShort(c.image) }}</span></el-tooltip>
                    <div v-if="c.digest" class="hint mono">运行中 {{ shortDigest(c.digest) }}</div>
                  </template>
                </el-table-column>
              </el-table>
            </template>
          </el-table-column>
          <el-table-column label="Pod" min-width="230">
            <template #default="{ row }">
              <span class="mono break-word">{{ row.name }}</span>
              <el-tag v-if="row.revision" size="small" :type="row.current ? 'success' : 'info'" effect="plain" class="gap-l">
                {{ row.current ? '当前版本' : `旧版本 r${row.revision}` }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="状态" min-width="140">
            <template #default="{ row }">
              <el-tooltip :disabled="!row.message" :content="row.message" placement="top">
                <el-tag :type="LEVEL_TAG[row.level]" size="small">{{ row.status }}</el-tag>
              </el-tooltip>
            </template>
          </el-table-column>
          <el-table-column label="就绪" width="64"><template #default="{ row }">{{ row.ready_containers }}/{{ row.total_containers }}</template></el-table-column>
          <el-table-column label="重启" width="120">
            <template #default="{ row }">
              <b :class="{ warn: row.restarts > 0 }">{{ row.restarts }}</b>
              <div v-if="row.last_restart_at" class="hint">{{ fmtAgo(row.last_restart_at) }}<span v-if="row.last_restart_reason"> · {{ row.last_restart_reason }}</span></div>
            </template>
          </el-table-column>
          <el-table-column label="运行时长" width="110">
            <template #default="{ row }">
              <el-tooltip :content="`创建 ${fmtTime(row.created_at)}，启动 ${fmtTime(row.start_time)}`">
                <span>{{ fmtAge(row.start_time || row.created_at) }}</span>
              </el-tooltip>
            </template>
          </el-table-column>
          <el-table-column label="节点 / IP" min-width="140">
            <template #default="{ row }">{{ row.node || '未调度' }}<div class="hint mono">{{ row.pod_ip || '-' }}</div></template>
          </el-table-column>
        </el-table>
        <TablePager v-model:page="pods.page.value" v-model:size="pods.size.value" :total="pods.total.value" compact />
      </section>

      <section v-if="data.replicasets.length" class="block">
        <h3>版本历史（ReplicaSet）</h3>
        <el-table :data="data.replicasets" size="small">
          <el-table-column label="版本" width="90">
            <template #default="{ row }">r{{ row.revision }}<el-tag v-if="row.current" size="small" type="success" effect="plain" class="gap-l">当前</el-tag></template>
          </el-table-column>
          <el-table-column label="镜像" min-width="260">
            <template #default="{ row }">
              <div v-for="i in row.images" :key="i">
                <el-tooltip :content="i" placement="top"><span class="mono">{{ imageShort(i) }}</span></el-tooltip>
              </div>
            </template>
          </el-table-column>
          <el-table-column label="副本（就绪）" width="110"><template #default="{ row }">{{ row.replicas }}（{{ row.ready_replicas }}）</template></el-table-column>
          <el-table-column label="平台发布" width="100"><template #default="{ row }">{{ row.deployment_id ? `#${row.deployment_id}` : '—' }}</template></el-table-column>
          <el-table-column label="创建" width="120"><template #default="{ row }">{{ fmtAgo(row.created_at) }}</template></el-table-column>
        </el-table>
      </section>

      <section class="block">
        <h3>网络与访问</h3>
        <div v-if="data.access.length" class="access">
          <div v-for="point in data.access" :key="point.address" class="access-item">
            <span class="hint">{{ point.label }}</span>
            <span class="access-value">
              <span class="mono break-word">{{ point.address }}</span>
              <el-button v-if="point.type !== 'node'" link size="small" @click="copy(point.address)">复制</el-button>
            </span>
          </div>
        </div>
        <el-table :data="data.services" size="small" empty-text="没有选中该工作负载的 Service" class="section-gap">
          <el-table-column prop="name" label="Service" min-width="180" />
          <el-table-column prop="type" label="类型" width="110" />
          <el-table-column prop="cluster_ip" label="集群 IP" width="140" />
          <el-table-column label="端口" min-width="260"><template #default="{ row }">{{ ports(row.ports) }}</template></el-table-column>
        </el-table>
        <el-table v-if="data.ingresses.length" :data="data.ingresses" size="small" class="section-gap">
          <el-table-column label="Ingress" min-width="170">
            <template #default="{ row }">
              {{ row.name }}
              <el-tag v-if="row.inconsistent" size="small" type="danger" effect="plain" class="gap-l">监听器配置未生效</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="规则" min-width="320">
            <template #default="{ row }">
              <div v-for="rule in row.rules" :key="rule.host">
                <div v-for="p in rule.paths" :key="p.path" class="mono">{{ row.https ? 'https' : 'http' }}://{{ rule.host }}{{ p.path }} → {{ p.service }}:{{ p.port }}</div>
              </div>
            </template>
          </el-table-column>
          <el-table-column prop="ingress_class" label="IngressClass" width="120" />
        </el-table>
      </section>

      <section class="block">
        <h3>事件 <span class="hint">最近 {{ data.events.length }} 条，警告 {{ data.summary.warning_events }} 条</span>
          <el-radio-group v-model="eventType" size="small" class="gap-l">
            <el-radio-button value="">全部</el-radio-button>
            <el-radio-button value="Warning">警告</el-radio-button>
            <el-radio-button value="Normal">正常</el-radio-button>
          </el-radio-group>
        </h3>
        <el-table :data="eventPage.rows.value" size="small" empty-text="最近没有相关事件（Kubernetes 默认只保留约 1 小时）">
          <el-table-column label="类型" width="80">
            <template #default="{ row }"><el-tag :type="row.type === 'Warning' ? 'warning' : 'info'" size="small">{{ row.type === 'Warning' ? '警告' : '正常' }}</el-tag></template>
          </el-table-column>
          <el-table-column prop="reason" label="原因" width="160" />
          <el-table-column label="对象" min-width="200"><template #default="{ row }"><span class="hint">{{ row.kind }}</span> <span class="mono">{{ row.name }}</span></template></el-table-column>
          <el-table-column prop="message" label="说明" min-width="300" show-overflow-tooltip />
          <el-table-column label="次数" width="70"><template #default="{ row }">{{ row.count }}</template></el-table-column>
          <el-table-column label="最近发生" width="130">
            <template #default="{ row }"><el-tooltip :content="fmtTime(row.last_seen)"><span>{{ fmtAgo(row.last_seen) }}</span></el-tooltip></template>
          </el-table-column>
        </el-table>
        <TablePager v-model:page="eventPage.page.value" v-model:size="eventPage.size.value" :total="eventPage.total.value" compact />
      </section>
    </template>
  </div>
</template>

<style scoped>
.runtime-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 12px; }
.block + .block { margin-top: 22px; }
.block h3 { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; margin: 0 0 10px; font-size: 14px; font-weight: 600; }
.gap-l { margin-left: 6px; }
.gap-b { margin-bottom: 10px; }
.warn { color: var(--el-color-danger); }
.nested { margin: 0 12px 0 48px; width: auto; }
.workload :deep(.el-descriptions__label) { white-space: nowrap; }
.condition { display: inline-flex; align-items: center; gap: 6px; margin-right: 16px; }
.conditions :deep(tr.bad-row) { color: var(--el-color-danger); }
.image-line { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
/* 访问地址：标签一列、地址一列，地址左对齐 */
.access { display: grid; grid-template-columns: max-content minmax(0, 1fr); gap: 6px 16px; align-items: center; }
.access-item { display: contents; }
.access-value { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; min-width: 0; }
</style>
