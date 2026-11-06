<script setup lang="ts">
import { computed, ref } from 'vue'
import { deploymentApi } from '@/api'
import type { Deployment } from '@/api/types'
import LogViewer from '@/components/LogViewer.vue'
import StatusTag from '@/components/StatusTag.vue'
import { useLogTail } from '@/composables/useLogTail'

const deploymentId = defineModel<number | null>({ required: true })
const visible = computed({
  get: () => deploymentId.value !== null,
  set: (open: boolean) => {
    if (!open) deploymentId.value = null
  },
})
const status = ref('')
const record = ref<Deployment | null>(null)

const { lines, truncated, error } = useLogTail(deploymentId, {
  interval: 1500,
  fetch: (offset) => deploymentApi.log(deploymentId.value as number, offset),
  onChunk: (chunk) => {
    status.value = chunk.status
  },
  async onFinish() {
    record.value = await deploymentApi.get(deploymentId.value as number).catch(() => null)
  },
})
</script>

<template>
  <el-dialog v-model="visible" :title="`发布日志 #${deploymentId ?? ''}`" width="820px" destroy-on-close @closed="record = null">
    <div class="status">
      <StatusTag v-if="status" :status="status" />
      <span v-if="record" class="hint">{{ record.message || record.error }}</span>
      <span v-if="error" class="hint">读取日志失败：{{ error }}</span>
    </div>
    <LogViewer :lines="lines" :truncated="truncated" height="420px" label="发布日志" />
    <template #footer><el-button @click="visible = false">关闭</el-button></template>
  </el-dialog>
</template>

<style scoped>
.status {
  display: flex;
  gap: 8px;
  align-items: center;
  margin-bottom: 10px;
}
</style>
