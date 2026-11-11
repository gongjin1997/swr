import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { roleApi } from '@/api'
import type { PermissionMeta, RoleCatalog, RoleDefinition, ScopeType } from '@/api/types'

/** 角色与权限目录（来自后端 access/catalog.py），角色、授权与账号页面共用。 */
export const useCatalog = defineStore('catalog', () => {
  const data = ref<RoleCatalog | null>(null)
  let pending: Promise<RoleCatalog> | null = null

  async function load(force = false): Promise<RoleCatalog> {
    if (data.value && !force) return data.value
    if (!pending || force) {
      pending = roleApi.catalog().then((catalog) => (data.value = catalog)).finally(() => (pending = null))
    }
    return pending
  }

  const roles = computed<RoleDefinition[]>(() => data.value?.items ?? [])
  const roleMap = computed(() => new Map(roles.value.map((r) => [r.code, r])))
  const permissionMap = computed(() => new Map((data.value?.permission_catalog ?? []).map((p) => [p.code, p])))

  const roleName = (code: string) => roleMap.value.get(code)?.name ?? code
  const permissionLabel = (code: string) => permissionMap.value.get(code)?.label ?? data.value?.permissions[code] ?? code
  const scopeLabel = (scope: string) => data.value?.scopes.find((s) => s.code === scope)?.label ?? scope
  const rolesFor = (scope: ScopeType, enabledOnly = true) =>
    roles.value.filter((r) => r.scope_type === scope && (!enabledOnly || r.enabled))

  /** 按权限分组排列（只含该范围可授予的权限），供角色编辑与权限展示使用。 */
  function grouped(codes: string[]): { code: string; label: string; items: PermissionMeta[] }[] {
    const wanted = new Set(codes)
    return (data.value?.groups ?? [])
      .map((g) => ({ ...g, items: (data.value?.permission_catalog ?? []).filter((p) => p.group === g.code && wanted.has(p.code)) }))
      .filter((g) => g.items.length)
  }

  /** 与后端 access.catalog.expand 相同：补齐隐含权限与范围的查看权限。仅用于预览，最终以后端为准。 */
  function expand(codes: string[], scope: ScopeType): string[] {
    const base: Record<ScopeType, string[]> = { global: [], project: ['project.view'], namespace: ['namespace.view'] }
    const result = new Set<string>()
    const pending = [...codes]
    while (pending.length) {
      const code = pending.pop() as string
      const meta = permissionMap.value.get(code)
      if (!meta || result.has(code) || !meta.scopes.includes(scope)) continue
      result.add(code)
      pending.push(...meta.implies)
      if (!pending.length && base[scope].every((b) => !result.has(b))) pending.push(...base[scope])
    }
    return [...result]
  }

  return { data, load, roles, roleMap, roleName, permissionLabel, scopeLabel, rolesFor, grouped, expand }
})
