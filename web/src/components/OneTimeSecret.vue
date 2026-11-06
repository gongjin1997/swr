<script setup lang="ts">
import { copyText } from '@/utils/format'
import { notifyError, notifySuccess } from '@/composables/useAction'

defineProps<{ title: string; secret: string }>()
const visible = defineModel<boolean>({ required: true })

async function copy(secret: string) {
  try {
    await copyText(secret)
    notifySuccess('已复制到剪贴板')
  } catch (e) {
    notifyError(e)
  }
}
</script>

<template>
  <el-dialog v-model="visible" :title="title" width="520px" :close-on-click-modal="false">
    <el-alert type="warning" :closable="false" show-icon title="仅本次展示，请妥善保存，不要通过不安全渠道发送。" />
    <el-input :model-value="secret" readonly type="textarea" :rows="3" class="mono section-gap" />
    <template #footer>
      <el-button @click="copy(secret)">复制</el-button>
      <el-button type="primary" @click="visible = false">关闭</el-button>
    </template>
  </el-dialog>
</template>
