<script setup lang="ts">
/** 项目或命名空间的成员：该范围内的授权记录与"添加成员"。 */
import { onMounted, ref, watch } from 'vue'
import { Plus } from '@element-plus/icons-vue'
import { bindingApi } from '@/api'
import type { BindingRow } from '@/api/types'
import { notifyError } from '@/composables/useAction'
import { useSession } from '@/stores/session'
import GrantDialog from './GrantDialog.vue'
import GrantTable from './GrantTable.vue'

const props = defineProps<{ scopeType: 'project' | 'namespace'; scopeId: number }>()
const session = useSession()
const rows = ref<BindingRow[]>([])
const loading = ref(false)
const adding = ref(false)
const manage = () => props.scopeType === 'project'
  ? session.canProject(props.scopeId, 'project.member.manage')
  : session.canNamespace(props.scopeId, 'namespace.member.manage')

async function load() {
  if (!manage() && !session.canGlobal('user.manage')) return
  loading.value = true
  try {
    rows.value = (await bindingApi.list({ scope_type: props.scopeType, scope_id: props.scopeId })).items
  } catch (e) {
    notifyError(e)
  } finally {
    loading.value = false
  }
}

watch(() => [props.scopeType, props.scopeId], load)
onMounted(load)
</script>

<template>
  <div>
    <div class="members-bar">
      <span class="hint">{{ scopeType === 'project' ? '项目成员可查看或维护项目内的应用与构建。' : '命名空间成员可查看、配置或发布其中的服务。' }}
        不包含通过全局角色获得访问权限的账号。</span>
      <el-button v-if="manage()" type="primary" :icon="Plus" @click="adding = true">添加成员</el-button>
    </div>
    <el-alert v-if="!manage() && !session.canGlobal('user.manage')" type="info" :closable="false" show-icon
      title="没有该范围的成员管理权限" />
    <GrantTable v-else :rows="rows" :loading="loading" hide-scope empty-text="还没有成员" @revoked="load" />
    <GrantDialog v-model="adding" :scope-type="scopeType" :scope-id="scopeId" @granted="load" />
  </div>
</template>

<style scoped>
.members-bar { display: flex; justify-content: space-between; align-items: center; gap: 12px; flex-wrap: wrap; margin-bottom: 14px; }
</style>
