import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { authApi } from '@/api'
import type { SessionProfile } from '@/api/types'

/**
 * 当前用户与权限。前端只用权限控制显示，后端会再次校验。
 *
 * - can(p)：在任一范围内拥有，用于菜单和页面入口
 * - canGlobal(p)：全局拥有
 * - canProject(id, p) / canNamespace(id, p)：对具体资源的操作按钮用这两个
 */
export const useSession = defineStore('session', () => {
  const profile = ref<SessionProfile | null>(null)
  const siteName = ref('SWR 镜像构建平台')

  async function loadSiteName() {
    try {
      siteName.value = (await authApi.config()).site_name || siteName.value
    } catch {
      /* 保留默认名称 */
    }
  }

  const permissions = computed(() => new Set(profile.value?.permissions ?? []))
  const globalPermissions = computed(() => new Set(profile.value?.global_permissions ?? []))
  const projectPermissions = computed(() => new Map((profile.value?.projects ?? []).map((p) => [p.id, new Set(p.perms)])))
  const namespacePermissions = computed(() => new Map((profile.value?.namespaces ?? []).map((n) => [n.id, new Set(n.perms)])))

  const can = (permission: string) => permissions.value.has(permission)
  const canAny = (...list: string[]) => list.some(can)
  const canGlobal = (permission: string) => globalPermissions.value.has(permission)
  const canProject = (id: number | null | undefined, permission: string) =>
    canGlobal(permission) || (id != null && !!projectPermissions.value.get(id)?.has(permission))
  const canNamespace = (id: number | null | undefined, permission: string) =>
    canGlobal(permission) || (id != null && !!namespacePermissions.value.get(id)?.has(permission))
  /** 拥有该权限的项目（用于"新建应用""选择要构建的应用"等下拉框） */
  const projectsWith = (permission: string) => (profile.value?.projects ?? []).filter((p) => canProject(p.id, permission))
  const projectName = (id: number | null | undefined) =>
    profile.value?.projects.find((p) => p.id === id)?.name ?? (id != null ? `项目 #${id}` : '-')

  const isPlatformAdmin = computed(() => !!profile.value?.is_platform_admin)
  const mustChangePassword = computed(() => !!profile.value?.must_change_password)
  const deployEnabled = computed(() => !!profile.value?.deploy_enabled)
  const userId = computed(() => profile.value?.id ?? null)

  async function load(): Promise<SessionProfile> {
    profile.value = await authApi.me()
    siteName.value = profile.value.site_name || siteName.value
    return profile.value
  }

  function clear(): void {
    profile.value = null
  }

  async function logout(): Promise<void> {
    try {
      await authApi.logout()
    } finally {
      clear()
    }
  }

  return {
    profile, siteName, loadSiteName, can, canAny, canGlobal, canProject, canNamespace, projectsWith, projectName,
    isPlatformAdmin, mustChangePassword, deployEnabled, userId, load, clear, logout,
  }
})
