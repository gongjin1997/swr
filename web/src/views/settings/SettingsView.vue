<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { onBeforeRouteLeave, useRoute, useRouter } from 'vue-router'
import { Box, Lock, Monitor, Refresh, Setting } from '@element-plus/icons-vue'
import SettingInput from './SettingInput.vue'
import { ElMessage } from 'element-plus'
import { settingsApi, type SettingsChange } from '@/api'
import type { SettingField, SettingsView, SystemInfo } from '@/api/types'
import { confirmAction, notifyError, useAction } from '@/composables/useAction'
import { usePlatform } from '@/stores/platform'
import { useSession } from '@/stores/session'
import { fmtSize } from '@/utils/format'

const route = useRoute()
const router = useRouter()
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

const sections = [
  { key: 'general', title: '通用设置', description: '配置系统名称与平台功能入口。', icon: Setting },
  { key: 'registry', title: '镜像仓库', description: '管理 SWR 仓库地址、登录凭证与连接参数。', icon: Box },
  { key: 'build', title: '构建参数', description: '配置构建引擎、并发任务与制品上传限制。', icon: Monitor },
  { key: 'security', title: '安全设置', description: '管理登录会话、密码要求与登录保护。', icon: Lock },
]
const active = computed(() => sections.some(s => s.key === route.query.section) ? String(route.query.section) : 'general')
const selected = computed(() => sections.find(s => s.key === active.value)!)
const fields = computed(() => view.value?.sections.find(s => s.key === active.value)?.fields ?? [])
const dirtyPaths = computed(() => (view.value?.sections ?? []).flatMap(s => s.fields).filter(f =>
  !f.locked_by && (clears[f.path] || JSON.stringify(values[f.path]) !== JSON.stringify(initial(f))),
).map(f => f.path))
const dirty = computed(() => dirtyPaths.value.length > 0)
const changedIn = (section: string) => dirtyPaths.value.some(path => path.startsWith(section + '.'))
const groups = computed(() => {
  const definitions: Record<string, { title: string; keys: string[] }[]> = {
    general: [{ title: '系统信息', keys: ['site_name', 'deploy_enabled'] }],
    registry: [
      { title: '仓库连接', keys: ['address', 'organization'] },
      { title: '登录凭证', keys: ['username', 'password', 'project', 'ak', 'sk'] },
      { title: '连接选项', keys: ['ca_file', 'timeout', 'insecure'] },
    ],
    build: [
      { title: '构建引擎', keys: ['docker_bin', 'max_concurrent', 'max_upload_mb', 'extra_build_args'] },
      { title: '超时控制', keys: ['login_timeout', 'build_timeout', 'push_timeout'] },
      { title: '构建清理', keys: ['keep_context', 'cleanup_local_images'] },
    ],
    security: [
      { title: '登录会话', keys: ['session_hours', 'remember_days'] },
      { title: '密码与登录保护', keys: ['password_min_length', 'max_login_failures', 'lock_minutes'] },
    ],
  }
  const result = (definitions[active.value] ?? []).map(g => ({ title: g.title, fields: g.keys.flatMap(key => fields.value.filter(f => f.key === key)) }))
  const known = new Set(result.flatMap(g => g.fields.map(f => f.key)))
  const extra = fields.value.filter(f => !known.has(f.key))
  if (extra.length) result.push({ title: '其他设置', fields: extra })
  return result
})
async function discard() {
  if (dirty.value && !await confirmAction('放弃尚未保存的设置修改？', '放弃修改')) return
  await load()
}
onBeforeRouteLeave(async () => !dirty.value || await confirmAction('当前有未保存的修改，确认离开？', '离开设置'))
function beforeUnload(event: BeforeUnloadEvent) {
  if (dirty.value) { event.preventDefault(); event.returnValue = '' }
}
onMounted(() => window.addEventListener('beforeunload', beforeUnload))
onBeforeUnmount(() => window.removeEventListener('beforeunload', beforeUnload))

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
          if (!Array.isArray(v)) throw new Error('not an array')
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

async function testRegistry() {
  await testing.run(async () => {
    const change = collect('registry')
    const r = await settingsApi.testRegistry(change)
    const ok = r.ok
    ElMessage({ type: ok ? 'success' : 'error', message: r.message, duration: 8000, showClose: true })
  })
}

onMounted(load)
</script>

<template>
  <section class="page settings-page" v-loading="loading">
    <aside class="settings-nav" aria-label="设置分类">
      <button v-for="section in sections" :key="section.key" type="button" :aria-label="section.title" :class="{ active: active === section.key }" :aria-current="active === section.key ? 'page' : undefined" @click="router.replace({ query: { ...route.query, section: section.key } })"><el-icon :size="18"><component :is="section.icon" /></el-icon>{{ section.title }}<i v-if="changedIn(section.key)" aria-label="有未保存修改" /></button>
      <div v-if="system" class="system-info"><b>平台信息</b><span>可用磁盘 <em>{{ fmtSize(system.disk.free) }}</em></span><span>构建记录 <em>{{ system.jobs }}</em></span></div>
    </aside>
    <el-card class="settings-panel">
        <template #header>
            <div class="page-card-header">
              <div class="page-card-header-content"><div class="settings-heading"><div><h2>{{ selected.title }}</h2><p>{{ selected.description }}</p></div><el-button v-if="active === 'registry'" :icon="Box" :loading="testing.busy.value" :disabled="loading || !view" @click="testRegistry">测试仓库连接</el-button></div></div>
              <div class="page-card-actions">
                <el-button :icon="Refresh" :disabled="saving.busy.value" @click="discard">重新加载</el-button>
              </div>
            </div>
          </template>
        <el-alert v-if="active === 'registry' || active === 'build'" title="请在没有进行中的构建任务时修改仓库和构建参数。" type="info" show-icon :closable="false" class="settings-notice" />
        <el-alert v-if="active === 'security'" title="登录有效期的修改不会缩短已经存在的会话。" type="info" show-icon :closable="false" class="settings-notice" />
        <el-form label-position="top" :disabled="saving.busy.value" @submit.prevent="save">
          <section v-for="group in groups" :key="group.title" class="settings-group"><h3>{{ group.title }}</h3><div class="form-grid"><SettingInput v-for="field in group.fields" :key="field.path" :field="field" v-model="values[field.path]" v-model:clear="clears[field.path]" :class="{ wide: field.type === 'list' || field.path === 'general.site_name' }" /></div></section>
          <el-empty v-if="!loading && !view" description="设置加载失败，请重新加载。" />
          <div class="settings-save"><span :class="{ changed: dirty }">{{ !view ? '尚未加载设置' : dirty ? `有 ${dirtyPaths.length} 项未保存修改` : '所有修改已保存' }}</span><div class="toolbar"><el-button :disabled="!dirty || saving.busy.value" @click="discard">放弃修改</el-button><el-button type="primary" native-type="submit" :loading="saving.busy.value" :disabled="!view || !dirty">保存修改</el-button></div></div>
        </el-form>
    </el-card>
    <p class="settings-footnote">设置优先级：环境变量 &gt; 页面设置 &gt; 配置文件。敏感值不会回显，留空保留原值。
      Kubernetes 集群与命名空间在 <RouterLink :to="{ name: 'kubernetes' }">平台 › 集群与命名空间</RouterLink> 中管理。</p>
  </section>
</template>

<style scoped>
.settings-page { display: grid; grid-template-columns: 210px minmax(0, 1fr); gap: 22px; align-items: start; }
.settings-page > :not(.settings-nav):not(.settings-footnote) { grid-column: 2; min-width: 0; }
.settings-page > .settings-footnote { grid-column: 1 / -1; }
.settings-nav { background: var(--hi-bg-card); border: 1px solid var(--hi-border); border-radius: 10px; padding: 12px; }
.settings-nav button { display: flex; align-items: center; gap: 10px; width: 100%; padding: 13px 12px; margin-bottom: 3px; border: 0; background: none; border-radius: 6px; color: var(--hi-text-2); cursor: pointer; font-size: 13px; text-align: left; }
.settings-nav button:hover { background: var(--hi-bg-soft); }
.settings-nav button.active { background: var(--hi-sider-active-bg); color: var(--el-color-primary); font-weight: 600; }
.settings-nav i:not(.el-icon) { width: 5px; height: 5px; background: var(--el-color-warning); border-radius: 50%; margin-left: auto; }
.system-info { display: grid; gap: 12px; border-top: 1px solid var(--hi-border-light); padding: 20px 10px 8px; margin-top: 20px; font-size: 11px; color: var(--hi-text-3); }
.system-info b { color: var(--hi-text-2); font-size: 12px; font-weight: 500; }
.system-info span { display: flex; justify-content: space-between; }
.system-info em { font-style: normal; color: var(--hi-text-2); }
.settings-panel { min-width: 0; }
.settings-heading { display: flex; justify-content: space-between; align-items: center; gap: 12px; flex-wrap: wrap; }
.settings-heading h2 { font-size: 17px; margin: 0; }
.settings-heading p { font-size: 12px; color: var(--hi-text-3); margin: 6px 0 0; }
.settings-notice { margin-bottom: 24px; }
.settings-group + .settings-group { border-top: 1px solid var(--hi-border-light); padding-top: 22px; }
.settings-group h3 { font-size: 13px; margin: 0 0 20px; font-weight: 600; }
.settings-group .form-grid { column-gap: 28px; }
.settings-save { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 12px; padding-top: 20px; border-top: 1px solid var(--hi-border); }
.settings-save > span { font-size: 12px; color: var(--hi-text-3); }
.settings-save > span.changed { color: var(--el-color-warning); }
.settings-footnote { text-align: center; font-size: 11px; color: var(--hi-text-3); margin-top: 22px; }
@media (max-width: 1000px) {
  .settings-page { grid-template-columns: minmax(0, 1fr); }
  .settings-page > :not(.settings-nav):not(.settings-footnote) { grid-column: 1; }
  .settings-nav { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 4px; }
  .settings-nav button { justify-content: flex-start; white-space: normal; width: 100%; min-width: 0; margin: 0; }
  .system-info { display: none; }
}
</style>
