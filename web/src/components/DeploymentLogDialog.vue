<script setup lang="ts">
/** 发布详情：服务、环境、镜像、审批记录与实时日志。发布中心与服务详情共用。 */
import { computed, ref, watch } from 'vue'
import { deploymentApi } from '@/api'
import type { Approval, Deployment } from '@/api/types'
import LogViewer from '@/components/LogViewer.vue'
import StatusTag from '@/components/StatusTag.vue'
import { useLogTail } from '@/composables/useLogTail'
import { DEPLOYMENT_KIND_TEXT, TIER_TAG, TIER_TEXT } from '@/constants'
import { fmtTime } from '@/utils/format'

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
  status.value = ''
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

const approver = (a: Approval) => a.approver_display_name || a.approver_username || `账号 #${a.approver_id}`
</script>

<template>
  <el-dialog v-model="visible" :title="`发布详情 #${deploymentId ?? ''}`" width="820px" destroy-on-close @closed="record = null">
    <el-descriptions v-if="record" :column="2" border class="section-gap">
      <el-descriptions-item label="服务">
        {{ record.target_name }} <span class="hint">{{ DEPLOYMENT_KIND_TEXT[record.kind] ?? record.kind }}</span>
      </el-descriptions-item>
      <el-descriptions-item label="项目 / 应用">{{ record.project_name }} / {{ record.app_name }}</el-descriptions-item>
      <el-descriptions-item label="环境">
        <el-tag size="small" :type="TIER_TAG[record.environment] ?? 'info'" effect="plain">{{ TIER_TEXT[record.environment] ?? record.environment }}</el-tag>
        <span class="hint gap">{{ record.cluster_name }} / {{ record.namespace_name }}</span>
      </el-descriptions-item>
      <el-descriptions-item label="发起人">{{ record.operator }}<span class="hint gap">{{ fmtTime(record.created_at) }}</span></el-descriptions-item>
      <el-descriptions-item label="构建 / 修订">#{{ record.job_id }} / {{ record.revision_id ?? '-' }}</el-descriptions-item>
      <el-descriptions-item label="申请备注">{{ record.remark || '无' }}</el-descriptions-item>
      <el-descriptions-item label="镜像" :span="2"><span class="mono break-word">{{ record.image }}</span></el-descriptions-item>
    </el-descriptions>
    <el-table v-if="record?.approvals?.length" :data="record.approvals" size="small" class="section-gap">
      <el-table-column label="审批人" min-width="150"><template #default="{ row }">{{ approver(row) }}</template></el-table-column>
      <el-table-column label="意见" width="90">
        <template #default="{ row }">
          <el-tag size="small" :type="row.decision === 'approved' ? 'success' : 'danger'">{{ row.decision === 'approved' ? '批准' : '拒绝' }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="审批备注" min-width="180"><template #default="{ row }">{{ row.comment || '-' }}</template></el-table-column>
      <el-table-column label="时间" width="170"><template #default="{ row }">{{ fmtTime(row.created_at) }}</template></el-table-column>
    </el-table>
    <div class="status">
      <StatusTag v-if="status || record" :status="status || record?.status || ''" />
      <span v-if="record?.status === 'pending_approval'" class="hint">{{ record.require_admin_approval ? '需要平台管理员确认' : '等待独立审批' }}</span>
      <span v-else-if="record" class="hint">{{ record.message || record.error }}</span>
      <span v-if="error" class="hint">读取日志失败：{{ error }}</span>
    </div>
    <LogViewer :lines="lines" :truncated="truncated" height="420px" label="发布日志" />
    <template #footer><el-button @click="visible = false">关闭</el-button></template>
  </el-dialog>
</template>

<style scoped>
.gap { margin-left: 6px; }
.status {
  display: flex;
  gap: 8px;
  align-items: center;
  margin-bottom: 10px;
}
</style>
