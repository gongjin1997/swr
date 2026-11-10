<script setup lang="ts">
import { computed, ref, watch } from 'vue'
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
watch(deploymentId, async id => {
  record.value = null
  if (id) record.value = await deploymentApi.get(id).catch(() => null)
})

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
  <el-dialog v-model="visible" :title="`发布详情 #${deploymentId ?? ''}`" width="820px" destroy-on-close @closed="record = null">
    <el-descriptions v-if="record" :column="2" border class="section-gap">
      <el-descriptions-item label="服务">{{ record.target_name }}</el-descriptions-item><el-descriptions-item label="项目 / 应用">{{ record.project_name }} / {{ record.app_name }}</el-descriptions-item>
      <el-descriptions-item label="命名空间">{{ record.cluster_name }} / {{ record.namespace_name }}</el-descriptions-item><el-descriptions-item label="发起人">{{ record.operator }}</el-descriptions-item>
      <el-descriptions-item label="构建 / 修订">#{{ record.job_id }} / {{ record.revision_id }}</el-descriptions-item><el-descriptions-item label="申请备注">{{ record.remark || '无' }}</el-descriptions-item>
      <el-descriptions-item label="镜像" :span="2"><span class="mono break-word">{{ record.image }}</span></el-descriptions-item>
    </el-descriptions>
    <el-table v-if="record?.approvals?.length" :data="record.approvals" class="section-gap"><el-table-column prop="approver_id" label="审批账号 ID" /><el-table-column label="意见"><template #default="{ row }">{{ row.decision === 'approved' ? '批准' : '拒绝' }}</template></el-table-column><el-table-column prop="comment" label="审批备注" /><el-table-column prop="created_at" label="时间" /></el-table>
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
