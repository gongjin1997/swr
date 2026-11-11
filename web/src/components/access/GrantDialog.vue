<script setup lang="ts">
/**
 * 新增授权。范围、资源与角色只展示当前账号可以授予的组合（来自 /api/bindings/options），
 * 规则与后端 services/grants.py 一致，最终由后端校验。
 * 可固定授权对象（账号详情）或固定范围（项目/命名空间成员）。
 */
import { computed, reactive, ref, watch } from 'vue'
import { bindingApi } from '@/api'
import type { BindableUser, GrantOptions, RoleDefinition, ScopeType } from '@/api/types'
import { notifyError, useAction } from '@/composables/useAction'
import { SCOPE_TEXT, TIER_TEXT } from '@/constants'
import { useCatalog } from '@/stores/catalog'
import { useSession } from '@/stores/session'

const props = defineProps<{ subjectId?: number; subjectLabel?: string; scopeType?: ScopeType; scopeId?: number }>()
const visible = defineModel<boolean>({ required: true })
const emit = defineEmits<{ granted: [] }>()

const session = useSession()
const catalog = useCatalog()
const options = ref<GrantOptions | null>(null)
const users = ref<BindableUser[]>([])
const loading = ref(false)
const { busy, run } = useAction()
const form = reactive({ userId: null as number | null, scope: 'project' as ScopeType, targetId: null as number | null,
  role: '', expires: null as string | null, note: '' })

interface Target { id: number; label: string; roles: string[] }

const targetsByScope = computed<Record<ScopeType, Target[]>>(() => ({
  global: options.value?.global ? [{ id: 0, label: '全局（所有项目与命名空间）', roles: options.value.global.roles }] : [],
  project: (options.value?.project ?? []).map((p) => ({ id: p.id, label: p.name, roles: p.roles })),
  namespace: (options.value?.namespace ?? []).map((n) => ({ id: n.id, label: `${n.cluster} / ${n.name}（${TIER_TEXT[n.tier] ?? n.tier}）`, roles: n.roles })),
}))
const scopes = computed(() => (['project', 'namespace', 'global'] as ScopeType[]).filter((s) =>
  (!props.scopeType || s === props.scopeType) && targetsByScope.value[s].some((t) => props.scopeId == null || t.id === props.scopeId)))
const targets = computed(() => targetsByScope.value[form.scope].filter((t) => props.scopeId == null || t.id === props.scopeId))
const target = computed(() => targets.value.find((t) => t.id === form.targetId) ?? null)
const roles = computed(() => (target.value?.roles ?? []).map((code) => catalog.roleMap.get(code))
  .filter((r): r is RoleDefinition => !!r))
const role = computed(() => catalog.roleMap.get(form.role) ?? null)
const candidates = computed(() => users.value.filter((u) => u.id !== session.userId))
const unavailable = computed(() => !loading.value && options.value !== null && !scopes.value.length)

watch(() => form.scope, () => {
  form.targetId = targets.value[0]?.id ?? null
})
watch(() => form.targetId, () => {
  if (!roles.value.some((r) => r.code === form.role)) form.role = roles.value[0]?.code ?? ''
})

watch(visible, async (open) => {
  if (!open) return
  loading.value = true
  options.value = null
  try {
    const [opts, list] = await Promise.all([
      bindingApi.options(), props.subjectId ? Promise.resolve(null) : bindingApi.users(), catalog.load(),
    ])
    options.value = opts
    if (list) users.value = list.items
    Object.assign(form, { userId: props.subjectId ?? candidates.value[0]?.id ?? null, expires: null, note: '' })
    form.scope = scopes.value[0] ?? 'project'
    form.targetId = targets.value[0]?.id ?? null
    form.role = roles.value[0]?.code ?? ''
  } catch (e) {
    notifyError(e)
  } finally {
    loading.value = false
  }
})

const disabledPast = (date: Date) => date.getTime() < Date.now() - 86400000

async function save() {
  if (!form.userId) return notifyError(new Error('请选择账号'))
  if (form.targetId === null || !form.role) return notifyError(new Error('请选择授权范围与角色'))
  const ok = await run(() => bindingApi.create(form.userId as number, {
    role: form.role, scope_type: form.scope, scope_id: form.targetId as number, expires_at: form.expires, note: form.note.trim(),
  }), '授权已添加')
  if (ok) {
    visible.value = false
    emit('granted')
  }
}
</script>

<template>
  <!-- 会在账号详情、命名空间成员等抽屉中打开，嵌套时必须挂到 body 上 -->
  <el-dialog v-model="visible" title="新增授权" width="580px" :close-on-click-modal="false" append-to-body>
    <div v-loading="loading">
      <el-empty v-if="unavailable" description="当前账号没有可以授予的范围或角色。" :image-size="80" />
      <el-form v-else label-position="top">
        <el-form-item label="账号">
          <el-input v-if="subjectId" :model-value="subjectLabel ?? `#${subjectId}`" disabled />
          <el-select v-else v-model="form.userId" filterable placeholder="选择要授权的账号">
            <el-option v-for="u in candidates" :key="u.id" :label="`${u.display_name}（${u.username}）`" :value="u.id" />
          </el-select>
        </el-form-item>
        <el-form-item v-if="scopes.length > 1" label="授权范围">
          <el-radio-group v-model="form.scope">
            <el-radio-button v-for="s in scopes" :key="s" :value="s">{{ SCOPE_TEXT[s] }}</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <el-form-item v-if="form.scope !== 'global'" :label="SCOPE_TEXT[form.scope]">
          <el-select v-model="form.targetId" filterable :disabled="scopeId != null">
            <el-option v-for="t in targets" :key="t.id" :label="t.label" :value="t.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="角色">
          <el-select v-model="form.role" placeholder="选择角色">
            <el-option v-for="r in roles" :key="r.code" :label="r.name" :value="r.code" />
          </el-select>
          <div v-if="role" class="hint">{{ role.description || role.permissions.map(catalog.permissionLabel).join('、') }}</div>
        </el-form-item>
        <el-form-item label="过期时间（可选）">
          <el-date-picker v-model="form.expires" type="datetime" value-format="YYYY-MM-DD[T]HH:mm:ssZ"
            :disabled-date="disabledPast" clearable placeholder="不设置则长期有效" />
        </el-form-item>
        <el-form-item label="说明"><el-input v-model="form.note" maxlength="200" placeholder="如：支持 11 月版本发布" /></el-form-item>
      </el-form>
    </div>
    <template #footer>
      <el-button @click="visible = false">取消</el-button>
      <el-button type="primary" :loading="busy" :disabled="unavailable || loading" @click="save">授予</el-button>
    </template>
  </el-dialog>
</template>
