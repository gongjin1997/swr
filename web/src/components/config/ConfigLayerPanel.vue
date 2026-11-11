<script setup lang="ts">
/** 某一层（平台、项目、环境）的全部配置：环境变量与敏感变量每层一个，配置文件与敏感文件按名称可以有多个。 */
import { Plus } from '@element-plus/icons-vue'
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { configApi } from '@/api'
import type { ConfigKind, ConfigScope, ConfigSet } from '@/api/types'
import { notifyError, useAction } from '@/composables/useAction'
import { CONFIG_KIND_HINT, CONFIG_KIND_TEXT } from '@/constants'
import { useSession } from '@/stores/session'
import ConfigSetEditor from './ConfigSetEditor.vue'

const props = defineProps<{ scopeType: ConfigScope; scopeId: number }>()
const emit = defineEmits<{ changed: [] }>()
const session = useSession()
const sets = ref<ConfigSet[]>([])
const loading = ref(false)
const { busy, run } = useAction()
const creator = reactive({ visible: false, kind: 'files' as ConfigKind, name: '', description: '' })
const KINDS: ConfigKind[] = ['env', 'secret_env', 'files', 'secret_files']
const SINGLETON: ConfigKind[] = ['env', 'secret_env']
const DEFAULT_NAMES: Partial<Record<ConfigKind, string>> = {
  files: 'shared-config', secret_files: 'trust-store',
}

const canEdit = computed(() => {
  if (props.scopeType === 'platform') return session.canGlobal('config.manage')
  if (props.scopeType === 'project') return session.canProject(props.scopeId, 'config.manage')
  if (props.scopeType === 'environment') return session.canNamespace(props.scopeId, 'environment.config')
  return false
})
const byKind = (kind: ConfigKind) => sets.value.filter((s) => s.kind === kind)
const singleton = (kind: ConfigKind) => SINGLETON.includes(kind)

async function load() {
  loading.value = true
  try {
    sets.value = (await configApi.list(props.scopeType, props.scopeId)).items
  } catch (e) {
    sets.value = []
    notifyError(e)
  } finally {
    loading.value = false
  }
}

async function create() {
  const ok = await run(async () => {
    const created = await configApi.create({
      scope_type: props.scopeType, scope_id: props.scopeId, kind: creator.kind, entries: [],
      name: singleton(creator.kind) ? undefined : creator.name.trim(), description: creator.description.trim(),
    })
    sets.value = [...sets.value, created]
    emit('changed')
  }, '已创建，点击“编辑”添加条目')
  if (ok) creator.visible = false
}

function openCreator(kind: ConfigKind) {
  const taken = byKind(kind).map((s) => s.name)
  const suggested = DEFAULT_NAMES[kind] ?? ''
  Object.assign(creator, { kind, name: suggested && !taken.includes(suggested) ? suggested : '', description: '' })
  if (singleton(kind)) void create()
  else creator.visible = true
}

function replace(updated: ConfigSet) {
  sets.value = sets.value.map((s) => (s.id === updated.id ? updated : s))
  emit('changed')
}

function drop(id: number) {
  sets.value = sets.value.filter((s) => s.id !== id)
  emit('changed')
}

watch(() => `${props.scopeType}:${props.scopeId}`, load)
onMounted(load)
defineExpose({ reload: load })
</script>

<template>
  <div v-loading="loading">
    <section v-for="kind in KINDS" :key="kind" class="layer-section">
      <div class="section-title">
        <h3>{{ CONFIG_KIND_TEXT[kind] }}</h3>
        <span class="hint">{{ CONFIG_KIND_HINT[kind] }}</span>
        <el-button v-if="canEdit && (!singleton(kind) || !byKind(kind).length)" size="small" :icon="Plus" :loading="busy"
          @click="openCreator(kind)">{{ singleton(kind) ? '创建' : '新建' }}{{ CONFIG_KIND_TEXT[kind] }}</el-button>
      </div>
      <ConfigSetEditor v-for="item in byKind(kind)" :key="item.id" :set="item" @saved="replace" @removed="drop(item.id)" />
      <p v-if="!byKind(kind).length" class="hint empty">本层没有{{ CONFIG_KIND_TEXT[kind] }}</p>
    </section>
  </div>

  <el-dialog v-model="creator.visible" :title="`新建${CONFIG_KIND_TEXT[creator.kind]}`" width="540px" :close-on-click-modal="false">
    <el-form label-position="top">
      <el-form-item label="名称（会作为 ConfigMap / Secret 的名称）">
        <el-input v-model="creator.name" placeholder="小写字母、数字、-、.，例如 shared-config" />
      </el-form-item>
      <el-form-item label="说明"><el-input v-model="creator.description" maxlength="300" /></el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="creator.visible = false">取消</el-button>
      <el-button type="primary" :loading="busy" :disabled="!creator.name.trim()" @click="create">创建</el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.layer-section + .layer-section { margin-top: 18px; }
.section-title { display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap; margin-bottom: 8px; }
.section-title h3 { margin: 0; font-size: 15px; }
.section-title .el-button { margin-left: auto; }
.empty { margin: 4px 0 0; }
</style>
