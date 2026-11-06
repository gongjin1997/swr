<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { settingsApi, type SettingsChange } from '@/api'
import type { SettingField, SettingsView, SystemInfo } from '@/api/types'
import { notifyError, useAction } from '@/composables/useAction'
import { usePlatform } from '@/stores/platform'
import { useSession } from '@/stores/session'
import { fmtSize } from '@/utils/format'

const session = useSession()
const platform = usePlatform()
const view = ref<SettingsView | null>(null)
const system = ref<SystemInfo | null>(null)
/** 表单值（list 类型以 JSON 文本编辑）与"清空"勾选 */
// eslint-disable-next-line @typescript-eslint/no-explicit-any -- 字段类型由后端元数据决定
const values = reactive<Record<string, any>>({})
const clears = reactive<Record<string, boolean>>({})
const loading = ref(false)
const saving = useAction()
const testing = useAction()

const SELECTS: Record<string, [string, string][]> = {
  'hcs.auth_mode': [['aksk', 'AK/SK 签名换 Token（默认）'], ['password', '用户名密码'], ['token', '预置 Token']],
  'hcs.token_scope': [['project', '项目级（project）'], ['domain', '账号级（domain）']],
}
const SOURCE_TEXT: Record<string, string> = { db: '页面设置', file: '配置文件 / 默认值', env: '环境变量' }

function initial(f: SettingField): unknown {
  if (f.type === 'list') return JSON.stringify(f.value ?? [])
  if (f.type === 'secret') return ''
  return f.value
}

async function load() {
  loading.value = true
  try {
    const [v, s] = await Promise.all([settingsApi.view(), settingsApi.system()])
    view.value = v
    system.value = s
    for (const sec of v.sections) {
      for (const f of sec.fields) {
        values[f.path] = initial(f)
        clears[f.path] = false
      }
    }
  } catch (e) {
    notifyError(e)
  } finally {
    loading.value = false
  }
}

/** 只提交改动过的字段；敏感字段留空表示不修改。 */
function collect(section?: string): SettingsChange {
  const change: SettingsChange = { values: {}, clear: [] }
  for (const sec of view.value?.sections ?? []) {
    if (section && sec.key !== section) continue
    for (const f of sec.fields) {
      if (f.locked_by) continue
      if (f.type === 'secret' && clears[f.path]) {
        change.clear.push(f.path)
        continue
      }
      let v = values[f.path]
      if (f.type === 'secret' && !v) continue
      if (f.type === 'int' || f.type === 'float') {
        if (v === '' || v === null || v === undefined) throw new Error(`${f.label}不能为空`)
        v = Number(v)
      }
      if (f.type === 'list') {
        try {
          v = JSON.parse(String(v))
        } catch {
          throw new Error(`${f.label}请填写 JSON 数组`)
        }
      }
      if (JSON.stringify(v) !== JSON.stringify(f.value)) change.values[f.path] = v
    }
  }
  return change
}

async function save() {
  await saving.run(async () => {
    const change = collect()
    if (!Object.keys(change.values).length && !change.clear.length) {
      ElMessage({ type: 'info', message: '没有需要保存的修改' })
      return
    }
    await settingsApi.save(change)
    ElMessage({ type: 'success', message: '设置已保存' })
    await Promise.all([load(), session.load(), platform.loadInfo()])
  })
}

async function test(section: 'registry' | 'hcs') {
  await testing.run(async () => {
    const change = collect(section)
    const r = section === 'registry' ? await settingsApi.testRegistry(change) : await settingsApi.testHcs(change)
    const ok = section === 'registry' ? r.ok : (r as { validated?: boolean }).validated
    ElMessage({ type: ok ? 'success' : 'error', message: r.message, duration: 8000, showClose: true })
  })
}

onMounted(load)
</script>

<template>
  <div class="page" v-loading="loading">
    <div class="page-heading">
      <div>
        <h2>系统设置</h2>
        <div v-if="system" class="hint">{{ system.config_priority }} · v{{ system.version }} · 可用磁盘 {{ fmtSize(system.disk.free) }}</div>
      </div>
      <el-button type="primary" :loading="saving.busy.value" @click="save">保存修改</el-button>
    </div>
    <el-alert type="info" :closable="false" class="section-gap"
      title="仓库或构建参数请在没有构建任务时修改。已存在的登录会话有效期不会被追溯缩短。TLS / 端口 / 数据目录仍在服务器配置文件中设置。" />
    <div class="grid-2 section-gap">
      <el-card v-for="sec in view?.sections ?? []" :key="sec.key">
        <template #header>
          <div class="card-title">
            <span>{{ sec.label }}</span>
            <el-button v-if="sec.key === 'registry'" :loading="testing.busy.value" @click="test('registry')">测试当前表单</el-button>
            <el-button v-if="sec.key === 'hcs'" :loading="testing.busy.value" @click="test('hcs')">测试 IAM 认证</el-button>
          </div>
        </template>
        <el-form label-position="top">
          <el-form-item v-for="f in sec.fields" :key="f.path" :label="f.label">
            <el-select v-if="SELECTS[f.path]" v-model="values[f.path]" :disabled="!!f.locked_by">
              <el-option v-for="[value, label] in SELECTS[f.path]" :key="value" :label="label" :value="value" />
            </el-select>
            <el-switch v-else-if="f.type === 'bool'" v-model="values[f.path]" :disabled="!!f.locked_by" />
            <el-input-number
              v-else-if="f.type === 'int' || f.type === 'float'"
              v-model="values[f.path]"
              :min="f.minimum"
              :max="f.maximum"
              :step="f.type === 'int' ? 1 : 0.5"
              :disabled="!!f.locked_by"
            />
            <el-input
              v-else-if="f.type === 'list' || f.path === 'hcs.aksk_request_body'"
              v-model="values[f.path]"
              type="textarea"
              :rows="2"
              class="mono"
              spellcheck="false"
              :placeholder="f.type === 'list' ? '[&quot;--network&quot;, &quot;host&quot;]' : ''"
              :disabled="!!f.locked_by"
            />
            <el-input
              v-else-if="f.type === 'secret'"
              v-model="values[f.path]"
              type="password"
              show-password
              autocomplete="new-password"
              :placeholder="f.is_set ? '已设置，留空保持不变' : '未设置'"
              :disabled="!!f.locked_by || clears[f.path]"
            />
            <el-input v-else v-model="values[f.path]" :disabled="!!f.locked_by" />
            <div class="hint field-hint">
              {{ f.help }}
              <template v-if="f.locked_by">由环境变量 {{ f.locked_by }} 控制（只读）</template>
              <template v-else>来源：{{ SOURCE_TEXT[f.source] ?? f.source }}</template>
            </div>
            <el-checkbox v-if="f.type === 'secret' && !f.locked_by && f.is_set" v-model="clears[f.path]" size="small">清空已保存的值</el-checkbox>
          </el-form-item>
        </el-form>
      </el-card>
    </div>
  </div>
</template>

<style scoped>
.field-hint {
  width: 100%;
}
</style>
