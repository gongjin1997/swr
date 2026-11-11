<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { accountApi } from '@/api'
import type { ApiToken, Binding, SessionRow } from '@/api/types'
import PermissionTags from '@/components/access/PermissionTags.vue'
import OneTimeSecret from '@/components/OneTimeSecret.vue'
import PasswordForm from '@/components/PasswordForm.vue'
import { confirmAction, notifyError, useAction } from '@/composables/useAction'
import { SCOPE_TEXT, TIER_TEXT } from '@/constants'
import { useCatalog } from '@/stores/catalog'
import { useSession } from '@/stores/session'
import { fmtTime } from '@/utils/format'

const tab = ref('profile')
const session = useSession()
const catalog = useCatalog()
const grants = computed(() => session.profile?.bindings ?? [])
const scopedProjects = computed(() => (session.profile?.projects ?? []).filter((p) => p.perms.length))
const scopedNamespaces = computed(() => (session.profile?.namespaces ?? []).filter((n) => n.perms.length))

function scopeLabel(binding: Binding): string {
  if (binding.scope_type === 'global') return '所有项目与命名空间'
  if (binding.scope_type === 'project') return session.projectName(binding.scope_id)
  const ns = session.profile?.namespaces.find((n) => n.id === binding.scope_id)
  return ns ? `${ns.cluster} / ${ns.name}` : `命名空间 #${binding.scope_id}`
}
const profile = reactive({ display_name: session.profile?.display_name ?? '', email: session.profile?.email ?? '' })
const sessions = ref<SessionRow[]>([])
const tokens = ref<ApiToken[]>([])
const tokenDialog = reactive({ visible: false, name: '', days: 30 })
const secret = reactive({ visible: false, value: '' })
const profileAction = useAction()
const tokenAction = useAction()

async function load() {
  try {
    const [s, t] = await Promise.all([accountApi.sessions(), accountApi.tokens()])
    sessions.value = s.items
    tokens.value = t.items
  } catch (e) {
    notifyError(e)
  }
}

async function saveProfile() {
  await profileAction.run(async () => {
    await accountApi.updateProfile(profile.display_name, profile.email)
    await session.load()
  }, '资料已保存')
}

async function createToken() {
  if (!tokenDialog.name.trim()) return notifyError(new Error('请填写令牌名称'))
  await tokenAction.run(async () => {
    const created = await accountApi.createToken(tokenDialog.name.trim(), tokenDialog.days)
    tokenDialog.visible = false
    tokenDialog.name = ''
    secret.value = created.token
    secret.visible = true
    await load()
  })
}

async function revokeToken(token: ApiToken) {
  if (!(await confirmAction(`确认撤销令牌“${token.name}”？使用它的自动化调用会立即失效。`, '撤销令牌', true))) return
  await tokenAction.run(async () => {
    await accountApi.revokeToken(token.id)
    await load()
  }, '令牌已撤销')
}

async function revokeSession(row: SessionRow) {
  await tokenAction.run(async () => {
    await accountApi.revokeSession(row.id)
    await load()
  }, '已退出该设备')
}

onMounted(() => {
  void load()
  catalog.load().catch(notifyError)
})
</script>

<template>
  <div class="page">
    <el-tabs v-model="tab" class="account-tabs">
      <el-tab-pane label="个人资料与安全" name="profile" /><el-tab-pane label="我的权限" name="access" />
      <el-tab-pane label="API 令牌" name="tokens" /><el-tab-pane label="登录设备" name="sessions" />
    </el-tabs>
    <div v-show="tab === 'profile'" class="grid-2">
      <el-card>
        <template #header><span class="card-title">基本资料</span></template>
        <el-form label-position="top" @submit.prevent="saveProfile">
          <el-form-item label="显示名称"><el-input v-model="profile.display_name" maxlength="80" /></el-form-item>
          <el-form-item label="邮箱"><el-input v-model="profile.email" type="email" maxlength="254" /></el-form-item>
          <el-button type="primary" native-type="submit" :loading="profileAction.busy.value">保存资料</el-button>
        </el-form>
      </el-card>
      <el-card>
        <template #header><span class="card-title">修改密码</span></template>
        <PasswordForm @changed="load" />
      </el-card>
    </div>

    <div v-show="tab === 'access'" class="access-panel">
      <el-alert v-if="session.isPlatformAdmin" type="success" :closable="false" show-icon title="你是平台管理员，拥有全部权限。" />
      <el-card>
        <template #header><span class="card-title">我的授权</span></template>
        <p class="hint">权限只来自授权：在全局、项目或命名空间范围被授予的角色。需要调整请联系平台管理员或对应项目、命名空间的管理员。</p>
        <el-table :data="grants" empty-text="还没有任何授权">
          <el-table-column label="角色" min-width="160"><template #default="{ row }">{{ catalog.roleName(row.role) }}</template></el-table-column>
          <el-table-column label="范围" min-width="240">
            <template #default="{ row }"><span class="hint">{{ SCOPE_TEXT[row.scope_type as keyof typeof SCOPE_TEXT] }}</span> {{ scopeLabel(row) }}</template>
          </el-table-column>
          <el-table-column label="有效期" min-width="170"><template #default="{ row }">{{ row.expires_at ? `至 ${fmtTime(row.expires_at)}` : '长期' }}</template></el-table-column>
          <el-table-column prop="note" label="说明" min-width="140" show-overflow-tooltip />
        </el-table>
      </el-card>
      <el-card v-if="!session.isPlatformAdmin">
        <template #header><span class="card-title">有效权限</span></template>
        <el-collapse>
          <el-collapse-item v-if="session.profile?.global_permissions.length" :title="`全局（${session.profile.global_permissions.length} 项权限）`" name="global">
            <PermissionTags :permissions="session.profile.global_permissions" />
          </el-collapse-item>
          <el-collapse-item v-for="p in scopedProjects" :key="`p${p.id}`" :title="`项目：${p.name}`" :name="`p${p.id}`">
            <PermissionTags :permissions="p.perms" />
          </el-collapse-item>
          <el-collapse-item v-for="n in scopedNamespaces" :key="`n${n.id}`" :title="`命名空间：${n.cluster} / ${n.name}（${TIER_TEXT[n.tier] ?? n.tier}）`" :name="`n${n.id}`">
            <PermissionTags :permissions="n.perms" />
          </el-collapse-item>
        </el-collapse>
        <el-empty v-if="!session.profile?.permissions.length" description="没有任何权限，只能修改自己的资料和密码。" :image-size="70" />
      </el-card>
    </div>

    <el-card v-show="tab === 'tokens'" class="account-panel">
      <template #header>
        <div class="card-title">
          <span>API 令牌</span>
          <el-button @click="tokenDialog.visible = true">创建令牌</el-button>
        </div>
      </template>
      <p class="hint">用于自动化调用，请求头为 Authorization: Bearer &lt;token&gt;。令牌继承账号权限；修改密码会撤销全部令牌。</p>
      <el-table :data="tokens" empty-text="暂无令牌">
        <el-table-column prop="name" label="名称" min-width="140" />
        <el-table-column label="前缀" min-width="140"><template #default="{ row }"><span class="mono">{{ row.prefix }}…</span></template></el-table-column>
        <el-table-column label="到期时间" min-width="170"><template #default="{ row }">{{ fmtTime(row.expires_at) }}</template></el-table-column>
        <el-table-column label="最近使用" min-width="170"><template #default="{ row }">{{ fmtTime(row.last_used_at) }}</template></el-table-column>
        <el-table-column label="操作" width="100">
          <template #default="{ row }"><el-button link type="danger" @click="revokeToken(row)">撤销</el-button></template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-card v-show="tab === 'sessions'" class="account-panel">
      <template #header><span class="card-title">登录设备</span></template>
      <el-table :data="sessions">
        <el-table-column label="设备" min-width="260">
          <template #default="{ row }">
            <span class="break-word">{{ row.user_agent }}</span>
            <el-tag v-if="row.current" size="small" type="success" class="current">当前设备</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="ip" label="IP" width="140" />
        <el-table-column label="最近活跃" width="180"><template #default="{ row }">{{ fmtTime(row.last_seen_at) }}</template></el-table-column>
        <el-table-column label="过期时间" width="180"><template #default="{ row }">{{ fmtTime(row.expires_at) }}</template></el-table-column>
        <el-table-column label="操作" width="110">
          <template #default="{ row }">
            <span v-if="row.current">-</span>
            <el-button v-else link type="danger" @click="revokeSession(row)">退出登录</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-dialog v-model="tokenDialog.visible" title="创建 API 令牌" width="420px">
      <el-form label-position="top" @submit.prevent="createToken">
        <el-form-item label="名称"><el-input v-model="tokenDialog.name" maxlength="80" /></el-form-item>
        <el-form-item label="有效期（天）"><el-input-number v-model="tokenDialog.days" :min="1" :max="365" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="tokenDialog.visible = false">取消</el-button>
        <el-button type="primary" :loading="tokenAction.busy.value" @click="createToken">创建</el-button>
      </template>
    </el-dialog>
    <OneTimeSecret v-model="secret.visible" title="API 令牌" :secret="secret.value" />
  </div>
</template>

<style scoped>
.current {
  margin-left: 8px;
}
.account-panel { margin-top: 0 !important; }
.access-panel { display: grid; gap: 16px; }
.access-panel .hint { margin: 0 0 12px; }
.account-tabs { margin-bottom: 16px; }
</style>
