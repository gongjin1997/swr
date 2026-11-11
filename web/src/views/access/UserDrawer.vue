<script setup lang="ts">
/** 账号详情：授权记录（可新增、撤销）与按范围汇总的有效权限。 */
import { Plus } from '@element-plus/icons-vue'
import { computed, ref, watch } from 'vue'
import { userApi } from '@/api'
import type { AccountDetail } from '@/api/types'
import GrantDialog from '@/components/access/GrantDialog.vue'
import GrantTable from '@/components/access/GrantTable.vue'
import PermissionTags from '@/components/access/PermissionTags.vue'
import { notifyError } from '@/composables/useAction'
import { TIER_TEXT } from '@/constants'
import { useCatalog } from '@/stores/catalog'
import { fmtTime } from '@/utils/format'

const props = defineProps<{ userId: number | null }>()
const emit = defineEmits<{ close: []; changed: [] }>()
const catalog = useCatalog()
const detail = ref<AccountDetail | null>(null)
const loading = ref(false)
const granting = ref(false)
const visible = computed({
  get: () => props.userId !== null,
  set: (open: boolean) => { if (!open) emit('close') },
})

async function load() {
  if (props.userId === null) return
  loading.value = true
  try {
    const [data] = await Promise.all([userApi.get(props.userId), catalog.load()])
    detail.value = data
  } catch (e) {
    notifyError(e)
    emit('close')
  } finally {
    loading.value = false
  }
}

async function changed() {
  await load()
  emit('changed')
}

watch(() => props.userId, (id) => {
  detail.value = null
  if (id !== null) void load()
}, { immediate: true })
</script>

<template>
  <el-drawer v-model="visible" :title="detail ? `${detail.display_name}（${detail.username}）` : '账号详情'" size="min(860px, 100vw)">
    <div v-loading="loading">
      <template v-if="detail">
        <el-descriptions :column="2" border size="small">
          <el-descriptions-item label="身份">
            <el-tag v-if="detail.is_platform_admin" type="danger" effect="plain">平台管理员</el-tag>
            <span v-else>{{ detail.role_label }}</span>
          </el-descriptions-item>
          <el-descriptions-item label="状态">
            <el-tag :type="detail.enabled ? 'success' : 'info'">{{ detail.enabled ? '启用' : '停用' }}</el-tag>
            <el-tag v-if="detail.must_change_password" type="warning" class="gap">待改密</el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="邮箱">{{ detail.email || '-' }}</el-descriptions-item>
          <el-descriptions-item label="最近登录">{{ fmtTime(detail.last_login_at) }}</el-descriptions-item>
          <el-descriptions-item label="创建时间">{{ fmtTime(detail.created_at) }}</el-descriptions-item>
          <el-descriptions-item label="密码修改">{{ fmtTime(detail.password_changed_at) }}</el-descriptions-item>
        </el-descriptions>

        <div class="section-title">
          <h3>授权记录</h3>
          <el-button type="primary" size="small" :icon="Plus" @click="granting = true">新增授权</el-button>
        </div>
        <GrantTable :rows="detail.bindings" hide-subject empty-text="该账号还没有任何授权" @revoked="changed" />

        <div class="section-title"><h3>有效权限</h3><span class="hint">按授权计算；停用账号显示的是启用后将拥有的权限</span></div>
        <el-collapse>
          <el-collapse-item v-if="detail.access.global.length" :title="`全局（${detail.access.global.length} 项权限）`" name="global">
            <PermissionTags :permissions="detail.access.global" />
          </el-collapse-item>
          <el-collapse-item v-for="p in detail.access.projects" :key="`p${p.id}`" :title="`项目：${p.name}`" :name="`p${p.id}`">
            <PermissionTags :permissions="p.perms" />
          </el-collapse-item>
          <el-collapse-item v-for="n in detail.access.namespaces" :key="`n${n.id}`"
            :title="`命名空间：${n.cluster} / ${n.name}（${TIER_TEXT[n.tier ?? ''] ?? n.tier}）`" :name="`n${n.id}`">
            <PermissionTags :permissions="n.perms" />
          </el-collapse-item>
        </el-collapse>
        <el-empty v-if="!detail.access.global.length && !detail.access.projects.length && !detail.access.namespaces.length"
          description="没有任何权限。登录后只能修改自己的资料和密码。" :image-size="70" />
      </template>
    </div>
    <GrantDialog v-if="detail" v-model="granting" :subject-id="detail.id" :subject-label="`${detail.display_name}（${detail.username}）`"
      @granted="changed" />
  </el-drawer>
</template>

<style scoped>
.gap { margin-left: 6px; }
.section-title { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin: 22px 0 10px; flex-wrap: wrap; }
.section-title h3 { margin: 0; font-size: 14px; }
</style>
