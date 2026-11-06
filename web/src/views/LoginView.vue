<script setup lang="ts">
import { reactive, ref } from 'vue'
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
  if (!form.username.trim() || !form.password) {
    error.value = '请输入用户名和密码'
    return
  }
  busy.value = true
  error.value = ''
  try {
    await authApi.login(form.username.trim(), form.password, form.remember)
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
  <div class="login-page">
    <section class="intro">
      <span class="eyebrow">SWR IMAGE BUILDER</span>
      <h1>{{ session.siteName }}</h1>
      <p class="slogan">从制品到镜像，<br />让每次发布有迹可循。</p>
      <div class="steps"><span>01 上传制品</span><span>02 增量构建</span><span>03 推送与部署</span></div>
    </section>
    <el-card class="login-card" shadow="always">
      <h2>登录工作台</h2>
      <p class="hint">使用管理员分配的账号登录</p>
      <el-form label-position="top" @submit.prevent="submit">
        <el-form-item label="用户名">
          <el-input v-model="form.username" autocomplete="username" maxlength="32" autofocus />
        </el-form-item>
        <el-form-item label="密码">
          <el-input v-model="form.password" type="password" autocomplete="current-password" maxlength="128" show-password />
        </el-form-item>
        <el-form-item>
          <el-checkbox v-model="form.remember">记住我（仅建议在个人设备上使用）</el-checkbox>
        </el-form-item>
        <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon role="alert" class="error" />
        <el-button type="primary" native-type="submit" :loading="busy" class="submit">登录</el-button>
      </el-form>
      <p class="hint">首次启动：用户名为 admin，初始密码由管理员配置，或在服务器 data/admin-initial-password.txt 中读取。</p>
    </el-card>
  </div>
</template>

<style scoped>
.login-page {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 64px;
  padding: 24px;
  flex-wrap: wrap;
}
.intro {
  max-width: 420px;
}
.eyebrow {
  letter-spacing: 0.2em;
  font-size: 12px;
  color: var(--el-color-primary);
}
.intro h1 {
  margin: 8px 0 16px;
  font-size: 30px;
}
.slogan {
  font-size: 18px;
  line-height: 1.7;
  color: var(--app-text-2);
}
.steps {
  display: flex;
  gap: 16px;
  margin-top: 24px;
  font-size: 13px;
  color: var(--app-text-2);
}
.login-card {
  width: 380px;
  max-width: 100%;
}
.login-card h2 {
  margin: 0 0 4px;
}
.error {
  margin-bottom: 16px;
}
.submit {
  width: 100%;
}
</style>
