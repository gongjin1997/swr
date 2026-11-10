<script setup lang="ts">
import { reactive, ref } from 'vue'
import { Lock, User } from '@element-plus/icons-vue'
import { usePlatform } from '@/stores/platform'
import { useRoute, useRouter } from 'vue-router'
import { authApi } from '@/api'
import { errorText } from '@/composables/useAction'
import { safeNext } from '@/router'
import { useSession } from '@/stores/session'

const session = useSession()
const router = useRouter()
const route = useRoute()
const form = reactive({ username: '', password: '', remember: true })
const error = ref('')
const busy = ref(false)

async function submit() {
  if (busy.value) return
  if (!form.username.trim() || !form.password) {
    error.value = '请输入用户名和密码'
    return
  }
  busy.value = true
  error.value = ''
  try {
    await authApi.login(form.username.trim(), form.password, form.remember)
    usePlatform().reset()
    await session.load()
    await router.replace(session.mustChangePassword ? { name: 'change-password' } : safeNext(route.query.next))
  } catch (e) {
    error.value = errorText(e)
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <main class="login-page">
    <section class="login-card">
      <div class="login-heading"><h2>{{ session.siteName }}</h2><p class="hint">登录工作台</p></div>
      <el-form label-position="top" size="large" @submit.prevent="submit">
        <el-form-item label="用户名"><el-input v-model="form.username" placeholder="请输入账号" :prefix-icon="User" autocomplete="username" maxlength="32" autofocus /></el-form-item>
        <el-form-item label="密码"><el-input v-model="form.password" placeholder="请输入密码" :prefix-icon="Lock" type="password" autocomplete="current-password" maxlength="128" show-password /></el-form-item>
        <div class="login-options"><el-checkbox v-model="form.remember">记住登录状态</el-checkbox><el-tooltip content="请联系平台管理员重置密码" trigger="click"><el-button link>忘记密码？</el-button></el-tooltip></div>
        <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon role="alert" class="login-error" />
        <el-button type="primary" native-type="submit" :loading="busy" class="login-submit">登录</el-button>
      </el-form>
      <p class="login-note">使用管理员分配的账号访问平台</p>
    </section>
  </main>
</template>

<style scoped>
.login-page { min-height: 100vh; display: flex; flex-direction: column; align-items: center; justify-content: center; padding: 32px 16px; background: var(--hi-bg-page); }
.login-card { width: min(420px, 100%); padding: 36px; border: 1px solid var(--hi-border); border-radius: 14px; background: var(--hi-bg-card); box-shadow: 0 16px 48px #1018280c; }
.login-heading { margin: 0 0 24px; }
.login-heading h2 { font-size: 17px; margin: 0 0 6px; }
.login-options { display: flex; align-items: center; justify-content: space-between; margin: -4px 0 20px; }
.login-options :deep(.el-checkbox__label) { font-size: 12px; }
.login-options .el-button { font-size: 12px; color: var(--hi-text-3); }
.login-error { margin-bottom: 18px; }
.login-submit { width: 100%; height: 42px; }
.login-note { margin: 24px 0 0; font-size: 11px; color: var(--hi-text-3); text-align: center; }
@media (max-width: 420px) { .login-card { padding: 28px 24px; } }
</style>
