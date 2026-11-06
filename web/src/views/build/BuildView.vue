<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { appApi, buildApi, jobApi } from '@/api'
import type { App, BuildMode, Job, Preview, Versions } from '@/api/types'
import FileDropzone from '@/components/FileDropzone.vue'
import JobDetail from '@/components/JobDetail.vue'
import { errorText, notifyError, notifySuccess } from '@/composables/useAction'
import { JOB_FINAL, MODE_TEXT, TAG_RE } from '@/constants'
import { usePlatform } from '@/stores/platform'
import { fmtSize, fmtTime, shortImage } from '@/utils/format'
import { ElMessage } from 'element-plus'

const LAST_APP_KEY = 'swr-builder.app'
const platform = usePlatform()
const route = useRoute()
const router = useRouter()

const appId = ref<number | null>(null)
const versions = ref<Versions | null>(null)
const versionsError = ref('')
const versionsLoading = ref(false)
const form = reactive({ mode: 'incremental' as BuildMode, baseTag: '', tag: '', remark: '', extractZip: false })
const files = ref<File[]>([])
const uploading = ref(false)
const progress = ref(0)
const preview = reactive<{ visible: boolean; data: Preview | null }>({ visible: false, data: null })
const previewing = ref(false)

const app = computed<App | null>(() => platform.apps.find((a) => a.id === appId.value) ?? null)
const jobId = computed<number | null>(() => (route.query.job ? Number(route.query.job) : null))

const fullDisabled = computed(() => !!app.value && !app.value.base_image && (app.value.full_template || '').includes('{{BASE_IMAGE}}'))
const hasLatest = computed(() => !!versions.value?.latest)
const baseTags = computed(() => (versions.value?.tags ?? []).filter((t) => t !== versions.value?.latest).slice(0, 50))
const layerWarn = computed(() => {
  const v = versions.value
  const layers = v?.latest_info?.layers
  return !!v?.max_layers && layers !== null && layers !== undefined && layers >= v.max_layers * 0.8
})

const tagError = computed(() => {
  const tag = form.tag.trim()
  if (!tag) return ''
  if (!TAG_RE.test(tag)) return '只能包含字母、数字、. _ -，不能以 . 或 - 开头'
  if (versions.value?.tags.includes(tag)) return `版本 ${tag} 已存在，平台不会覆盖已有版本`
  return ''
})
const tagHint = computed(() => tagError.value || (versions.value ? `留空自动使用 ${versions.value.next_tag}` : '留空自动递增'))

const incrementalDesc = computed(() => {
  if (versions.value?.latest) return `在 ${versions.value.latest} 上叠加本次文件，只新增 1 层`
  if (versions.value) return '仓库暂无版本，将自动改为全量构建'
  return '在上一版本镜像上叠加本次文件'
})
const fullDesc = computed(() =>
  app.value?.base_image ? `基于 ${shortImage(app.value.base_image)} 重新构建，层数归零` : '应用未配置基础镜像',
)

/** 与后端 dockerfile.plan_files 相同的目标路径规则，仅用于展示。 */
const destinations = computed(() => {
  const a = app.value
  if (!a) return []
  const dir = (a.target_dir || '/app').replace(/\/+$/, '')
  const names = files.value.map((f) => f.name)
  return names.map((name) => {
    if (form.extractZip && /\.zip$/i.test(name)) return `${dir}/（解压后合并）`
    if (names.length === 1 && a.target_name) return `${dir}/${a.target_name}`
    return `${dir}/${name}`
  })
})

async function loadVersions() {
  const current = app.value
  if (!current) {
    versions.value = null
    return
  }
  versionsLoading.value = true
  versionsError.value = ''
  try {
    const v = await appApi.versions(current.id)
    if (appId.value === current.id) versions.value = v
  } catch (e) {
    versions.value = null
    versionsError.value = errorText(e)
  } finally {
    versionsLoading.value = false
  }
}

watch(appId, (id) => {
  versions.value = null
  form.tag = ''
  form.baseTag = ''
  if (id !== null) localStorage.setItem(LAST_APP_KEY, String(id))
  if (app.value) form.mode = app.value.default_mode
  void loadVersions()
})

watch(fullDisabled, (disabled) => {
  if (disabled && form.mode === 'full') form.mode = 'incremental'
})

function payload() {
  return {
    mode: form.mode,
    base_tag: form.mode === 'incremental' ? form.baseTag : '',
    tag: form.tag.trim(),
    filenames: files.value.map((f) => f.name),
    extract_zip: form.extractZip,
  }
}

function check(requireFiles: boolean): boolean {
  if (!app.value) return fail('请先选择应用')
  if (requireFiles && !files.value.length) return fail('请先选择要上传的文件')
  if (tagError.value) return fail(tagError.value)
  return true
}

function fail(message: string): false {
  ElMessage({ type: 'error', message })
  return false
}

async function openPreview() {
  if (!check(false)) return
  previewing.value = true
  try {
    preview.data = await appApi.preview(app.value!.id, payload())
    preview.visible = true
  } catch (e) {
    notifyError(e)
  } finally {
    previewing.value = false
  }
}

async function submit() {
  if (!check(true)) return
  uploading.value = true
  progress.value = 0
  try {
    const p = payload()
    const result = await buildApi.submit(
      { appId: app.value!.id, mode: p.mode, tag: p.tag, baseTag: p.base_tag, remark: form.remark.trim(), extractZip: p.extract_zip, files: files.value },
      (ratio) => (progress.value = Math.round(ratio * 100)),
    )
    notifySuccess(`已创建构建任务 #${result.id}`)
    files.value = []
    form.remark = ''
    form.tag = ''
    await router.replace({ query: { ...route.query, job: String(result.id) } })
  } catch (e) {
    notifyError(e)
  } finally {
    uploading.value = false
  }
}

function onFinished(job: Job) {
  if (job.status === 'success') {
    ElMessage({ type: 'success', message: `构建成功：${job.image}`, duration: 6000 })
    if (app.value?.id === job.app_id) void loadVersions()
  } else if (job.status === 'failed') {
    ElMessage({ type: 'error', message: `构建失败：${job.error || ''}`, duration: 8000, showClose: true })
  }
}

onMounted(async () => {
  try {
    await platform.loadApps(true)
  } catch (e) {
    notifyError(e)
  }
  const wanted = Number(route.query.app) || Number(localStorage.getItem(LAST_APP_KEY))
  const initial = platform.apps.find((a) => a.id === wanted) ?? platform.apps[0]
  if (initial) appId.value = initial.id
  if (!jobId.value) {
    try {
      const recent = await jobApi.list({ limit: 1 })
      const running = recent.items[0]
      if (running && !JOB_FINAL.has(running.status)) await router.replace({ query: { ...route.query, job: String(running.id) } })
    } catch {
      /* 读取最近任务失败不影响构建 */
    }
  }
})
</script>

<template>
  <div class="page">
    <div class="build-grid">
      <el-card>
        <template #header><span class="card-title">新建构建</span></template>
        <el-form label-position="top">
          <el-form-item label="应用">
            <div class="row">
              <el-select v-model="appId" filterable placeholder="请先在“应用管理”中新建应用" class="grow">
                <el-option v-for="a in platform.apps" :key="a.id" :label="a.name" :value="a.id" />
              </el-select>
              <el-button :loading="versionsLoading" @click="loadVersions">刷新版本</el-button>
            </div>
            <div v-if="app" class="hint mono">{{ app.image_repo }}:&lt;版本号&gt;</div>
          </el-form-item>

          <div v-if="app" class="version-box">
            <div v-if="versionsLoading" class="hint">正在读取 SWR 中的版本…</div>
            <el-alert v-else-if="versionsError" type="error" :closable="false" :title="`读取版本失败：${versionsError}`">
              <el-button link type="primary" @click="loadVersions">重试</el-button>
            </el-alert>
            <div v-else-if="versions && !versions.latest" class="hint">
              仓库中还没有版本，首次构建会基于基础镜像，版本号为 <b class="mono">{{ versions.next_tag }}</b>
            </div>
            <template v-else-if="versions">
              <el-descriptions :column="3" size="small" border>
                <el-descriptions-item label="上一版本"><span class="mono">{{ versions.latest }}</span></el-descriptions-item>
                <el-descriptions-item label="新版本"><b class="mono accent">{{ form.tag.trim() || versions.next_tag }}</b></el-descriptions-item>
                <el-descriptions-item label="镜像层数">
                  <span :class="{ warn: layerWarn }">{{ versions.latest_info?.layers ?? '-' }}<template v-if="versions.max_layers"> / 上限 {{ versions.max_layers }}</template></span>
                </el-descriptions-item>
                <el-descriptions-item label="压缩后大小">{{ fmtSize(versions.latest_info?.size) }}</el-descriptions-item>
                <el-descriptions-item label="架构">
                  {{ versions.latest_info?.architecture ? `${versions.latest_info.os || 'linux'}/${versions.latest_info.architecture}` : '-' }}
                </el-descriptions-item>
                <el-descriptions-item label="推送时间">{{ fmtTime(versions.latest_info?.created) }}</el-descriptions-item>
              </el-descriptions>
              <el-alert v-if="versions.warning" type="warning" :title="versions.warning" :closable="false" class="section-gap" />
            </template>
          </div>

          <el-form-item label="构建方式">
            <el-radio-group v-model="form.mode" class="modes">
              <el-radio value="incremental" border>
                <b>{{ MODE_TEXT.incremental }}</b><span class="hint mode-desc">{{ incrementalDesc }}</span>
              </el-radio>
              <el-radio value="full" border :disabled="fullDisabled">
                <b>{{ MODE_TEXT.full }}</b><span class="hint mode-desc">{{ fullDesc }}</span>
              </el-radio>
            </el-radio-group>
          </el-form-item>

          <div class="form-grid">
            <el-form-item v-if="form.mode === 'incremental' && hasLatest" label="基于版本">
              <el-select v-model="form.baseTag">
                <el-option :label="`最新版本（${versions?.latest}）`" value="" />
                <el-option v-for="t in baseTags" :key="t" :label="t" :value="t" />
              </el-select>
            </el-form-item>
            <el-form-item label="新版本号" :error="tagError">
              <el-input v-model="form.tag" :placeholder="versions?.next_tag ?? '自动递增'" autocomplete="off" spellcheck="false" />
              <div v-if="!tagError" class="hint">{{ tagHint }}</div>
            </el-form-item>
          </div>

          <el-form-item label="上传文件">
            <div class="grow">
              <FileDropzone v-model="files" :max-mb="platform.info?.max_upload_mb" :destinations="destinations" />
              <el-checkbox v-model="form.extractZip" class="section-gap">zip 包解压后合并到目标目录（适合增量补丁）</el-checkbox>
            </div>
          </el-form-item>

          <el-form-item label="备注">
            <el-input v-model="form.remark" maxlength="200" placeholder="本次变更说明（可选）" />
          </el-form-item>

          <el-progress v-if="uploading" :percentage="progress" :format="(p: number) => (p < 100 ? `上传中 ${p}%` : '上传完成，正在创建任务…')" />
          <div class="actions">
            <el-button :loading="previewing" :disabled="uploading" @click="openPreview">预览 Dockerfile</el-button>
            <el-button type="primary" :loading="uploading" @click="submit">上传并构建</el-button>
          </div>
        </el-form>
      </el-card>

      <el-card class="log-card">
        <template #header><span class="card-title">构建日志</span></template>
        <JobDetail :job-id="jobId" log-height="calc(100vh - 360px)" @finished="onFinished" />
      </el-card>
    </div>

    <el-dialog v-model="preview.visible" title="Dockerfile 预览" width="760px">
      <template v-if="preview.data">
        <el-descriptions :column="1" size="small" border>
          <el-descriptions-item label="构建方式">
            {{ MODE_TEXT[preview.data.plan.mode] }}<template v-if="preview.data.plan.mode !== preview.data.plan.requested_mode">（已自动切换）</template>
          </el-descriptions-item>
          <el-descriptions-item label="上一版本">{{ preview.data.plan.latest || '无' }}</el-descriptions-item>
          <el-descriptions-item label="基础镜像"><span class="mono">{{ preview.data.plan.base_image }}</span></el-descriptions-item>
          <el-descriptions-item label="目标镜像"><span class="mono">{{ preview.data.plan.image }}</span></el-descriptions-item>
          <el-descriptions-item label="期望架构">{{ preview.data.plan.expected_arch || '不校验' }}</el-descriptions-item>
          <el-descriptions-item label="上传文件">
            <div v-for="f in preview.data.files" :key="f.name" class="mono">{{ f.name }} → {{ f.dest }}</div>
            <span v-if="!preview.data.files.length">（未选择文件）</span>
          </el-descriptions-item>
        </el-descriptions>
        <el-alert v-if="preview.data.plan.notes.length" type="warning" :closable="false" class="section-gap">
          <ul class="notes"><li v-for="n in preview.data.plan.notes" :key="n">{{ n }}</li></ul>
        </el-alert>
        <pre class="code-block section-gap">{{ preview.data.dockerfile }}</pre>
      </template>
      <template #footer><el-button @click="preview.visible = false">关闭</el-button></template>
    </el-dialog>
  </div>
</template>

<style scoped>
.build-grid {
  display: grid;
  grid-template-columns: minmax(420px, 5fr) minmax(420px, 6fr);
  gap: 16px;
  align-items: start;
}
@media (max-width: 1100px) {
  .build-grid {
    grid-template-columns: 1fr;
  }
}
.log-card {
  position: sticky;
  top: 76px;
}
.row {
  display: flex;
  gap: 8px;
  width: 100%;
}
.grow {
  flex: 1;
  width: 100%;
}
.version-box {
  margin-bottom: 18px;
}
.modes {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 8px;
  width: 100%;
}
.modes :deep(.el-radio) {
  height: auto;
  padding: 10px 12px;
  margin-right: 0;
  white-space: normal;
  align-items: flex-start;
}
.mode-desc {
  display: block;
}
.accent {
  color: var(--el-color-primary);
}
.warn {
  color: var(--el-color-warning);
  font-weight: 600;
}
.actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 16px;
}
.notes {
  margin: 0;
  padding-left: 18px;
}
</style>
