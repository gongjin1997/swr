<script setup lang="ts">
import { Plus, Search } from '@element-plus/icons-vue'
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { roleApi } from '@/api'
import type { RoleDefinition, ScopeType } from '@/api/types'
import PermissionPicker from '@/components/access/PermissionPicker.vue'
import PermissionTags from '@/components/access/PermissionTags.vue'
import { confirmAction, notifyError, useAction } from '@/composables/useAction'
import { SCOPE_TEXT } from '@/constants'
import { useCatalog } from '@/stores/catalog'
import { useSession } from '@/stores/session'
import TablePager from '@/components/TablePager.vue'
import { usePagination } from '@/composables/usePagination'

const session = useSession()
const catalog = useCatalog()
const router = useRouter()
const loading = ref(false)
const filters = reactive({ scope: '' as ScopeType | '', keyword: '' })
const { busy, run } = useAction()
const editable = computed(() => session.canGlobal('role.manage'))
const form = reactive({ visible: false, code: null as string | null, data: { code: '', name: '', scope_type: 'namespace' as ScopeType,
  permissions: [] as string[], description: '', enabled: true } })
const rows = computed(() => catalog.roles.filter((role) =>
  (!filters.scope || role.scope_type === filters.scope) &&
  (!filters.keyword.trim() || `${role.name} ${role.code} ${role.description}`.toLowerCase().includes(filters.keyword.trim().toLowerCase()))))
const pager = usePagination(() => rows.value, { resetOn: () => [filters.scope, filters.keyword] })

async function load(force = true) {
  loading.value = true
  try {
    await catalog.load(force)
  } catch (e) {
    notifyError(e)
  } finally {
    loading.value = false
  }
}

function open(role?: RoleDefinition) {
  form.code = role?.code ?? null
  form.data = { code: role?.code ?? '', name: role?.name ?? '', scope_type: role?.scope_type ?? 'namespace',
    permissions: [...(role?.permissions ?? [])], description: role?.description ?? '', enabled: role?.enabled ?? true }
  form.visible = true
}

async function save() {
  if (!form.data.permissions.length) return notifyError(new Error('请至少选择一项权限'))
  const ok = await run(() => roleApi.save(form.code, { ...form.data, permissions: [...form.data.permissions] }), '角色已保存')
  if (ok) {
    form.visible = false
    await Promise.all([load(), session.load()])
  }
}

async function toggle(role: RoleDefinition) {
  const action = role.enabled ? '停用' : '启用'
  if (role.enabled && !(await confirmAction(`停用角色“${role.name}”？持有该角色的 ${role.binding_count} 个授权会立即失效。`, '停用角色', true))) return
  if (await run(() => roleApi.save(role.code, { code: role.code, name: role.name, scope_type: role.scope_type,
    permissions: role.permissions, description: role.description, enabled: !role.enabled }), `已${action}`)) {
    await Promise.all([load(), session.load()])
  }
}

async function remove(role: RoleDefinition) {
  if (!(await confirmAction(`删除角色“${role.name}”？`, '删除角色', true))) return
  if (await run(() => roleApi.remove(role.code), '角色已删除')) await load()
}

function showBindings(role: RoleDefinition) {
  void router.push({ name: 'bindings', query: { role: role.code } })
}

onMounted(() => load(false))
</script>

<template>
  <el-card>
    <div class="filter-bar">
      <el-input v-model="filters.keyword" :prefix-icon="Search" placeholder="搜索角色名称或标识" clearable aria-label="搜索角色" />
      <el-select v-model="filters.scope" clearable placeholder="全部范围" aria-label="按授权范围筛选">
        <el-option v-for="(label, key) in SCOPE_TEXT" :key="key" :label="label" :value="key" />
      </el-select>
      <span class="count">共 {{ rows.length }} 个角色</span>
      <el-button v-if="editable" type="primary" :icon="Plus" @click="open()">新建角色</el-button>
    </div>
    <el-table v-loading="loading" :data="pager.rows.value" row-key="code" empty-text="暂无角色">
      <el-table-column type="expand">
        <template #default="{ row }"><div class="expand"><PermissionTags :permissions="row.permissions" /></div></template>
      </el-table-column>
      <el-table-column label="角色" min-width="200">
        <template #default="{ row }">
          <b>{{ row.name }}</b><el-tag v-if="row.builtin" size="small" effect="plain" class="gap">内置</el-tag>
          <div class="hint mono">{{ row.code }}</div>
        </template>
      </el-table-column>
      <el-table-column label="范围" width="100"><template #default="{ row }">{{ SCOPE_TEXT[row.scope_type as ScopeType] }}</template></el-table-column>
      <el-table-column label="说明" min-width="260"><template #default="{ row }"><span class="hint">{{ row.description || `${row.permissions.length} 项权限` }}</span></template></el-table-column>
      <el-table-column label="授权数" width="90">
        <template #default="{ row }"><el-button link type="primary" :disabled="!row.binding_count" @click="showBindings(row)">{{ row.binding_count }}</el-button></template>
      </el-table-column>
      <el-table-column label="状态" width="90"><template #default="{ row }"><el-tag :type="row.enabled ? 'success' : 'info'">{{ row.enabled ? '启用' : '停用' }}</el-tag></template></el-table-column>
      <el-table-column v-if="editable" label="操作" width="170" fixed="right">
        <template #default="{ row }">
          <span v-if="row.builtin" class="hint">不可修改</span>
          <template v-else>
            <el-button link type="primary" @click="open(row)">编辑</el-button>
            <el-button link :type="row.enabled ? 'warning' : 'primary'" @click="toggle(row)">{{ row.enabled ? '停用' : '启用' }}</el-button>
            <el-tooltip :content="row.binding_count ? '仍有授权使用该角色，请先撤销或停用' : '删除角色'">
              <el-button link type="danger" :disabled="row.binding_count > 0" @click="remove(row)">删除</el-button>
            </el-tooltip>
          </template>
        </template>
      </el-table-column>
    </el-table>
    <TablePager v-model:page="pager.page.value" v-model:size="pager.size.value" :total="pager.total.value" />
  </el-card>

  <el-dialog v-model="form.visible" :title="form.code ? '编辑角色' : '新建角色'" width="760px" top="6vh" :close-on-click-modal="false">
    <el-form label-position="top" @submit.prevent="save">
      <div class="form-grid">
        <el-form-item label="角色名称" required><el-input v-model="form.data.name" maxlength="80" placeholder="如 UAT 审批员" /></el-form-item>
        <el-form-item label="角色标识" required>
          <el-input v-model="form.data.code" :disabled="!!form.code" maxlength="48" placeholder="小写字母开头，如 uat_reviewer" />
        </el-form-item>
        <el-form-item label="授权范围">
          <el-radio-group v-model="form.data.scope_type" :disabled="!!form.code" @change="form.data.permissions = []">
            <el-radio-button v-for="(label, key) in SCOPE_TEXT" :key="key" :value="key">{{ label }}</el-radio-button>
          </el-radio-group>
          <div class="hint">全局角色对所有项目和命名空间生效；创建后不能修改范围</div>
        </el-form-item>
        <el-form-item label="状态"><el-switch v-model="form.data.enabled" active-text="启用" inactive-text="停用" /></el-form-item>
      </div>
      <el-form-item label="权限" required><PermissionPicker v-model="form.data.permissions" :scope="form.data.scope_type" /></el-form-item>
      <el-form-item label="说明"><el-input v-model="form.data.description" type="textarea" :rows="2" maxlength="300" /></el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="form.visible = false">取消</el-button>
      <el-button type="primary" :loading="busy" @click="save">保存</el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.gap { margin-left: 6px; }
.expand { padding: 6px 48px 10px; }
</style>
