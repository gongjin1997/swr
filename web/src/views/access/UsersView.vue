<script setup lang="ts">
import { Plus, Refresh, Search } from '@element-plus/icons-vue'
import { computed, onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { bindingApi, userApi } from '@/api'
import type { Account, GrantOptions, ScopeType } from '@/api/types'
import OneTimeSecret from '@/components/OneTimeSecret.vue'
import { confirmAction, notifyError, useAction } from '@/composables/useAction'
import { SCOPE_TEXT, TIER_TEXT } from '@/constants'
import { useCatalog } from '@/stores/catalog'
import { useSession } from '@/stores/session'
import { fmtTime } from '@/utils/format'
import UserDrawer from './UserDrawer.vue'
import TablePager from '@/components/TablePager.vue'
import { usePagination } from '@/composables/usePagination'

const session = useSession()
const catalog = useCatalog()
const route = useRoute()
const router = useRouter()
const users = ref<Account[]>([])
const loading = ref(false)
const filters = reactive({ keyword: '', status: '', kind: '' })
const { busy, run } = useAction()
const secret = reactive({ visible: false, title: '', value: '' })

const filtered = computed(() => users.value.filter((u) => {
  const keyword = filters.keyword.trim().toLowerCase()
  if (keyword && !`${u.username} ${u.display_name} ${u.email}`.toLowerCase().includes(keyword)) return false
  if (filters.status === 'enabled' && !u.enabled) return false
  if (filters.status === 'disabled' && u.enabled) return false
  if (filters.status === 'password' && !u.must_change_password) return false
  if (filters.kind === 'admin' && !u.is_platform_admin) return false
  if (filters.kind === 'global' && !u.global_roles.length) return false
  if (filters.kind === 'none' && u.grant_count) return false
  return true
}))
const pager = usePagination(() => filtered.value, { resetOn: () => [filters.keyword, filters.status, filters.kind] })
const detailId = computed<number | null>(() => (route.query.user ? Number(route.query.user) : null))

async function load() {
  loading.value = true
  try {
    const [list] = await Promise.all([userApi.list(), catalog.load()])
    users.value = list.items
  } catch (e) {
    notifyError(e)
  } finally {
    loading.value = false
  }
}

function openDetail(user: Account | null) {
  void router.replace({ query: { ...route.query, user: user ? String(user.id) : undefined } })
}

// ------------------------------------------------------------------ 新建 / 编辑
interface GrantRow { scope: ScopeType; targetId: number | null; role: string }
const grantOptions = ref<GrantOptions | null>(null)
const creator = reactive({ visible: false, username: '', display_name: '', email: '', password: '', platform_admin: false,
  generated: false, grants: [] as GrantRow[] })
const editor = reactive({ visible: false, user: null as Account | null, display_name: '', email: '', enabled: true,
  platform_admin: false })

function targetsOf(scope: ScopeType) {
  const o = grantOptions.value
  if (!o) return []
  if (scope === 'global') return o.global ? [{ id: 0, label: '全局', roles: o.global.roles }] : []
  if (scope === 'project') return o.project.map((p) => ({ id: p.id, label: p.name, roles: p.roles }))
  return o.namespace.map((n) => ({ id: n.id, label: `${n.cluster} / ${n.name}（${TIER_TEXT[n.tier] ?? n.tier}）`, roles: n.roles }))
}
const grantScopes = computed(() => (['project', 'namespace', 'global'] as ScopeType[]).filter((s) => targetsOf(s).length))
function rolesOf(row: GrantRow) {
  return (targetsOf(row.scope).find((t) => t.id === row.targetId)?.roles ?? []).map((code) => ({ code, name: catalog.roleName(code) }))
}
function addGrant() {
  const scope = grantScopes.value[0]
  if (!scope) return notifyError(new Error('当前账号没有可以授予的范围'))
  const targetId = targetsOf(scope)[0]?.id ?? null
  const row: GrantRow = { scope, targetId, role: '' }
  row.role = rolesOf(row)[0]?.code ?? ''
  creator.grants.push(row)
}
function changeScope(row: GrantRow) {
  row.targetId = targetsOf(row.scope)[0]?.id ?? null
  changeTarget(row)
}
function changeTarget(row: GrantRow) {
  if (!rolesOf(row).some((r) => r.code === row.role)) row.role = rolesOf(row)[0]?.code ?? ''
}

/** 生成满足密码策略（同时含字母和数字）的随机密码，去掉易混淆字符。 */
function randomPassword(length = 16): string {
  const letters = 'abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ'
  const digits = '23456789'
  const random = (max: number) => crypto.getRandomValues(new Uint32Array(1))[0] % max
  const pick = (chars: string) => chars[random(chars.length)]
  const chars = [pick(letters), pick(digits), ...Array.from({ length: length - 2 }, () => pick(letters + digits))]
  for (let i = chars.length - 1; i > 0; i -= 1) {
    const j = random(i + 1)
    ;[chars[i], chars[j]] = [chars[j], chars[i]]
  }
  return chars.join('')
}

async function openCreator() {
  Object.assign(creator, { visible: true, username: '', display_name: '', email: '', password: '', platform_admin: false,
    generated: false, grants: [] })
  try {
    grantOptions.value = await bindingApi.options().catch(() => null)
  } catch (e) {
    notifyError(e)
  }
}

function generatePassword() {
  creator.password = randomPassword()
  creator.generated = true
}

async function create() {
  if (creator.grants.some((g) => g.targetId === null || !g.role)) return notifyError(new Error('请完善初始授权，或删除空白行'))
  const password = creator.password
  const ok = await run(async () => {
    await userApi.create({
      username: creator.username.trim(), display_name: creator.display_name.trim(), email: creator.email.trim(), password,
      platform_admin: creator.platform_admin,
      grants: creator.grants.map((g) => ({ role: g.role, scope_type: g.scope, scope_id: g.targetId as number })),
    })
  }, '账号已创建')
  if (!ok) return
  creator.visible = false
  if (creator.generated) Object.assign(secret, { visible: true, title: `初始密码：${creator.username.trim()}`, value: password })
  await load()
}

function openEditor(user: Account) {
  Object.assign(editor, { visible: true, user, display_name: user.display_name, email: user.email, enabled: user.enabled,
    platform_admin: user.is_platform_admin })
}

async function saveEdit() {
  const user = editor.user
  if (!user) return
  const ok = await run(() => userApi.update(user.id, {
    display_name: editor.display_name.trim(), email: editor.email.trim(), enabled: editor.enabled,
    ...(session.isPlatformAdmin ? { platform_admin: editor.platform_admin } : {}),
  }), '账号已保存')
  if (ok) {
    editor.visible = false
    await load()
  }
}

// ------------------------------------------------------------------ 其他操作
async function toggle(user: Account) {
  const action = user.enabled ? '停用' : '启用'
  const note = user.enabled ? '停用后该账号的会话与 API 令牌立即失效。' : ''
  if (!(await confirmAction(`${action}账号 ${user.username}？${note}`, `${action}账号`, user.enabled))) return
  // 只提交启用状态：带上列表里的 display_name/email 会把别人刚改过的资料覆盖回旧值
  if (await run(() => userApi.update(user.id, { enabled: !user.enabled }), `已${action}`)) await load()
}

async function resetPassword(user: Account) {
  if (!(await confirmAction(`重置 ${user.username} 的密码？该账号的会话和 API 令牌会被撤销，下次登录需修改密码。`, '重置密码', true))) return
  await run(async () => {
    const r = await userApi.resetPassword(user.id)
    Object.assign(secret, { visible: true, title: `临时密码：${user.username}`, value: r.password })
    await load()
  })
}

async function remove(user: Account) {
  if (!(await confirmAction(`删除账号 ${user.username}？其全部授权、会话和令牌会一并删除，操作不可撤销。`, '删除账号', true))) return
  if (await run(() => userApi.remove(user.id), '账号已删除')) await load()
}

const isSelf = (user: Account) => user.id === session.userId
onMounted(load)
</script>

<template>
  <el-card>
    <div class="filter-bar">
      <el-input v-model="filters.keyword" :prefix-icon="Search" placeholder="搜索账号、姓名或邮箱" clearable aria-label="搜索账号" />
      <el-select v-model="filters.status" placeholder="全部状态" clearable aria-label="按状态筛选">
        <el-option label="启用" value="enabled" /><el-option label="停用" value="disabled" /><el-option label="待改密" value="password" />
      </el-select>
      <el-select v-model="filters.kind" placeholder="全部身份" clearable aria-label="按身份筛选">
        <el-option label="平台管理员" value="admin" /><el-option label="持有全局角色" value="global" /><el-option label="未授权" value="none" />
      </el-select>
      <el-button :icon="Refresh" :loading="loading" @click="load">刷新</el-button>
      <span class="count">共 {{ filtered.length }} 个账号</span>
      <el-button type="primary" :icon="Plus" @click="openCreator">新增账号</el-button>
    </div>
    <el-table v-loading="loading" :data="pager.rows.value" empty-text="没有匹配的账号">
      <el-table-column label="账号" min-width="150">
        <template #default="{ row }">
          <el-button link type="primary" @click="openDetail(row)">{{ row.display_name }}</el-button>
          <div class="hint">{{ row.username }}<template v-if="row.email"> · {{ row.email }}</template></div>
        </template>
      </el-table-column>
      <el-table-column label="身份" min-width="140">
        <template #default="{ row }">
          <el-tag v-if="row.is_platform_admin" type="danger" effect="plain">平台管理员</el-tag>
          <span v-else :class="{ hint: !row.grant_count }">{{ row.role_label }}</span>
        </template>
      </el-table-column>
      <el-table-column label="授权数" width="78"><template #default="{ row }">{{ row.grant_count }}</template></el-table-column>
      <el-table-column label="状态" width="120">
        <template #default="{ row }">
          <el-tag :type="row.enabled ? 'success' : 'info'">{{ row.enabled ? '启用' : '停用' }}</el-tag>
          <el-tag v-if="row.must_change_password" type="warning" class="gap">待改密</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="最近登录" width="150"><template #default="{ row }">{{ fmtTime(row.last_login_at) }}</template></el-table-column>
      <el-table-column label="操作" width="220" fixed="right">
        <template #default="{ row }">
          <el-button link type="primary" @click="openDetail(row)">授权</el-button>
          <template v-if="row.can_manage">
            <el-button link type="primary" @click="openEditor(row)">编辑</el-button>
            <template v-if="!isSelf(row)">
              <el-button link type="primary" @click="resetPassword(row)">重置密码</el-button>
              <el-button link :type="row.enabled ? 'warning' : 'primary'" @click="toggle(row)">{{ row.enabled ? '停用' : '启用' }}</el-button>
              <el-button link type="danger" @click="remove(row)">删除</el-button>
            </template>
          </template>
          <el-tooltip v-else content="该账号的权限高于你，只有平台管理员可以管理"><span class="hint">受保护</span></el-tooltip>
        </template>
      </el-table-column>
    </el-table>
    <TablePager v-model:page="pager.page.value" v-model:size="pager.size.value" :total="pager.total.value" />
  </el-card>

  <el-dialog v-model="creator.visible" title="新增账号" width="680px" :close-on-click-modal="false">
    <el-form label-position="top" @submit.prevent="create">
      <div class="form-grid">
        <el-form-item label="用户名" required><el-input v-model="creator.username" maxlength="32" autocomplete="off" placeholder="2–32 位字母、数字、_ . @ -" /></el-form-item>
        <el-form-item label="显示名称"><el-input v-model="creator.display_name" maxlength="80" placeholder="默认同用户名" /></el-form-item>
        <el-form-item label="邮箱"><el-input v-model="creator.email" type="email" maxlength="254" /></el-form-item>
        <el-form-item label="初始密码" required>
          <el-input v-model="creator.password" type="password" show-password autocomplete="new-password" @input="creator.generated = false">
            <template #append><el-button @click="generatePassword">生成</el-button></template>
          </el-input>
          <div class="hint">首次登录必须修改；生成的密码会在创建后显示一次</div>
        </el-form-item>
      </div>
      <el-form-item v-if="session.isPlatformAdmin">
        <el-checkbox v-model="creator.platform_admin">设为平台管理员（拥有全部权限）</el-checkbox>
      </el-form-item>
      <el-form-item label="初始授权（可选）">
        <div class="grants">
          <p class="hint">新账号默认没有任何权限。可在这里直接授予项目或命名空间角色，之后也可在账号详情中调整。</p>
          <div v-for="(g, i) in creator.grants" :key="i" class="grant-row">
            <el-select v-model="g.scope" class="scope" @change="changeScope(g)">
              <el-option v-for="s in grantScopes" :key="s" :label="SCOPE_TEXT[s]" :value="s" />
            </el-select>
            <el-select v-if="g.scope !== 'global'" v-model="g.targetId" filterable class="target" @change="changeTarget(g)">
              <el-option v-for="t in targetsOf(g.scope)" :key="t.id" :label="t.label" :value="t.id" />
            </el-select>
            <el-select v-model="g.role" class="role">
              <el-option v-for="r in rolesOf(g)" :key="r.code" :label="r.name" :value="r.code" />
            </el-select>
            <el-button link type="danger" @click="creator.grants.splice(i, 1)">删除</el-button>
          </div>
          <el-button size="small" :icon="Plus" :disabled="!grantScopes.length" @click="addGrant">添加授权</el-button>
        </div>
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="creator.visible = false">取消</el-button>
      <el-button type="primary" :loading="busy" @click="create">创建账号</el-button>
    </template>
  </el-dialog>

  <el-dialog v-model="editor.visible" :title="`编辑账号 · ${editor.user?.username ?? ''}`" width="520px">
    <el-form label-position="top" @submit.prevent="saveEdit">
      <el-form-item label="显示名称"><el-input v-model="editor.display_name" maxlength="80" /></el-form-item>
      <el-form-item label="邮箱"><el-input v-model="editor.email" type="email" maxlength="254" /></el-form-item>
      <el-form-item><el-checkbox v-model="editor.enabled" :disabled="!!editor.user && isSelf(editor.user)">启用账号</el-checkbox></el-form-item>
      <el-form-item v-if="session.isPlatformAdmin">
        <el-checkbox v-model="editor.platform_admin" :disabled="!!editor.user && isSelf(editor.user)">平台管理员</el-checkbox>
        <div class="hint">修改平台管理员身份不会影响该账号的其他授权。</div>
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="editor.visible = false">取消</el-button>
      <el-button type="primary" :loading="busy" @click="saveEdit">保存</el-button>
    </template>
  </el-dialog>

  <UserDrawer :user-id="detailId" @close="openDetail(null)" @changed="load" />
  <OneTimeSecret v-model="secret.visible" :title="secret.title" :secret="secret.value" />
</template>

<style scoped>
.gap { margin-left: 6px; }
.grants { width: 100%; }
.grants > p { margin: 0 0 8px; }
.grant-row { display: flex; gap: 8px; align-items: center; margin-bottom: 8px; flex-wrap: wrap; }
.grant-row .scope { width: 110px; }
.grant-row .target { width: 220px; }
.grant-row .role { width: 170px; }
</style>
