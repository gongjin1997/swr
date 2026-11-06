<script setup lang="ts">
/** 构建任务详情：状态、步骤、镜像地址、提示与实时日志。构建页与构建记录抽屉共用。 */
import { computed, ref, toRef, watch } from 'vue'
import { jobApi } from '@/api'
import type { Job } from '@/api/types'
import LogViewer from '@/components/LogViewer.vue'
import StatusTag from '@/components/StatusTag.vue'
import { confirmAction, notifyError, notifySuccess } from '@/composables/useAction'
import { useLogTail } from '@/composables/useLogTail'
import { BUILD_STEPS, JOB_FINAL, MODE_TEXT } from '@/constants'
import { useSession } from '@/stores/session'
import { copyText } from '@/utils/format'

const props = defineProps<{ jobId: number | null; logHeight?: string }>()
const emit = defineEmits<{ finished: [job: Job] }>()
const session = useSession()
const job = ref<Job | null>(null)

const { lines, truncated, error } = useLogTail(toRef(props, 'jobId'), {
  fetch: (offset) => jobApi.log(props.jobId as number, offset),
  async onChunk(chunk) {
    if (!job.value || job.value.id !== props.jobId || job.value.status !== chunk.status || job.value.step !== chunk.step) {
      job.value = await jobApi.get(props.jobId as number)
    }
  },
  onFinish() {
    if (job.value) emit('finished', job.value)
  },
})

watch(
  () => props.jobId,
  () => {
    job.value = null
  },
)

const stepIndex = computed(() => BUILD_STEPS.findIndex(([key]) => key === job.value?.step))
const activeStep = computed(() => (job.value?.status === 'success' ? BUILD_STEPS.length : Math.max(stepIndex.value, 0)))
const stepStatus = computed(() => {
  const status = job.value?.status
  if (status === 'failed' || status === 'cancelled') return 'error'
  return 'process'
})

const canCancel = computed(() => {
  const j = job.value
  if (!j || JOB_FINAL.has(j.status)) return false
  return session.can('job.cancel') || (session.can('job.cancel.own') && String(j.created_by) === String(session.profile?.id))
})

async function cancel() {
  if (!job.value || !(await confirmAction('确定取消这个构建任务？', '取消构建', true))) return
  try {
    await jobApi.cancel(job.value.id)
    notifySuccess('已取消')
  } catch (e) {
    notifyError(e)
  }
}

async function copyImage() {
  try {
    await copyText(job.value?.image ?? '')
    notifySuccess('已复制镜像地址')
  } catch (e) {
    notifyError(e)
  }
}
</script>

<template>
  <div v-if="jobId !== null" class="job">
    <div class="head">
      <div class="title">
        <span class="mono">#{{ jobId }}</span>
        <span v-if="job">{{ job.app_name }}<template v-if="job.new_tag"> : {{ job.new_tag }}</template><template v-if="job.mode"> · {{ MODE_TEXT[job.mode] }}</template></span>
        <span v-else class="hint">加载中…</span>
        <StatusTag v-if="job" :status="job.status" />
      </div>
      <el-button v-if="canCancel" type="danger" plain size="small" @click="cancel">取消构建</el-button>
    </div>
    <div v-if="job?.image" class="image">
      <code class="mono break-word">{{ job.image }}</code>
      <el-button size="small" @click="copyImage">复制</el-button>
    </div>
    <el-steps :active="activeStep" :process-status="stepStatus" finish-status="success" simple class="steps">
      <el-step v-for="[key, title] in BUILD_STEPS" :key="key" :title="title" />
    </el-steps>
    <el-alert v-if="job?.notes?.length" type="warning" :closable="false" class="section-gap">
      <ul class="notes"><li v-for="n in job.notes" :key="n">{{ n }}</li></ul>
    </el-alert>
    <el-alert v-if="job?.error" type="error" :title="job.error" :closable="false" class="section-gap" />
    <p v-if="error" class="hint">读取日志失败：{{ error }}，稍后自动重试</p>
    <LogViewer :lines="lines" :truncated="truncated" :height="logHeight" label="构建日志" class="section-gap" />
  </div>
  <el-empty v-else description="还没有进行中的构建。选择应用、上传文件后点击“上传并构建”，这里会实时显示日志" />
</template>

<style scoped>
.head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 8px;
}
.title {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  font-weight: 600;
}
.image {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 10px;
}
.steps {
  margin-top: 12px;
}
.notes {
  margin: 0;
  padding-left: 18px;
}
</style>
