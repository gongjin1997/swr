<script setup lang="ts">
/** 发布服务：选择一次成功构建（固定 digest）和配置修订。同一次对话框内重复提交复用幂等键。 */
import { reactive, ref, watch } from 'vue'
import { jobApi, serviceApi } from '@/api'
import { idempotencyKey } from '@/api/http'
import type { Job, Service } from '@/api/types'
import { notifyError, notifySuccess } from '@/composables/useAction'

const props = defineProps<{ service: Service | null }>()
const visible = defineModel<boolean>({ required: true })
const emit = defineEmits<{ submitted: [id: number] }>()

const jobs = ref<Job[]>([])
const form = reactive({ jobId: null as number | null, useLatest: false, remark: '' })
const submitting = ref(false)
let key = ''

watch(visible, async (open) => {
  if (!open || !props.service) return
  key = idempotencyKey()
  Object.assign(form, { jobId: null, useLatest: false, remark: '' })
  try {
    jobs.value = (await jobApi.list({ app_id: props.service.app_id, status: 'success', limit: 100 })).items
    form.jobId = jobs.value[0]?.id ?? null
  } catch (e) {
    notifyError(e)
  }
})

async function submit() {
  if (!props.service || !form.jobId) return notifyError(new Error('请选择一次成功的构建'))
  submitting.value = true
  try {
    const d = await serviceApi.deploy(props.service.id, key, { job_id: form.jobId, use_latest_config: form.useLatest, remark: form.remark.trim() })
    notifySuccess(`发布任务 #${d.id}：${d.status === 'pending_approval' ? '等待审批' : '已排队'}`)
    visible.value = false
    emit('submitted', d.id)
  } catch (e) {
    notifyError(e)
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <el-dialog v-model="visible" :title="`发布 ${service?.name ?? ''}`" width="620px">
    <el-form label-position="top">
      <el-form-item label="成功构建">
        <el-select v-model="form.jobId" filterable placeholder="该服务应用暂无成功构建" style="width: 100%">
          <el-option v-for="j in jobs" :key="j.id" :value="j.id" :label="`#${j.id} · ${j.new_tag || j.image}`">
            <span>#{{ j.id }} · {{ j.new_tag || j.image }}</span>
            <span class="hint digest">{{ j.digest ? j.digest.slice(0, 19) + '…' : '无 digest' }}</span>
          </el-option>
        </el-select>
      </el-form-item>
      <el-form-item>
        <el-checkbox v-model="form.useLatest">使用最新配置修订（默认沿用已部署的修订；首次发布使用最新修订）</el-checkbox>
      </el-form-item>
      <el-form-item label="备注"><el-input v-model="form.remark" maxlength="200" /></el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="visible = false">取消</el-button>
      <el-button type="primary" :loading="submitting" :disabled="!jobs.length" @click="submit">提交发布</el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.digest {
  float: right;
  margin-left: 12px;
}
</style>
