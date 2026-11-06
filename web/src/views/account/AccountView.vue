<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { accountApi } from '@/api'
import type { ApiToken, SessionRow } from '@/api/types'
import OneTimeSecret from '@/components/OneTimeSecret.vue'
import PasswordForm from '@/components/PasswordForm.vue'
import { confirmAction, notifyError, useAction } from '@/composables/useAction'
import { useSession } from '@/stores/session'
import { fmtTime } from '@/utils/format'

const session = useSession()
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

onMounted(load)
</script>

<template>
  <div class="page">
    <div class="page-heading">
      <div>
        <h2>我的账号</h2>
        <div class="hint">{{ session.profile?.username }} · {{ session.profile?.role_label }}</div>
      </div>
    </div>
    <div class="grid-2">
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

    <el-card class="section-gap">
      <template #header>
        <div class="card-title">
          <span>API 令牌</span>
          <el-button @click="tokenDialog.visible = true">创建令牌</el-button>
        </div>
      </template>
      <p class="hint">用于自动化调用，请求头为 Authorization: Bearer &lt;token&gt;。令牌继承账号权限；修改密码会撤销全部令牌。</p>
      <el-table :data="tokens" empty-text="暂无令牌">
        <el-table-column prop="name" label="名称" />
        <el-table-column label="前缀"><template #default="{ row }"><span class="mono">{{ row.prefix }}…</span></template></el-table-column>
        <el-table-column label="到期时间"><template #default="{ row }">{{ fmtTime(row.expires_at) }}</template></el-table-column>
        <el-table-column label="最近使用"><template #default="{ row }">{{ fmtTime(row.last_used_at) }}</template></el-table-column>
        <el-table-column label="操作" width="100">
          <template #default="{ row }"><el-button link type="danger" @click="revokeToken(row)">撤销</el-button></template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-card class="section-gap">
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
</style>
