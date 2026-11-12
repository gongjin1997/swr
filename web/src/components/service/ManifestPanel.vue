<script setup lang="ts">
/**
 * 托管服务的对象清单：按当前配置渲染（敏感值已隐藏）、在集群中做服务端校验（dry-run，列出与线上的差异），
 * 以及最近一次成功发布实际下发的快照。
 *
 * 对象与校验结果在同一张表里：每行是一个对象，展开后是与线上的字段差异和该对象的 YAML。
 */
import { computed, onMounted, ref, watch } from 'vue'
import { serviceApi } from '@/api'
import type { ApplyResult, ManifestSnapshot, RenderResult, Service } from '@/api/types'
import { notifyError, useAction } from '@/composables/useAction'
import { APPLY_ACTION_TAG, APPLY_ACTION_TEXT, CONFIG_KIND_TEXT, CONFIG_SCOPE_TEXT } from '@/constants'
import { fmtTime } from '@/utils/format'

interface ObjectRow {
  key: string
  kind: string
  name: string
  yaml: string
  check: ApplyResult | null
}

const props = defineProps<{ service: Service }>()
const result = ref<RenderResult | null>(null)
const snapshot = ref<ManifestSnapshot | null>(null)
const snapshotMissing = ref(false)
const view = ref<'objects' | 'yaml' | 'snapshot'>('objects')
const { busy, run } = useAction()
const canValidate = computed(() => !!props.service.permissions?.includes('service.config'))
const validated = computed(() => !!result.value?.dry_run)
const actions = computed(() => {
  const counts: Record<string, number> = {}
  for (const row of result.value?.dry_run ?? []) counts[row.action] = (counts[row.action] ?? 0) + 1
  return counts
})
/** 渲染出的对象，加上校验结果；上次发布有、本次没有（将删除）的对象只出现在校验结果里，也列出来。 */
const rows = computed<ObjectRow[]>(() => {
  const current = result.value
  if (!current) return []
  const checks = new Map((current.dry_run ?? []).map((c) => [`${c.kind}/${c.name}`, c]))
  const list: ObjectRow[] = current.objects.map((o) => ({ key: o.key, kind: o.kind, name: o.name, yaml: o.yaml,
    check: checks.get(`${o.kind}/${o.name}`) ?? null }))
  const listed = new Set(list.map((o) => `${o.kind}/${o.name}`))
  for (const c of current.dry_run ?? []) {
    if (!listed.has(`${c.kind}/${c.name}`)) list.push({ key: `${c.kind}/${c.name}`, kind: c.kind, name: c.name, yaml: '', check: c })
  }
  return list
})
const actionText = (action: string) => APPLY_ACTION_TEXT[action] ?? action
const pinText = (pin: { scope_type: string; kind: string; name: string; version: number }) =>
  `${CONFIG_SCOPE_TEXT[pin.scope_type as keyof typeof CONFIG_SCOPE_TEXT] ?? pin.scope_type}层 · ` +
  `${CONFIG_KIND_TEXT[pin.kind as keyof typeof CONFIG_KIND_TEXT] ?? pin.kind} ${pin.name} v${pin.version}`
const short = (value: unknown) => (value === null || value === undefined ? '（无）' : typeof value === 'string' ? value : JSON.stringify(value))
/** 变化的字段：列出前两个路径，其余只给数量。 */
function changedFields(check: ApplyResult | null): string {
  if (!check?.diff.length) return ''
  const paths = check.diff.map((d) => d.path)
  return paths.slice(0, 2).join('、') + (paths.length > 2 ? ` 等 ${paths.length} 项` : '')
}
function noDiffText(check: ApplyResult): string {
  if (check.action === 'create') return '集群中还没有该对象，将新建'
  if (check.action === 'delete') return '本次清单中已不包含该对象，将删除'
  if (check.action === 'error') return check.message
  return '平台声明的字段与线上一致'
}

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

      <template v-if="view === 'objects'">
        <div v-if="validated" class="summary">
          <span>服务端校验：</span>
          <el-tag v-for="(count, action) in actions" :key="action" :type="APPLY_ACTION_TAG[action]" size="small" effect="plain">
            {{ actionText(String(action)) }} {{ count }}
          </el-tag>
          <span class="hint">展开一行查看与线上的差异和 YAML</span>
        </div>
        <p v-else class="hint gap-b">展开一行查看 YAML；做一次服务端校验可以看到每个对象与线上的差异。</p>
        <el-table :data="rows" size="small" row-key="key">
          <el-table-column type="expand">
            <template #default="{ row }">
              <div class="detail">
                <template v-if="row.check">
                  <el-table v-if="row.check.diff.length" :data="row.check.diff" size="small">
                    <el-table-column label="字段" min-width="240"><template #default="{ row: d }"><span class="mono break-word">{{ d.path }}</span></template></el-table-column>
                    <el-table-column label="线上" min-width="200"><template #default="{ row: d }"><span class="mono break-word">{{ short(d.live) }}</span></template></el-table-column>
                    <el-table-column label="将下发" min-width="200"><template #default="{ row: d }"><span class="mono break-word">{{ short(d.desired) }}</span></template></el-table-column>
                  </el-table>
                  <p v-else class="hint">{{ noDiffText(row.check) }}</p>
                </template>
                <pre v-if="row.yaml" class="code-block">{{ row.yaml }}</pre>
              </div>
            </template>
          </el-table-column>
          <el-table-column label="对象" min-width="240">
            <template #default="{ row }"><span class="hint kind">{{ row.kind }}</span><span class="mono">{{ row.name }}</span></template>
          </el-table-column>
          <el-table-column label="校验结果" width="110">
            <template #default="{ row }">
              <el-tag v-if="row.check" :type="APPLY_ACTION_TAG[row.check.action]" size="small">{{ actionText(row.check.action) }}</el-tag>
              <span v-else class="hint">未校验</span>
            </template>
          </el-table-column>
          <el-table-column label="变化的字段" min-width="220">
            <template #default="{ row }">
              <span v-if="changedFields(row.check)" class="mono break-word">{{ changedFields(row.check) }}</span>
              <span v-else class="hint">—</span>
            </template>
          </el-table-column>
          <el-table-column label="说明" min-width="220" show-overflow-tooltip>
            <template #default="{ row }">{{ row.check?.message ?? '' }}</template>
          </el-table-column>
        </el-table>
      </template>
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
.detail { display: grid; gap: 10px; margin: 0 12px 0 48px; min-width: 0; }
.kind { display: inline-block; min-width: 150px; }
.pins { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; margin-bottom: 8px; }
.tall { max-height: 640px; }
</style>
