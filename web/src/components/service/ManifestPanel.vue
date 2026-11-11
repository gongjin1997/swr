<script setup lang="ts">
/**
 * 托管服务的对象清单：按当前配置渲染（敏感值已隐藏）、在集群中做服务端校验（dry-run，列出与线上的差异），
 * 以及最近一次成功发布实际下发的快照。
 */
import { computed, onMounted, ref, watch } from 'vue'
import { serviceApi } from '@/api'
import type { ManifestSnapshot, RenderResult, Service } from '@/api/types'
import { notifyError, useAction } from '@/composables/useAction'
import { APPLY_ACTION_TAG, APPLY_ACTION_TEXT, CONFIG_KIND_TEXT, CONFIG_SCOPE_TEXT } from '@/constants'
import { fmtTime } from '@/utils/format'

const props = defineProps<{ service: Service }>()
const result = ref<RenderResult | null>(null)
const snapshot = ref<ManifestSnapshot | null>(null)
const snapshotMissing = ref(false)
const view = ref<'objects' | 'yaml' | 'snapshot'>('objects')
const expanded = ref<string[]>([])
const { busy, run } = useAction()
const canValidate = computed(() => !!props.service.permissions?.includes('service.config'))
const actions = computed(() => {
  const counts: Record<string, number> = {}
  for (const row of result.value?.dry_run ?? []) counts[row.action] = (counts[row.action] ?? 0) + 1
  return counts
})
const actionText = (action: string) => APPLY_ACTION_TEXT[action] ?? action
const pinText = (pin: { scope_type: string; kind: string; name: string; version: number }) =>
  `${CONFIG_SCOPE_TEXT[pin.scope_type as keyof typeof CONFIG_SCOPE_TEXT] ?? pin.scope_type}层 · ` +
  `${CONFIG_KIND_TEXT[pin.kind as keyof typeof CONFIG_KIND_TEXT] ?? pin.kind} ${pin.name} v${pin.version}`
const short = (value: unknown) => (value === null || value === undefined ? '（无）' : typeof value === 'string' ? value : JSON.stringify(value))

async function render(dryRun: boolean) {
  await run(async () => {
    result.value = await serviceApi.render(props.service.id, { dry_run: dryRun })
  }, dryRun ? '服务端校验完成' : undefined)
}

async function loadSnapshot() {
  try {
    snapshot.value = await serviceApi.manifest(props.service.id)
    snapshotMissing.value = false
  } catch {
    snapshot.value = null
    snapshotMissing.value = true
  }
}

async function load() {
  try {
    await Promise.all([render(false), loadSnapshot()])
  } catch (e) {
    notifyError(e)
  }
}

watch(() => props.service.current_revision_id, load)
onMounted(load)
</script>

<template>
  <div v-loading="busy && !result">
    <div class="toolbar bar">
      <el-radio-group v-model="view" size="small">
        <el-radio-button value="objects">对象（{{ result?.objects.length ?? 0 }}）</el-radio-button>
        <el-radio-button value="yaml">完整 YAML</el-radio-button>
        <el-radio-button value="snapshot">最近一次发布</el-radio-button>
      </el-radio-group>
      <span class="hint">按当前修订 {{ service.current_revision_id }} 与各层配置的最新版本渲染，敏感值已隐藏。</span>
      <el-button v-if="canValidate" type="primary" plain size="small" :loading="busy" @click="render(true)">服务端校验（dry-run）</el-button>
    </div>
    <template v-if="result">
      <el-alert v-for="e in result.errors" :key="e" type="error" :title="e" :closable="false" show-icon class="gap-b" />
      <el-alert v-for="w in result.warnings" :key="w" type="warning" :title="w" :closable="false" show-icon class="gap-b" />
      <div v-if="result.dry_run" class="gap-b">
        <div class="summary">
          <span>服务端校验：</span>
          <el-tag v-for="(count, action) in actions" :key="action" :type="APPLY_ACTION_TAG[action]" size="small" effect="plain">
            {{ actionText(String(action)) }} {{ count }}
          </el-tag>
        </div>
        <el-table :data="result.dry_run" size="small" row-key="name">
          <el-table-column type="expand">
            <template #default="{ row }">
              <el-table v-if="row.diff.length" :data="row.diff" size="small" class="nested">
                <el-table-column label="字段" min-width="260"><template #default="{ row: d }"><span class="mono break-word">{{ d.path }}</span></template></el-table-column>
                <el-table-column label="线上" min-width="200"><template #default="{ row: d }"><span class="mono break-word">{{ short(d.live) }}</span></template></el-table-column>
                <el-table-column label="将下发" min-width="200"><template #default="{ row: d }"><span class="mono break-word">{{ short(d.desired) }}</span></template></el-table-column>
              </el-table>
              <p v-else class="hint nested">{{ row.action === 'create' ? '集群中还没有该对象，将新建' : '平台声明的字段与线上一致' }}</p>
            </template>
          </el-table-column>
          <el-table-column label="对象" min-width="240"><template #default="{ row }"><span class="hint">{{ row.kind }}</span> <span class="mono">{{ row.name }}</span></template></el-table-column>
          <el-table-column label="结果" width="110"><template #default="{ row }"><el-tag :type="APPLY_ACTION_TAG[row.action]" size="small">{{ actionText(row.action) }}</el-tag></template></el-table-column>
          <el-table-column label="变化的字段" width="110"><template #default="{ row }">{{ row.diff.length || '—' }}</template></el-table-column>
          <el-table-column prop="message" label="说明" min-width="260" show-overflow-tooltip />
        </el-table>
      </div>

      <el-collapse v-if="view === 'objects'" v-model="expanded">
        <el-collapse-item v-for="obj in result.objects" :key="obj.key" :name="obj.key">
          <template #title><span class="hint obj-kind">{{ obj.kind }}</span><span class="mono">{{ obj.name }}</span></template>
          <pre class="code-block">{{ obj.yaml }}</pre>
        </el-collapse-item>
      </el-collapse>
      <pre v-else-if="view === 'yaml'" class="code-block tall">{{ result.yaml }}</pre>
      <template v-else>
        <el-empty v-if="snapshotMissing" description="还没有成功的发布" />
        <template v-else-if="snapshot">
          <p class="hint">发布 #{{ snapshot.deployment_id }} · {{ fmtTime(snapshot.created_at) }} · 环境档案 v{{ snapshot.profile_version }}
            <template v-if="snapshot.ingress_version"> · 负载均衡入口 v{{ snapshot.ingress_version }}</template></p>
          <div class="pins"><el-tag v-for="pin in snapshot.config" :key="pin.set_id" size="small" effect="plain">{{ pinText(pin) }}</el-tag></div>
          <pre class="code-block tall">{{ snapshot.yaml }}</pre>
        </template>
      </template>
      <div v-if="view !== 'snapshot' && result.config.length" class="pins section-gap">
        <span class="hint">本次渲染使用的配置：</span>
        <el-tag v-for="pin in result.config" :key="pin.set_id" size="small" effect="plain">{{ pinText(pin) }}</el-tag>
      </div>
    </template>
  </div>
</template>

<style scoped>
.bar { margin-bottom: 12px; }
.gap-b { margin-bottom: 10px; }
.summary { display: flex; gap: 8px; align-items: center; margin-bottom: 8px; flex-wrap: wrap; }
.nested { margin: 0 12px 0 48px; width: auto; }
.obj-kind { display: inline-block; min-width: 170px; }
.pins { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; margin-bottom: 8px; }
.tall { max-height: 640px; }
</style>
