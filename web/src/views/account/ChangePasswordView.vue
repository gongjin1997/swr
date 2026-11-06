<script setup lang="ts">
import { useRouter } from 'vue-router'
import PasswordForm from '@/components/PasswordForm.vue'
import { useSession } from '@/stores/session'

const session = useSession()
const router = useRouter()

async function onChanged() {
  await session.load()
  await router.replace({ name: 'home' })
}
</script>

<template>
  <div class="page narrow">
    <el-alert
      v-if="session.mustChangePassword"
      type="warning"
      :closable="false"
      show-icon
      title="首次登录或密码被重置，请先修改密码后继续。"
      class="section-gap"
    />
    <el-card class="section-gap">
      <template #header><span class="card-title">设置新密码</span></template>
      <PasswordForm @changed="onChanged" />
    </el-card>
  </div>
</template>

<style scoped>
.narrow {
  max-width: 520px;
}
</style>
