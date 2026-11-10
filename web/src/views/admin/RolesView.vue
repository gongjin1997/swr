<script setup lang="ts">
import { computed, onMounted, reactive, ref, shallowRef } from 'vue'
import { roleApi } from '@/api'
import type { RoleCatalog, RoleDefinition } from '@/api/types'
import { confirmAction, notifyError, useAction } from '@/composables/useAction'
import { useSession } from '@/stores/session'
const session = useSession()
const catalog = ref<RoleCatalog | null>(null)
const scope = shallowRef('')
const editing = shallowRef<string | null>(null)
const visible = shallowRef(false)
const { busy, run } = useAction()
const scopeLabels: Record<string, string> = { global: '全局', project: '项目', namespace: '命名空间' }
const form = reactive({ code: '', name: '', scope_type: 'namespace' as RoleDefinition['scope_type'], permissions: [] as string[], description: '', enabled: true })
const rows = computed(() => (catalog.value?.items ?? []).filter(role => !scope.value || role.scope_type === scope.value))
const permissionOptions = computed(() => catalog.value?.scope_permissions[form.scope_type] ?? [])
async function load() { try { catalog.value = await roleApi.list() } catch (e) { notifyError(e) } }
function open(role?: RoleDefinition) {
  editing.value = role?.code ?? null
  Object.assign(form, { code: role?.code ?? '', name: role?.name ?? '', scope_type: role?.scope_type ?? 'namespace',
    permissions: [...(role?.permissions ?? [])], description: role?.description ?? '', enabled: role?.enabled ?? true })
  visible.value = true
}
async function save() {
  if (await run(() => roleApi.save(editing.value, { ...form, permissions: [...form.permissions] }), '角色已保存')) {
    visible.value = false
    await Promise.all([load(), session.load()])
  }
}
async function remove(role: RoleDefinition) {
  if (await confirmAction(`删除角色“${role.name}”？`, '删除角色', true) &&
      await run(() => roleApi.remove(role.code), '角色已删除')) await load()
}
onMounted(load)
</script>
<template>
  <div class="page">
    <el-card>
      <template #header><div class="card-title"><span>角色管理</span><div class="toolbar"><RouterLink to="/resources/bindings"><el-button>成员授权</el-button></RouterLink><el-button type="primary" @click="open()">新建角色</el-button></div></div></template>
      <div class="filter-bar"><el-select v-model="scope" clearable placeholder="全部授权范围" aria-label="角色授权范围"><el-option v-for="(label, key) in scopeLabels" :key="key" :label="label" :value="key" /></el-select><el-button @click="load">刷新</el-button></div>
      <el-table :data="rows" empty-text="暂无角色">
        <el-table-column label="角色" min-width="170"><template #default="{ row }"><b>{{ row.name }}</b><p class="hint">{{ row.code }}</p></template></el-table-column>
        <el-table-column label="范围" width="110"><template #default="{ row }">{{ scopeLabels[row.scope_type] }}</template></el-table-column>
        <el-table-column label="权限" min-width="330"><template #default="{ row }"><span class="permission-list">{{ row.permissions.map((p: string) => catalog?.permissions[p] ?? p).join('、') }}</span></template></el-table-column>
        <el-table-column label="授权数" width="90" prop="binding_count" />
        <el-table-column label="状态" width="100"><template #default="{ row }"><el-tag :type="row.enabled ? 'success' : 'info'">{{ row.enabled ? '启用' : '停用' }}</el-tag></template></el-table-column>
        <el-table-column label="操作" width="145"><template #default="{ row }"><span v-if="row.builtin" class="hint">内置角色</span><template v-else><el-button link type="primary" @click="open(row)">编辑</el-button><el-button link type="danger" :disabled="row.binding_count > 0" @click="remove(row)">删除</el-button></template></template></el-table-column>
      </el-table>
    </el-card>
    <el-dialog v-model="visible" :title="editing ? '编辑角色' : '新建角色'" width="720px">
      <el-form label-position="top">
        <div class="form-grid"><el-form-item label="角色名称"><el-input v-model="form.name" maxlength="80" /></el-form-item><el-form-item label="角色标识"><el-input v-model="form.code" :disabled="!!editing" maxlength="48" placeholder="如 uat_reviewer" /></el-form-item></div>
        <el-form-item label="授权范围"><el-select v-model="form.scope_type" :disabled="!!editing" @change="form.permissions = []"><el-option v-for="(label, key) in scopeLabels" :key="key" :label="label" :value="key" /></el-select></el-form-item>
        <el-form-item label="权限"><el-checkbox-group v-model="form.permissions" class="permission-options"><el-checkbox v-for="permission in permissionOptions" :key="permission" :value="permission">{{ catalog?.permissions[permission] ?? permission }}</el-checkbox></el-checkbox-group></el-form-item>
        <el-form-item label="说明"><el-input v-model="form.description" type="textarea" maxlength="300" /></el-form-item>
        <el-form-item label="启用角色"><el-switch v-model="form.enabled" /><span class="hint">停用后已有授权立即失效</span></el-form-item>
      </el-form>
      <template #footer><el-button @click="visible = false">取消</el-button><el-button type="primary" :loading="busy" @click="save">保存</el-button></template>
    </el-dialog>
  </div>
</template>
<style scoped>
.permission-list { line-height: 1.8; font-size: 12px; }
.permission-options { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 4px 16px; }
@media (max-width: 600px) { .permission-options { grid-template-columns: 1fr; } }
</style>
