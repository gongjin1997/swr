<script setup lang="ts">
/** 一个配置集：查看、编辑（保存为新版本）、查看敏感值明文、历史版本与差异。 */
import { Clock, Delete, Edit, Plus, View } from '@element-plus/icons-vue'
import { computed, reactive, ref } from 'vue'
import { certificateApi, configApi } from '@/api'
import type { CertificateOption, ConfigChange, ConfigEntryInput, ConfigSet, ConfigVersion, FileFormat } from '@/api/types'
import TablePager from '@/components/TablePager.vue'
import { confirmAction, notifyError, useAction } from '@/composables/useAction'
import { usePagination } from '@/composables/usePagination'
import { CONFIG_KIND_TEXT } from '@/constants'
import { fmtSize, fmtTime } from '@/utils/format'

const props = defineProps<{ set: ConfigSet }>()
const emit = defineEmits<{ saved: [set: ConfigSet]; removed: [] }>()

type Mode = 'value' | 'keep' | 'certificate'
interface Row {
  key: string
  value: string
  description: string
  locked: boolean
  format: FileFormat
  mode: Mode
  certificateId: number | null
  existing: boolean
}

const FORMATS: FileFormat[] = ['properties', 'yaml', 'json', 'xml', 'text']
const CHANGE_TEXT: Record<ConfigChange['change'], string> = { added: '新增', removed: '删除', changed: '修改' }
const changeText = (change: ConfigChange['change']) => CHANGE_TEXT[change]

const secret = computed(() => props.set.kind === 'secret_env' || props.set.kind === 'secret_files')
const fileKind = computed(() => props.set.kind === 'files' || props.set.kind === 'secret_files')
const lockable = computed(() => props.set.scope_type !== 'service' && !fileKind.value)
const editing = ref(false)
const rows = ref<Row[]>([])
const note = ref('')
const revealed = ref<Record<string, string>>({})
const certificates = ref<CertificateOption[]>([])
const { busy, run } = useAction()
const fileDialog = reactive({ visible: false, index: -1, title: '', content: '', readonly: true })
const history = reactive({
  visible: false, items: [] as ConfigVersion[], from: 0, to: 0, changes: [] as ConfigChange[], loaded: false,
})
const bulk = reactive({ visible: false, text: '' })
const pager = usePagination(() => props.set.entries, { size: 20 })
/** 分页后表格行号对应的条目下标（查看文件内容时使用）。 */
const entryIndex = (index: number) => (pager.page.value - 1) * pager.size.value + index

function startEdit() {
  rows.value = props.set.entries.map((e) => ({
    key: e.key, value: e.value ?? '', description: e.description, locked: e.locked, format: e.format ?? 'text',
    mode: (secret.value ? (e.source === 'certificate' ? 'certificate' : 'keep') : 'value') as Mode,
    certificateId: e.certificate_id ?? null, existing: true,
  }))
  note.value = ''
  editing.value = true
  if (secret.value && !certificates.value.length) void loadCertificates()
}

async function loadCertificates() {
  try {
    certificates.value = (await certificateApi.options()).items
  } catch (e) {
    notifyError(e)
  }
}

function addRow() {
  rows.value.push({ key: '', value: '', description: '', locked: false, format: 'properties', mode: 'value',
    certificateId: null, existing: false })
}

function removeRow(index: number) {
  rows.value.splice(index, 1)
}

function toInput(row: Row): ConfigEntryInput {
  const key = row.key.trim()
  if (!key) throw new Error('请填写键或文件名')
  const base: ConfigEntryInput = { key, description: row.description.trim() }
  if (lockable.value) base.locked = row.locked
  if (!secret.value) return fileKind.value ? { ...base, value: row.value, format: row.format } : { ...base, value: row.value }
  if (row.mode === 'keep') return { ...base, keep: true }
  if (row.mode === 'certificate') {
    if (!row.certificateId) throw new Error(`${key}：请选择证书`)
    return { ...base, certificate_id: row.certificateId }
  }
  if (!row.value) throw new Error(`${key}：请填写新值`)
  return { ...base, value: row.value }
}

async function save() {
  await run(async () => {
    const entries = rows.value.map(toInput)
    const updated = await configApi.update(props.set.id, { base_version: props.set.version, entries, note: note.value.trim() })
    editing.value = false
    revealed.value = {}
    emit('saved', updated)
  }, '已保存为新版本')
}

async function reveal() {
  await run(async () => {
    const result = await configApi.reveal(props.set.id)
    revealed.value = Object.fromEntries(Object.entries(result.values).map(([key, item]) =>
      [key, item.encoding === 'base64' ? `（二进制文件，Base64）${item.value.slice(0, 80)}…` : item.value]))
  })
}

async function remove() {
  if (!await confirmAction(`删除配置"${props.set.name}"及其全部历史版本？已发布到集群的对象不会被删除。`, '删除配置', true)) return
  if (await run(() => configApi.remove(props.set.id), '配置已删除')) emit('removed')
}

/** 每行一个 KEY=VALUE（或 KEY: VALUE）；# 开头的行忽略；已存在的键更新为新值。 */
function bulkImport() {
  for (const raw of bulk.text.split(/\r?\n/)) {
    const line = raw.trim()
    const index = line.search(/[=:]/)
    if (!line || line.startsWith('#') || index <= 0) continue
    const key = line.slice(0, index).trim()
    const value = line.slice(index + 1).trim()
    const found = rows.value.find((r) => r.key === key)
    if (found) {
      found.value = value
      found.mode = 'value'
    } else {
      rows.value.push({ key, value, description: '', locked: false, format: 'text', mode: 'value', certificateId: null,
        existing: false })
    }
  }
  bulk.visible = false
  bulk.text = ''
}

function openFile(index: number, readonly: boolean) {
  const source = readonly ? props.set.entries[index] : rows.value[index]
  Object.assign(fileDialog, { visible: true, index, readonly, title: source?.key || '新文件', content: source?.value ?? '' })
}

function closeFile(apply: boolean) {
  const row = rows.value[fileDialog.index]
  if (apply && !fileDialog.readonly && row) row.value = fileDialog.content
  fileDialog.visible = false
}

async function openHistory() {
  Object.assign(history, { visible: true, changes: [], loaded: false })
  try {
    history.items = (await configApi.versions(props.set.id)).items
    history.to = history.items[0]?.number ?? 0
    history.from = history.items[1]?.number ?? history.to
  } catch (e) {
    notifyError(e)
  }
}

async function compareVersions() {
  await run(async () => {
    history.changes = (await configApi.diff(props.set.id, history.from, history.to)).items
    history.loaded = true
  })
}
</script>

<template>
  <el-card class="config-set" shadow="never">
    <template #header>
      <div class="set-header">
        <div class="set-title">
          <b class="mono">{{ set.name }}</b>
          <el-tag size="small" effect="plain">{{ CONFIG_KIND_TEXT[set.kind] }}</el-tag>
          <el-tag size="small" type="info" effect="plain">v{{ set.version }}</el-tag>
          <span class="hint">{{ fmtTime(set.updated_at) }}</span>
          <!-- 类型的说明已在分组标题里，这里只显示配置自己的说明 -->
          <div v-if="set.description" class="hint desc">{{ set.description }}</div>
        </div>
        <div class="toolbar">
          <el-button v-if="secret && set.can_reveal && !editing" :icon="View" :loading="busy" @click="reveal">查看明文</el-button>
          <el-button :icon="Clock" @click="openHistory">历史版本</el-button>
          <template v-if="set.can_edit && !editing">
            <el-button type="primary" :icon="Edit" @click="startEdit">编辑</el-button>
            <el-button :icon="Delete" @click="remove">删除</el-button>
          </template>
        </div>
      </div>
    </template>

    <el-table v-if="!editing" :data="pager.rows.value" size="small" empty-text="还没有条目，点击“编辑”添加">
      <el-table-column :label="fileKind ? '文件名' : '键'" min-width="210">
        <template #default="{ row }">
          <span class="mono">{{ row.key }}</span>
          <el-tag v-if="row.locked" size="small" type="warning" effect="plain" class="lock-tag">已锁定</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="值" min-width="300">
        <template #default="{ row, $index }">
          <template v-if="secret">
            <span v-if="revealed[row.key] !== undefined" class="mono break-word">{{ revealed[row.key] }}</span>
            <span v-else-if="row.source === 'certificate'">证书：{{ row.certificate_name }} v{{ row.certificate_version }}</span>
            <span v-else class="hint">已设置（{{ fmtSize(row.size) }}）</span>
          </template>
          <el-button v-else-if="fileKind" link type="primary" @click="openFile(entryIndex($index), true)">查看内容（{{ row.format }}）</el-button>
          <span v-else class="mono break-word">{{ row.value }}</span>
        </template>
      </el-table-column>
      <el-table-column prop="description" label="说明" min-width="160" />
    </el-table>
    <TablePager v-if="!editing" v-model:page="pager.page.value" v-model:size="pager.size.value" :total="pager.total.value" compact />

    <template v-else>
      <el-table :data="rows" size="small" empty-text="点击下方“添加”">
        <el-table-column :label="fileKind ? '文件名' : '键'" min-width="210">
          <template #default="{ row }">
            <el-input v-model="row.key" :disabled="row.existing" :placeholder="fileKind ? 'application.properties' : 'KEY'" />
          </template>
        </el-table-column>
        <el-table-column label="值" min-width="330">
          <template #default="{ row, $index }">
            <template v-if="secret">
              <el-radio-group v-model="row.mode" size="small">
                <el-radio-button v-if="row.existing" value="keep">保持不变</el-radio-button>
                <el-radio-button value="value">填写新值</el-radio-button>
                <el-radio-button value="certificate">引用证书</el-radio-button>
              </el-radio-group>
              <el-input v-if="row.mode === 'value'" v-model="row.value" type="password" show-password class="cell-gap"
                placeholder="新值" />
              <el-select v-if="row.mode === 'certificate'" v-model="row.certificateId" filterable class="cell-gap"
                placeholder="选择证书或密钥文件">
                <el-option v-for="c in certificates" :key="c.id" :label="`${c.name}（${c.kind_label} · v${c.version}）`"
                  :value="c.id" />
              </el-select>
            </template>
            <template v-else-if="fileKind">
              <el-select v-model="row.format" size="small" class="format-select" aria-label="文件格式">
                <el-option v-for="f in FORMATS" :key="f" :label="f" :value="f" />
              </el-select>
              <el-button link type="primary" @click="openFile($index, false)">编辑内容（{{ row.value.length }} 字符）</el-button>
            </template>
            <el-input v-else v-model="row.value" type="textarea" :autosize="{ minRows: 1, maxRows: 6 }" />
          </template>
        </el-table-column>
        <el-table-column label="说明" min-width="150">
          <template #default="{ row }"><el-input v-model="row.description" maxlength="200" /></template>
        </el-table-column>
        <el-table-column v-if="lockable" label="锁定" width="72">
          <template #default="{ row }"><el-switch v-model="row.locked" aria-label="下层不能覆盖" /></template>
        </el-table-column>
        <el-table-column width="56">
          <template #default="{ $index }"><el-button link type="danger" :icon="Delete" aria-label="删除条目" @click="removeRow($index)" /></template>
        </el-table-column>
      </el-table>
      <div class="edit-bar">
        <el-button :icon="Plus" @click="addRow">添加</el-button>
        <el-button v-if="!fileKind" @click="bulk.visible = true">批量粘贴</el-button>
        <el-input v-model="note" placeholder="修改说明（可选）" maxlength="300" class="note" />
        <el-button @click="editing = false">取消</el-button>
        <el-button type="primary" :loading="busy" @click="save">保存为 v{{ set.version + 1 }}</el-button>
      </div>
    </template>
  </el-card>

  <el-dialog v-model="fileDialog.visible" :title="fileDialog.title" width="880px" top="5vh">
    <el-input v-model="fileDialog.content" type="textarea" :rows="24" :readonly="fileDialog.readonly" class="mono file-content" />
    <template #footer>
      <el-button @click="closeFile(false)">{{ fileDialog.readonly ? '关闭' : '取消' }}</el-button>
      <el-button v-if="!fileDialog.readonly" type="primary" @click="closeFile(true)">确定</el-button>
    </template>
  </el-dialog>

  <el-dialog v-model="bulk.visible" title="批量粘贴" width="640px">
    <p class="hint">每行一个 KEY=VALUE（也接受 KEY: VALUE），# 开头的行忽略；已存在的键更新为新值。</p>
    <el-input v-model="bulk.text" type="textarea" :rows="12" class="mono" />
    <template #footer>
      <el-button @click="bulk.visible = false">取消</el-button>
      <el-button type="primary" @click="bulkImport">导入</el-button>
    </template>
  </el-dialog>

  <el-drawer v-model="history.visible" :title="`历史版本 · ${set.name}`" size="min(780px, 100vw)">
    <el-table :data="history.items" size="small">
      <el-table-column label="版本" width="80"><template #default="{ row }">v{{ row.number }}</template></el-table-column>
      <el-table-column prop="note" label="修改说明" min-width="180" />
      <el-table-column label="修改人" width="120"><template #default="{ row }">{{ row.created_by_username || '-' }}</template></el-table-column>
      <el-table-column label="时间" width="170"><template #default="{ row }">{{ fmtTime(row.created_at) }}</template></el-table-column>
    </el-table>
    <div v-if="history.items.length > 1" class="edit-bar">
      <el-select v-model="history.from" class="version-select" aria-label="旧版本">
        <el-option v-for="v in history.items" :key="v.number" :label="`v${v.number}`" :value="v.number" />
      </el-select>
      <span>→</span>
      <el-select v-model="history.to" class="version-select" aria-label="新版本">
        <el-option v-for="v in history.items" :key="v.number" :label="`v${v.number}`" :value="v.number" />
      </el-select>
      <el-button :loading="busy" @click="compareVersions">比较</el-button>
    </div>
    <el-table v-if="history.loaded" :data="history.changes" size="small" empty-text="两个版本没有差异">
      <el-table-column prop="key" label="键" min-width="160" />
      <el-table-column label="变化" width="80"><template #default="{ row }">{{ changeText(row.change) }}</template></el-table-column>
      <el-table-column label="旧值" min-width="170">
        <template #default="{ row }"><span class="mono break-word">{{ secret ? '（敏感值不显示）' : row.old ?? '' }}</span></template>
      </el-table-column>
      <el-table-column label="新值" min-width="170">
        <template #default="{ row }"><span class="mono break-word">{{ secret ? '（敏感值不显示）' : row.new ?? '' }}</span></template>
      </el-table-column>
    </el-table>
  </el-drawer>
</template>

<style scoped>
.config-set { margin-bottom: 12px; }
/* 标题区可以收窄、说明在标题区内换行，操作按钮固定在右侧；窄屏时按钮整体换到下一行 */
.set-header { display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; flex-wrap: wrap; }
.set-title { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; min-width: 0; flex: 1 1 320px; }
.set-header > .toolbar { flex: none; }
.set-title .desc { flex-basis: 100%; }
.lock-tag { margin-left: 6px; }
.cell-gap { margin-top: 6px; }
.format-select { width: 120px; margin-right: 8px; }
.edit-bar { display: flex; align-items: center; gap: 8px; margin-top: 12px; flex-wrap: wrap; }
.note { flex: 1; min-width: 200px; }
.version-select { width: 100px; }
.file-content :deep(textarea) { font-family: var(--hi-font-mono, monospace); font-size: 12px; }
</style>
