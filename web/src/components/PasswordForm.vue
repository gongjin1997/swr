<script setup lang="ts">
import { reactive, ref } from 'vue'
import type { FormInstance, FormRules } from 'element-plus'
import { accountApi } from '@/api'
import { useAction } from '@/composables/useAction'

const emit = defineEmits<{ changed: [] }>()
const formRef = ref<FormInstance>()
const form = reactive({ old: '', next: '', confirm: '' })
const { busy, run } = useAction()

const rules: FormRules = {
  old: [{ required: true, message: '请输入当前密码', trigger: 'blur' }],
  next: [
    { required: true, message: '请输入新密码', trigger: 'blur' },
    { pattern: /(?=.*[A-Za-z])(?=.*\d)/, message: '需要同时包含字母和数字', trigger: 'blur' },
  ],
  confirm: [
    {
      validator: (_rule, value, callback) => (value === form.next ? callback() : callback(new Error('两次新密码不一致'))),
      trigger: 'blur',
    },
  ],
}

async function submit() {
  if (!(await formRef.value?.validate().catch(() => false))) return
  const ok = await run(() => accountApi.changePassword(form.old, form.next), '密码已修改')
  if (ok) {
    formRef.value?.resetFields()
    emit('changed')
  }
}
</script>

<template>
  <el-form ref="formRef" :model="form" :rules="rules" label-position="top" @submit.prevent="submit">
    <el-form-item label="当前密码" prop="old">
      <el-input v-model="form.old" type="password" autocomplete="current-password" show-password />
    </el-form-item>
    <el-form-item label="新密码" prop="next">
      <el-input v-model="form.next" type="password" autocomplete="new-password" show-password />
      <div class="hint">至少包含字母和数字，最小长度以系统安全设置为准</div>
    </el-form-item>
    <el-form-item label="确认新密码" prop="confirm">
      <el-input v-model="form.confirm" type="password" autocomplete="new-password" show-password />
    </el-form-item>
    <el-button type="primary" native-type="submit" :loading="busy">修改密码</el-button>
    <p class="hint">修改后其他登录会话及全部 API 令牌失效。</p>
  </el-form>
</template>
