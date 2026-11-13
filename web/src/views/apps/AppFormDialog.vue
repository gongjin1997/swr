<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import type { FormInstance, FormRules } from 'element-plus'
import { appApi } from '@/api'
import type { App, AppFields } from '@/api/types'
import { notifyError, notifySuccess } from '@/composables/useAction'
import { usePlatform } from '@/stores/platform'
import { useSession } from '@/stores/session'

const props = defineProps<{ app: App | null; projectId?: number }>()
const visible = defineModel<boolean>({ required: true })
const emit = defineEmits<{ saved: [app: App] }>()
const platform = usePlatform()
const session = useSession()
const projectId = ref<number | null>(null)
const writableProjects = computed(() => session.projectsWith('app.manage'))

const FIELD_KEYS: (keyof AppFields)[] = [
  'name', 'repository', 'organization', 'description', 'base_image', 'default_mode', 'max_layers', 'target_dir',
  'target_name', 'chown', 'platform', 'latest_strategy', 'initial_tag', 'version_increment', 'tag_template', 'tag_regex',
  'push_latest',
  'full_template', 'incremental_template',
]

const TEMPLATE_NOTE =
  '可用变量：{{BASE_IMAGE}} 基础镜像（累加时为上一版本），{{COPY_FILES}} 复制上传文件的 COPY 指令，' +
  '{{TARGET_DIR}} 目标目录，{{NEW_TAG}} 新版本号，{{PREV_TAG}} 上一版本号'

const formRef = ref<FormInstance>()
const form = reactive<AppFields>({} as AppFields)
/** 按字段名动态赋值用 */
const formFields = form as unknown as Record<string, unknown>
const saving = ref(false)
const importing = reactive({ open: false, text: '', busy: false, warnings: [] as string[] })

const rules: FormRules = {
  name: [{ required: true, message: '请填写应用名称', trigger: 'blur' }],
  repository: [
    { required: true, message: '请填写镜像名称', trigger: 'blur' },
    { pattern: /^[a-z0-9]+(?:[._/-][a-z0-9]+)*$/, message: '镜像名称只能是小写字母、数字和 . _ - /', trigger: 'blur' },
  ],
  tag_regex: [
    {
      validator: (_r, value: string, cb) => {
        try {
          if (value) new RegExp(value)
          cb()
        } catch {
          cb(new Error('正则表达式无效'))
        }
      },
      trigger: 'blur',
    },
  ],
}

const orgPlaceholder = computed(() => {
  const org = platform.info?.registry.organization
  return org ? `留空使用默认组织 ${org}` : '留空使用默认组织'
})

watch(visible, async (open) => {
  if (!open) return
  importing.open = false
  importing.warnings = []
  projectId.value = props.app?.project_id ?? writableProjects.value.find(p => p.id === props.projectId)?.id ?? writableProjects.value[0]?.id ?? null
  try {
    const defaults = await platform.loadAppDefaults()
    const source = { ...defaults, ...(props.app ?? {}) } as AppFields
    for (const key of FIELD_KEYS) formFields[key] = source[key] ?? ''
    form.max_layers = Number(source.max_layers ?? 0)
    form.push_latest = !!source.push_latest
    form.version_increment = source.version_increment === 'last' ? 'last' : 'carry'
    formRef.value?.clearValidate()
  } catch (e) {
    notifyError(e)
  }
})

async function runImport() {
  if (!importing.text.trim()) return notifyError(new Error('请粘贴 Dockerfile 内容'))
  importing.busy = true
  try {
    const r = await appApi.importDockerfile(importing.text)
    for (const key of ['base_image', 'target_dir', 'target_name', 'chown', 'full_template'] as const) {
      if (r[key] !== undefined) formFields[key] = r[key]
    }
    for (const key of ['name', 'repository'] as const) if (!form[key] && r[key]) form[key] = r[key] as string
    importing.warnings = r.warnings
    importing.open = false
    notifySuccess('已识别并填充，请检查后保存')
  } catch (e) {
    notifyError(e)
  } finally {
    importing.busy = false
  }
}

async function save() {
  const target = projectId.value
  if (!target) return notifyError(new Error('请选择有应用管理权限的项目'))
  if (!(await formRef.value?.validate().catch(() => false))) return
  saving.value = true
  try {
    const data = { ...form, max_layers: Number(form.max_layers || 0) }
    const saved = props.app ? await appApi.update(props.app.id, data) : await appApi.create({ ...data, project_id: target })
    notifySuccess('应用已保存')
    visible.value = false
    emit('saved', saved)
  } catch (e) {
    notifyError(e)
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <el-dialog v-model="visible" :title="app ? `编辑应用：${app.name}` : '新建应用'" width="860px" :close-on-click-modal="false" top="5vh">
    <div class="import">
      <el-button size="small" @click="importing.open = !importing.open">从现有 Dockerfile 导入</el-button>
      <span class="hint">自动识别基础镜像、jar 目标路径、属主并生成模板</span>
      <div v-if="importing.open" class="section-gap">
        <el-input v-model="importing.text" type="textarea" :rows="8" class="mono" placeholder="粘贴现有的 Dockerfile 内容" spellcheck="false" />
        <div class="right section-gap"><el-button size="small" type="primary" :loading="importing.busy" @click="runImport">识别并填充</el-button></div>
      </div>
      <el-alert v-if="importing.warnings.length" type="warning" :closable="false" class="section-gap">
        <ul class="list"><li v-for="w in importing.warnings" :key="w">{{ w }}</li></ul>
      </el-alert>
    </div>

    <el-form ref="formRef" :model="form" :rules="rules" label-position="top">
      <h4>基本信息</h4>
      <div class="form-grid">
        <el-form-item label="所属项目" required><el-select v-model="projectId" :disabled="!!app" placeholder="选择所属项目"><el-option v-for="project in writableProjects" :key="project.id" :value="project.id" :label="project.name" /></el-select></el-form-item>
        <el-form-item label="应用名称" prop="name"><el-input v-model="form.name" placeholder="如 cnnc-coding-chc-fin" /></el-form-item>
        <el-form-item label="镜像名称" prop="repository">
          <el-input v-model="form.repository" placeholder="SWR 中的镜像名（小写）" />
          <div class="hint">镜像地址 = 仓库地址/组织/镜像名称:版本号</div>
        </el-form-item>
        <el-form-item label="组织"><el-input v-model="form.organization" :placeholder="orgPlaceholder" /></el-form-item>
        <el-form-item label="描述"><el-input v-model="form.description" placeholder="可选" /></el-form-item>
      </div>

      <h4>构建设置</h4>
      <div class="form-grid">
        <el-form-item label="基础镜像" class="wide">
          <el-input v-model="form.base_image" placeholder="swr.xxx/metaerp_sdk/java:8-huawei-r30-arm64" />
          <div class="hint">全量构建和首次构建时使用</div>
        </el-form-item>
        <el-form-item label="默认构建方式">
          <el-select v-model="form.default_mode">
            <el-option label="累加构建（基于上一版本）" value="incremental" />
            <el-option label="全量构建（基于基础镜像）" value="full" />
          </el-select>
        </el-form-item>
        <el-form-item label="累加层数上限">
          <el-input-number v-model="form.max_layers" :min="0" :max="127" />
          <div class="hint">上一版本层数达到该值自动改为全量构建，0 为不限制</div>
        </el-form-item>
        <el-form-item label="目标目录">
          <el-input v-model="form.target_dir" placeholder="/app" />
          <div class="hint">上传文件复制到镜像内的目录</div>
        </el-form-item>
        <el-form-item label="目标文件名">
          <el-input v-model="form.target_name" placeholder="如 cnnc-coding-chc-fin.jar" />
          <div class="hint">只上传一个文件时重命名为它；留空保持原名</div>
        </el-form-item>
        <el-form-item label="文件属主">
          <el-input v-model="form.chown" placeholder="如 nonroot:nonroot" />
          <div class="hint">对应 COPY --chown，留空不设置</div>
        </el-form-item>
        <el-form-item label="构建平台">
          <el-input v-model="form.platform" placeholder="如 linux/arm64" />
          <div class="hint">构建机架构与镜像不一致时填写</div>
        </el-form-item>
      </div>

      <h4>版本规则</h4>
      <div class="form-grid">
        <el-form-item label="上一版本判定">
          <el-select v-model="form.latest_strategy">
            <el-option label="版本号最大的标签" value="version" />
            <el-option label="最近推送的标签" value="time" />
          </el-select>
        </el-form-item>
        <el-form-item label="首个版本号">
          <el-input v-model="form.initial_tag" placeholder="1.0.0" />
          <div class="hint">仓库里还没有版本时使用</div>
        </el-form-item>
        <el-form-item label="版本递增方式">
          <el-select v-model="form.version_increment">
            <el-option label="逢十进一（1.0.9 → 1.1.0）" value="carry" />
            <el-option label="末位递增（1.0.9 → 1.0.10）" value="last" />
          </el-select>
          <div class="hint">逢十进一：除第一位外每一位只用 0–9，例如 1.0.8 → 1.0.9 → 1.1.0、1.9.9 → 2.0.0</div>
        </el-form-item>
        <el-form-item label="版本号模板">
          <el-input v-model="form.tag_template" placeholder="可选，如 1.0.{seq} 或 {date}.{seq}" />
          <div class="hint">填写后按模板生成版本号，不使用上面的递增方式</div>
        </el-form-item>
        <el-form-item label="标签过滤（正则）" prop="tag_regex">
          <el-input v-model="form.tag_regex" placeholder="可选，如 ^\d+\.\d+\.\d+$" />
          <div class="hint">只把匹配的标签当作历史版本</div>
        </el-form-item>
        <el-form-item class="wide"><el-checkbox v-model="form.push_latest">同时推送 latest 标签</el-checkbox></el-form-item>
      </div>

      <h4>Dockerfile 模板</h4>
      <el-alert type="info" :closable="false" class="tpl-note" :title="TEMPLATE_NOTE" />
      <el-form-item label="全量构建模板">
        <el-input v-model="form.full_template" type="textarea" :rows="9" class="mono" spellcheck="false" />
      </el-form-item>
      <el-form-item label="累加构建模板">
        <el-input v-model="form.incremental_template" type="textarea" :rows="4" class="mono" spellcheck="false" />
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="visible = false">取消</el-button>
      <el-button type="primary" :loading="saving" @click="save">保存</el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
h4 {
  margin: 18px 0 10px;
}
.import {
  padding-bottom: 8px;
  border-bottom: 1px solid var(--el-border-color-lighter);
}
.import .hint {
  margin-left: 8px;
}
.right {
  text-align: right;
}
.list {
  margin: 0;
  padding-left: 18px;
}
.tpl-note {
  margin-bottom: 12px;
}
</style>
