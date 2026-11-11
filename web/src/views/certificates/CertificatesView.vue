<script setup lang="ts">
/** 证书与密钥文件库：上传、解析有效期、按版本轮换、查看引用、下载（需要敏感配置查看权限）。 */
import { Plus, Refresh, Search, Upload } from '@element-plus/icons-vue'
import { computed, onMounted, reactive, ref } from 'vue'
import { certificateApi } from '@/api'
import type { Certificate, CertificateDetail } from '@/api/types'
import PageHeader from '@/components/PageHeader.vue'
import { confirmAction, notifyError, useAction } from '@/composables/useAction'
import { CONFIG_KIND_TEXT, CONFIG_SCOPE_TEXT, EXPIRY_TAG, EXPIRY_TEXT } from '@/constants'
import { useSession } from '@/stores/session'
import { downloadBase64, fmtSize, fmtTime, readFileBase64 } from '@/utils/format'

const session = useSession()
const items = ref<Certificate[]>([])
const loading = ref(false)
const keyword = ref('')
const { busy, run } = useAction()
const manage = computed(() => session.canGlobal('certificate.manage'))
const canDownload = computed(() => session.canGlobal('secret.view'))
const filtered = computed(() => items.value.filter((c) => !keyword.value.trim() ||
  `${c.name} ${c.filename} ${String(c.metadata.subject ?? '')}`.toLowerCase().includes(keyword.value.trim().toLowerCase())))

const uploader = reactive({
  visible: false, rotate: null as CertificateDetail | null, name: '', description: '', password: '', notAfter: '', note: '',
  file: null as File | null,
})
const detail = reactive({ visible: false, item: null as CertificateDetail | null })
const fileInput = ref<HTMLInputElement>()

async function load() {
  loading.value = true
  try {
    items.value = (await certificateApi.list()).items
  } catch (e) {
    notifyError(e)
  } finally {
    loading.value = false
  }
}

function openUpload(rotate: CertificateDetail | null) {
  Object.assign(uploader, { visible: true, rotate, name: '', description: '', password: '', notAfter: '', note: '', file: null })
}

function pickFile() {
  const file = fileInput.value?.files?.[0] ?? null
  uploader.file = file
  if (file && !uploader.name && !uploader.rotate) uploader.name = file.name
}

async function submitUpload() {
  const file = uploader.file
  if (!file) return notifyError(new Error('请选择文件'))
  const ok = await run(async () => {
    const payload = {
      filename: file.name, content_base64: await readFileBase64(file), note: uploader.note.trim(),
      ...(uploader.password ? { password: uploader.password } : {}),
      ...(uploader.notAfter ? { not_after: uploader.notAfter } : {}),
    }
    const saved = uploader.rotate
      ? await certificateApi.rotate(uploader.rotate.id, { ...payload, base_version: uploader.rotate.version })
      : await certificateApi.create({ ...payload, name: uploader.name.trim(), description: uploader.description.trim() })
    if (detail.visible) detail.item = saved
  }, uploader.rotate ? '已上传新版本；引用旧版本的配置保持不变，需要时在配置中改为新版本' : '证书已上传')
  if (ok) {
    uploader.visible = false
    await load()
  }
}

async function openDetail(id: number) {
  detail.visible = true
  detail.item = null
  try {
    detail.item = await certificateApi.get(id)
  } catch (e) {
    notifyError(e)
  }
}

async function download(number: number) {
  const item = detail.item
  if (!item) return
  await run(async () => {
    const content = await certificateApi.content(item.id, number)
    downloadBase64(content.filename, content.content_base64)
  })
}

async function remove(item: CertificateDetail) {
  if (!await confirmAction(`删除证书"${item.name}"及其全部版本？`, '删除证书', true)) return
  if (await run(() => certificateApi.remove(item.id), '证书已删除')) {
    detail.visible = false
    await load()
  }
}

const metaText = (item: Certificate) => String(item.metadata.subject ?? item.metadata.format ?? '')
const META_FIELDS: [string, string][] = [
  ['subject', '主题'], ['issuer', '签发者'], ['serial', '序列号'], ['san', '备用名称（SAN）'],
  ['fingerprint_sha256', '指纹（SHA-256）'], ['key_type', '密钥类型'], ['key_size', '密钥长度'], ['chain_length', '证书链长度'],
]
function metaValue(item: CertificateDetail, key: string): string {
  const value = item.metadata[key]
  if (Array.isArray(value)) return value.join(', ') || '—'
  return value === undefined || value === null || value === '' ? '—' : String(value)
}
function onRowClick(row: Certificate) {
  void openDetail(row.id)
}

onMounted(load)
</script>

<template>
  <div class="page">
    <PageHeader title="证书" description="证书、私钥、Java 密钥库与其他密钥文件统一保存在这里；环境配置中的敏感文件与敏感变量按版本引用它们。">
      <el-button v-if="manage" type="primary" :icon="Plus" @click="openUpload(null)">上传证书或文件</el-button>
    </PageHeader>
    <el-card>
      <div class="filter-bar">
        <el-input v-model="keyword" :prefix-icon="Search" placeholder="搜索名称、文件名或主题" clearable aria-label="搜索证书" />
        <el-button :icon="Refresh" :loading="loading" @click="load">刷新</el-button>
        <span class="count">共 {{ filtered.length }} 个</span>
      </div>
      <el-table v-loading="loading" :data="filtered" empty-text="还没有证书" @row-click="onRowClick">
        <el-table-column label="名称" min-width="200">
          <template #default="{ row }"><b>{{ row.name }}</b><div class="hint mono">{{ row.filename }}</div></template>
        </el-table-column>
        <el-table-column prop="kind_label" label="类型" width="130" />
        <el-table-column label="主题" min-width="220">
          <template #default="{ row }"><span class="mono break-word">{{ metaText(row) || '—' }}</span></template>
        </el-table-column>
        <el-table-column label="到期" width="190">
          <template #default="{ row }">
            <el-tag size="small" :type="EXPIRY_TAG[row.status]">{{ EXPIRY_TEXT[row.status] }}</el-tag>
            <div class="hint">{{ row.not_after ? fmtTime(row.not_after) : '—' }}</div>
          </template>
        </el-table-column>
        <el-table-column label="版本" width="70"><template #default="{ row }">v{{ row.version }}</template></el-table-column>
        <el-table-column label="被引用" width="80"><template #default="{ row }">{{ row.used_by }}</template></el-table-column>
        <el-table-column label="大小" width="90"><template #default="{ row }">{{ fmtSize(row.size) }}</template></el-table-column>
      </el-table>
    </el-card>

    <el-dialog v-model="uploader.visible" :title="uploader.rotate ? `上传新版本 · ${uploader.rotate.name}` : '上传证书或文件'" width="600px"
      :close-on-click-modal="false">
      <el-form label-position="top">
        <el-form-item label="文件（最大 512 KB）">
          <input ref="fileInput" type="file" class="file-input" @change="pickFile" />
          <span class="hint">文件名会作为 Secret 中的文件名，例如 ca.pem、kafka.client.truststore.jks</span>
        </el-form-item>
        <template v-if="!uploader.rotate">
          <el-form-item label="名称"><el-input v-model="uploader.name" maxlength="128" /></el-form-item>
          <el-form-item label="说明"><el-input v-model="uploader.description" maxlength="300" /></el-form-item>
        </template>
        <div class="form-grid">
          <el-form-item label="PKCS#12 口令（只用于读取有效期，不保存）">
            <el-input v-model="uploader.password" type="password" show-password />
          </el-form-item>
          <el-form-item label="到期日（无法解析的文件可手工填写）">
            <el-date-picker v-model="uploader.notAfter" type="date" value-format="YYYY-MM-DD" placeholder="可选" />
          </el-form-item>
        </div>
        <el-form-item label="版本说明"><el-input v-model="uploader.note" maxlength="300" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="uploader.visible = false">取消</el-button>
        <el-button type="primary" :icon="Upload" :loading="busy" :disabled="!uploader.file" @click="submitUpload">上传</el-button>
      </template>
    </el-dialog>

    <el-drawer v-model="detail.visible" :title="detail.item ? `证书 · ${detail.item.name}` : '证书'" size="min(860px, 100vw)">
      <div v-if="detail.item" v-loading="busy">
        <div class="toolbar drawer-actions">
          <el-button v-if="manage" type="primary" :icon="Upload" @click="openUpload(detail.item)">上传新版本</el-button>
          <el-button v-if="manage" @click="remove(detail.item)">删除</el-button>
        </div>
        <el-descriptions :column="2" border size="small">
          <el-descriptions-item label="类型">{{ detail.item.kind_label }}</el-descriptions-item>
          <el-descriptions-item label="当前版本">v{{ detail.item.version }} · {{ detail.item.filename }}</el-descriptions-item>
          <el-descriptions-item label="到期">
            <el-tag size="small" :type="EXPIRY_TAG[detail.item.status]">{{ EXPIRY_TEXT[detail.item.status] }}</el-tag>
            {{ detail.item.not_after ? fmtTime(detail.item.not_after) : '' }}
            <span v-if="detail.item.days_left !== null" class="hint">（剩余 {{ detail.item.days_left }} 天）</span>
          </el-descriptions-item>
          <el-descriptions-item label="SHA-256"><span class="mono break-word">{{ detail.item.sha256 }}</span></el-descriptions-item>
          <el-descriptions-item v-for="[key, label] in META_FIELDS" :key="key" :label="label">
            <span class="mono break-word">{{ metaValue(detail.item, key) }}</span>
          </el-descriptions-item>
        </el-descriptions>

        <h4>版本</h4>
        <el-table :data="detail.item.versions" size="small">
          <el-table-column label="版本" width="70"><template #default="{ row }">v{{ row.number }}</template></el-table-column>
          <el-table-column label="文件" min-width="160"><template #default="{ row }"><span class="mono">{{ row.filename }}</span></template></el-table-column>
          <el-table-column label="到期" width="170"><template #default="{ row }">{{ row.not_after ? fmtTime(row.not_after) : '—' }}</template></el-table-column>
          <el-table-column prop="note" label="说明" min-width="140" />
          <el-table-column label="上传" width="170"><template #default="{ row }">{{ row.created_by_username || '-' }} · {{ fmtTime(row.created_at) }}</template></el-table-column>
          <el-table-column v-if="canDownload" width="80">
            <template #default="{ row }"><el-button link type="primary" @click="download(row.number)">下载</el-button></template>
          </el-table-column>
        </el-table>

        <h4>被这些配置引用（当前版本）</h4>
        <el-table :data="detail.item.usage" size="small" empty-text="没有配置引用该证书">
          <el-table-column label="层级" width="90"><template #default="{ row }">{{ CONFIG_SCOPE_TEXT[row.scope_type] }}</template></el-table-column>
          <el-table-column label="配置" min-width="180"><template #default="{ row }"><span class="mono">{{ row.name }}</span></template></el-table-column>
          <el-table-column label="类型" width="120"><template #default="{ row }">{{ CONFIG_KIND_TEXT[row.kind] }}</template></el-table-column>
          <el-table-column label="条目" min-width="160"><template #default="{ row }"><span class="mono">{{ row.entry_key }}</span></template></el-table-column>
          <el-table-column label="引用版本" width="90"><template #default="{ row }">v{{ row.certificate_version }}</template></el-table-column>
        </el-table>
      </div>
    </el-drawer>
  </div>
</template>

<style scoped>
.file-input { display: block; margin-bottom: 4px; }
.drawer-actions { justify-content: flex-end; margin-bottom: 12px; }
h4 { margin: 18px 0 8px; }
</style>
