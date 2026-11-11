<script setup lang="ts">
/** 构建任务详情：状态、步骤、镜像地址、提示与实时日志。构建页与构建记录抽屉共用。 */
import { CircleCheck, CircleClose, Document } from '@element-plus/icons-vue'
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
const stepFailed = computed(() => job.value?.status === 'failed' || job.value?.status === 'cancelled')

const canCancel = computed(() => {
  const j = job.value
  if (!j || JOB_FINAL.has(j.status)) return false
  return session.canProject(j.project_id, 'job.cancel') ||
    (session.canProject(j.project_id, 'job.cancel.own') && String(j.created_by) === String(session.userId))
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
      <RouterLink v-if="job.status === 'success' && session.deployEnabled" :to="{ name: 'releases', query: { app: job.app_id, job: job.id } }"><el-button type="primary" size="small">发布此构建</el-button></RouterLink>
    </div>
    <ol class="build-progress" aria-label="构建执行进度">
      <li v-for="([key, title], index) in BUILD_STEPS" :key="key" :class="{ done: index < activeStep, current: index === activeStep, failed: index === activeStep && stepFailed }" :aria-current="index === activeStep ? 'step' : undefined">
        <el-icon v-if="index < activeStep" :size="20"><CircleCheck /></el-icon>
        <el-icon v-else-if="index === activeStep && stepFailed" :size="20"><CircleClose /></el-icon>
        <span v-else class="step-number">{{ index + 1 }}</span><span>{{ title }}</span>
      </li>
    </ol>
    <el-alert v-if="job?.notes?.length" type="warning" :closable="false" class="section-gap">
      <ul class="notes"><li v-for="n in job.notes" :key="n">{{ n }}</li></ul>
    </el-alert>
    <el-alert v-if="job?.error" type="error" :title="job.error" :closable="false" class="section-gap" />
    <p v-if="error" class="hint">读取日志失败：{{ error }}，稍后自动重试</p>
    <LogViewer :lines="lines" :truncated="truncated" :height="logHeight" label="构建日志" class="section-gap" />
  </div>
  <div v-else class="log-empty"><div class="terminal-caption"><i /><i /><i /><span>BUILD OUTPUT</span></div><div class="log-waiting"><el-icon :size="34"><Document /></el-icon><h3>等待新的构建任务</h3><p>选择应用、上传制品并开始构建，<br />即可在此查看实时日志与执行进度。</p></div><div class="terminal-footer"><span class="terminal-cursor" />准备就绪，等待构建…</div></div>
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
.build-progress { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); list-style: none; padding: 16px 8px; gap: 8px; margin: 18px 0 0; background: var(--hi-bg-soft); border-radius: 6px; }
.build-progress li { display: flex; flex-direction: column; align-items: center; gap: 8px; color: var(--hi-text-3); font-size: 11px; white-space: nowrap; }
.build-progress li.done { color: var(--el-color-success); }
.build-progress li.current { color: var(--el-color-primary); }
.build-progress li.failed { color: var(--el-color-danger); }
.step-number { width: 20px; height: 20px; display: grid; place-items: center; border: 1px solid currentColor; border-radius: 50%; font: 10px var(--app-mono); }
.notes {
  margin: 0;
  padding-left: 18px;
}
.log-empty { background: var(--app-log-bg); color: #a9b6ca; min-height: 410px; border-radius: 8px; display: flex; flex-direction: column; }
.terminal-caption { display: flex; align-items: center; gap: 6px; padding: 15px 18px; border-bottom: 1px solid #ffffff0d; }
.terminal-caption i { width: 7px; height: 7px; background: #516078; border-radius: 50%; }
.terminal-caption span { margin-left: 7px; font: 10px var(--app-mono); }
.log-waiting { margin: auto; padding: 45px 20px; text-align: center; }
.log-waiting .el-icon { color: #6e819e; }
.log-waiting h3 { font-size: 14px; font-weight: 500; margin: 16px 0 8px; color: #d5deec; }
.log-waiting p { font-size: 12px; line-height: 1.9; margin: 0; }
.terminal-footer { padding: 16px 18px; font: 11px var(--app-mono); display: flex; gap: 8px; border-top: 1px solid #ffffff0d; }
.terminal-cursor { display: inline-block; width: 6px; height: 13px; background: #78a7ff; }
@media (max-width: 600px) { .head { flex-wrap: wrap; } }
</style>
