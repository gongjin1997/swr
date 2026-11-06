<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { bindingApi, namespaceApi, projectApi } from '@/api'
import type { BindableUser, Binding, Namespace, Project } from '@/api/types'
import { confirmAction, notifyError, useAction } from '@/composables/useAction'
import { ROLE_TEXT, SCOPE_ROLES } from '@/constants'
import { useSession } from '@/stores/session'
import { fmtTime } from '@/utils/format'

const session = useSession()
const bindings = ref<Binding[]>([])
const users = ref<BindableUser[]>([])
const projects = ref<Project[]>([])
const namespaces = ref<Namespace[]>([])
const loading = ref(false)
const { busy, run } = useAction()
const form = reactive({ visible: false, userId: null as number | null, scope: 'project' as Binding['scope_type'],
  targetId: null as number | null, role: '', expires: null as string | null, note: '' })

const scopes = computed(() => {
  const list: [Binding['scope_type'], string][] = []
  if (session.can('user.manage')) list.push(['global', '全局'])
  if (session.can('project.member.manage')) list.push(['project', '项目'])
  if (session.can('namespace.member.manage')) list.push(['namespace', '命名空间'])
  return list
})
const targets = computed(() =>
  form.scope === 'project'
    ? projects.value.map((p) => ({ id: p.id, label: p.name }))
    : form.scope === 'namespace'
      ? namespaces.value.map((n) => ({ id: n.id, label: `${n.cluster_name} / ${n.name}` }))
      : [],
)
const candidates = computed(() => users.value.filter((u) => u.enabled && u.id !== session.profile?.id))

watch(() => form.scope, (scope) => {
  form.role = SCOPE_ROLES[scope][0][0]
  form.targetId = targets.value[0]?.id ?? null
})

async function load() {
  loading.value = true
  try {
    const empty = { items: [], total: 0 }
    const [b, u, p, n] = await Promise.all([
      bindingApi.list(), bindingApi.users(), projectApi.list().catch(() => empty), namespaceApi.list().catch(() => empty),
    ])
    bindings.value = b.items
    users.value = u.items
    projects.value = p.items as Project[]
    namespaces.value = n.items as Namespace[]
  } catch (e) {
    notifyError(e)
  } finally {
    loading.value = false
  }
}

function userLabel(id: number) {
  const u = users.value.find((x) => x.id === id)
  return u ? `${u.display_name}（${u.username}）` : `#${id}`
}

function scopeLabel(b: Binding) {
  if (b.scope_type === 'global') return '全局'
  if (b.scope_type === 'project') return `项目：${projects.value.find((p) => p.id === b.scope_id)?.name ?? '#' + b.scope_id}`
  const n = namespaces.value.find((x) => x.id === b.scope_id)
  return `命名空间：${n ? `${n.cluster_name} / ${n.name}` : '#' + b.scope_id}`
}

function open() {
  if (!candidates.value.length) return notifyError(new Error('没有可授权的其他启用用户'))
  const scope = scopes.value.find(([s]) => s === 'project')?.[0] ?? scopes.value[0]?.[0] ?? 'project'
  Object.assign(form, { visible: true, userId: candidates.value[0].id, scope, expires: null, note: '' })
  form.role = SCOPE_ROLES[scope][0][0]
  form.targetId = targets.value[0]?.id ?? null
}

async function save() {
  if (!form.userId) return notifyError(new Error('请选择用户'))
  if (form.scope !== 'global' && !form.targetId) return notifyError(new Error('请选择授权的资源'))
  const ok = await run(() => bindingApi.create({
    subject_type: 'user', subject_id: form.userId!, scope_type: form.scope,
    scope_id: form.scope === 'global' ? 0 : form.targetId!, role: form.role, expires_at: form.expires, note: form.note.trim(),
  }), '授权已创建')
  if (ok) {
    form.visible = false
    await load()
  }
}

async function revoke(b: Binding) {
  if (!(await confirmAction(`撤销 ${userLabel(b.subject_id)} 的“${ROLE_TEXT[b.role] ?? b.role}”授权？`, '撤销授权', true))) return
  if (await run(() => bindingApi.remove(b.id), '授权已撤销')) await load()
}

const disabledPast = (date: Date) => date.getTime() < Date.now() - 86400000

onMounted(load)
</script>

<template>
  <el-card>
    <template #header><div class="card-title"><span>成员授权</span><el-button type="primary" @click="open">新增授权</el-button></div></template>
    <el-table v-loading="loading" :data="bindings" empty-text="暂无授权记录">
      <el-table-column label="用户" min-width="180"><template #default="{ row }">{{ userLabel(row.subject_id) }}</template></el-table-column>
      <el-table-column label="角色" width="140"><template #default="{ row }">{{ ROLE_TEXT[row.role] ?? row.role }}</template></el-table-column>
      <el-table-column label="范围" min-width="200"><template #default="{ row }">{{ scopeLabel(row) }}</template></el-table-column>
      <el-table-column label="过期时间" width="170"><template #default="{ row }">{{ row.expires_at ? fmtTime(row.expires_at) : '永不过期' }}</template></el-table-column>
      <el-table-column prop="note" label="说明" min-width="160" />
      <el-table-column label="操作" width="90"><template #default="{ row }"><el-button link type="danger" @click="revoke(row)">撤销</el-button></template></el-table-column>
    </el-table>
  </el-card>

  <el-dialog v-model="form.visible" title="新增成员授权" width="560px">
    <el-form label-position="top">
      <el-form-item label="用户">
        <el-select v-model="form.userId" filterable>
          <el-option v-for="u in candidates" :key="u.id" :label="`${u.display_name} · ${u.username}`" :value="u.id" />
        </el-select>
      </el-form-item>
      <el-form-item label="范围">
        <el-radio-group v-model="form.scope">
          <el-radio-button v-for="[value, label] in scopes" :key="value" :value="value">{{ label }}</el-radio-button>
        </el-radio-group>
      </el-form-item>
      <el-form-item v-if="form.scope !== 'global'" label="资源">
        <el-select v-model="form.targetId" filterable>
          <el-option v-for="t in targets" :key="t.id" :label="t.label" :value="t.id" />
        </el-select>
      </el-form-item>
      <el-form-item label="角色">
        <el-select v-model="form.role">
          <el-option v-for="[value, label] in SCOPE_ROLES[form.scope]" :key="value" :label="label" :value="value" />
        </el-select>
      </el-form-item>
      <el-form-item label="过期时间（可选）">
        <el-date-picker v-model="form.expires" type="datetime" value-format="YYYY-MM-DD[T]HH:mm:ssZ" :disabled-date="disabledPast" clearable placeholder="不设置则永不过期" />
      </el-form-item>
      <el-form-item label="授权说明"><el-input v-model="form.note" maxlength="200" /></el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="form.visible = false">取消</el-button>
      <el-button type="primary" :loading="busy" @click="save">授予授权</el-button>
    </template>
  </el-dialog>
</template>
