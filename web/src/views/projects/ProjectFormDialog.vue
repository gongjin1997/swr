<script setup lang="ts">
/** 新建或编辑项目：slug、名称与 SWR 组织授权。项目列表与项目详情共用。 */
import { reactive, watch } from 'vue'
import { projectApi } from '@/api'
import type { Project, RegistryOrg } from '@/api/types'
import RegistryOrgsEditor from '@/components/RegistryOrgsEditor.vue'
import { useAction } from '@/composables/useAction'
import { useSession } from '@/stores/session'

const props = defineProps<{ project: Project | null }>()
const visible = defineModel<boolean>({ required: true })
const emit = defineEmits<{ saved: [project: Project] }>()
const session = useSession()
const { busy, run } = useAction()
const form = reactive({ slug: '', name: '', orgs: [] as RegistryOrg[] })

watch(visible, (open) => {
  if (!open) return
  Object.assign(form, {
    slug: props.project?.slug ?? '',
    name: props.project?.name ?? '',
    orgs: (props.project?.registry_orgs ?? []).map((o) => ({ namespace: o.namespace, base_prefixes: [...o.base_prefixes] })),
  })
})

async function save() {
  const data = {
    slug: form.slug.trim().toLowerCase(),
    name: form.name.trim(),
    registry_orgs: form.orgs.filter((o) => o.namespace.trim()).map((o) => ({ namespace: o.namespace.trim(), base_prefixes: o.base_prefixes })),
  }
  const result: { project?: Project } = {}
  const ok = await run(async () => {
    result.project = props.project ? await projectApi.update(props.project.id, data) : await projectApi.create(data)
    await session.load()
  }, '项目已保存')
  if (ok && result.project) {
    visible.value = false
    emit('saved', result.project)
  }
}
</script>

<template>
  <el-dialog v-model="visible" :title="project ? `编辑项目 · ${project.name}` : '新建项目'" width="720px" :close-on-click-modal="false">
    <el-form label-position="top" @submit.prevent="save">
      <div class="form-grid">
        <el-form-item label="项目名称" required><el-input v-model="form.name" maxlength="64" placeholder="如 支付中心" /></el-form-item>
        <el-form-item label="项目标识（slug）" required>
          <el-input v-model="form.slug" :disabled="project?.slug === 'default'" maxlength="63" placeholder="小写字母、数字和 -，如 payments" />
        </el-form-item>
      </div>
      <el-form-item label="SWR 组织与镜像仓库前缀">
        <RegistryOrgsEditor v-model="form.orgs" />
        <div class="hint">项目内的应用只能推送到、或基于这些组织下的镜像构建。</div>
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="visible = false">取消</el-button>
      <el-button type="primary" :loading="busy" @click="save">保存</el-button>
    </template>
  </el-dialog>
</template>
