<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { userApi } from '@/api'
import type { PublicUser, RolesInfo } from '@/api/types'
import OneTimeSecret from '@/components/OneTimeSecret.vue'
import { confirmAction, notifyError, useAction } from '@/composables/useAction'
import { useSession } from '@/stores/session'
import { fmtTime } from '@/utils/format'

const session = useSession()
const users = ref<PublicUser[]>([])
const roles = ref<RolesInfo | null>(null)
const loading = ref(false)
const { busy, run } = useAction()
const form = reactive({ visible: false, user: null as PublicUser | null, username: '', display_name: '', email: '',
  role: 'developer', enabled: true, password: '' })
const secret = reactive({ visible: false, title: '', value: '' })

async function load() {
  loading.value = true
  try {
    const [u, r] = await Promise.all([userApi.list(), userApi.roles()])
    users.value = u.items
    roles.value = r
  } catch (e) {
    notifyError(e)
  } finally {
    loading.value = false
  }
}

function open(user: PublicUser | null) {
  Object.assign(form, {
    visible: true, user, username: user?.username ?? '', display_name: user?.display_name ?? '', email: user?.email ?? '',
    role: user && ['admin', 'developer', 'viewer'].includes(user.role) ? user.role : 'developer',
    enabled: user?.enabled ?? true, password: '',
  })
}

async function save() {
  const ok = await run(() =>
    form.user
      ? userApi.update(form.user.id, { display_name: form.display_name, email: form.email, role: form.role, enabled: form.enabled })
      : userApi.create({ username: form.username.trim(), display_name: form.display_name, email: form.email, role: form.role, password: form.password }),
  '账号已保存')
  if (ok) {
    form.visible = false
    await load()
  }
}

async function resetPassword(user: PublicUser) {
  if (!(await confirmAction(`重置 ${user.username} 的密码？该账号的会话和 API 令牌会被撤销。`, '重置密码', true))) return
  await run(async () => {
    const r = await userApi.resetPassword(user.id)
    Object.assign(secret, { visible: true, title: `临时密码：${user.username}`, value: r.password })
    await load()
  })
}

async function remove(user: PublicUser) {
  if (!(await confirmAction(`删除账号 ${user.username}？操作不可撤销。`, '删除账号', true))) return
  if (await run(() => userApi.remove(user.id), '账号已删除')) await load()
}

onMounted(load)
</script>

<template>
  <div class="page">
    <div class="page-heading">
      <div>
        <h2>账号管理</h2>
        <div class="hint">管理员、开发者、只读用户。新增账号首次登录必须修改密码。项目和命名空间级权限在“资源管理 › 成员授权”中分配。</div>
      </div>
      <el-button type="primary" @click="open(null)">新增账号</el-button>
    </div>
    <el-card>
      <el-table v-loading="loading" :data="users">
        <el-table-column prop="username" label="用户名" min-width="140" />
        <el-table-column prop="display_name" label="显示名称" min-width="140" />
        <el-table-column prop="role_label" label="角色" width="120" />
        <el-table-column label="状态" width="150">
          <template #default="{ row }">
            <el-tag :type="row.enabled ? 'success' : 'info'">{{ row.enabled ? '启用' : '停用' }}</el-tag>
            <el-tag v-if="row.must_change_password" type="warning" class="gap">待改密</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="最近登录" width="170"><template #default="{ row }">{{ fmtTime(row.last_login_at) }}</template></el-table-column>
        <el-table-column label="操作" width="210">
          <template #default="{ row }">
            <el-button link type="primary" @click="open(row)">编辑</el-button>
            <template v-if="row.id !== session.profile?.id">
              <el-button link type="primary" @click="resetPassword(row)">重置密码</el-button>
              <el-button link type="danger" @click="remove(row)">删除</el-button>
            </template>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-card v-if="roles">
      <template #header><span class="card-title">基础角色权限</span></template>
      <el-table :data="Object.entries(roles.permissions)" size="small">
        <el-table-column label="权限" min-width="200"><template #default="{ row }">{{ row[1] }}</template></el-table-column>
        <el-table-column v-for="r in roles.roles" :key="r.value" :label="r.label" width="110" align="center">
          <template #default="{ row }">{{ r.permissions.includes(row[0]) ? '✓' : '—' }}</template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-dialog v-model="form.visible" :title="form.user ? '编辑账号' : '新增账号'" width="520px">
      <el-form label-position="top">
        <el-form-item label="用户名"><el-input v-model="form.username" :disabled="!!form.user" maxlength="32" /></el-form-item>
        <el-form-item label="显示名称"><el-input v-model="form.display_name" maxlength="80" /></el-form-item>
        <el-form-item label="邮箱"><el-input v-model="form.email" type="email" maxlength="254" /></el-form-item>
        <el-form-item label="角色">
          <el-select v-model="form.role">
            <el-option v-for="r in roles?.roles ?? []" :key="r.value" :label="r.label" :value="r.value" />
          </el-select>
        </el-form-item>
        <el-form-item v-if="form.user"><el-checkbox v-model="form.enabled">启用账号</el-checkbox></el-form-item>
        <el-form-item v-else label="初始密码">
          <el-input v-model="form.password" type="password" show-password autocomplete="new-password" />
          <div class="hint">首次登录后必须修改</div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="form.visible = false">取消</el-button>
        <el-button type="primary" :loading="busy" @click="save">保存</el-button>
      </template>
    </el-dialog>
    <OneTimeSecret v-model="secret.visible" :title="secret.title" :secret="secret.value" />
  </div>
</template>

<style scoped>
.gap {
  margin-left: 6px;
}
</style>
