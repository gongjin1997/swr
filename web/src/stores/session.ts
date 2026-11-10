import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { authApi } from '@/api'
import type { SessionProfile } from '@/api/types'

/** 当前登录用户与权限。前端只用权限控制显示，后端会再次校验。 */
export const useSession = defineStore('session', () => {
  const profile = ref<SessionProfile | null>(null)
  const siteName = ref('SWR 镜像构建平台')
  async function loadSiteName() {
    try { siteName.value = (await authApi.config()).site_name || siteName.value } catch { /* keep the default name */ }
  }

  const permissions = computed(() => new Set(profile.value?.permissions ?? []))
  const can = (permission: string) => permissions.value.has(permission)
  const canAny = (...list: string[]) => list.some(can)
  const canNamespace = (id: number, permission: string) => can('platform.admin') ||
    !!profile.value?.namespaces.find(n => n.id === id)?.perms.includes(permission)
  const mustChangePassword = computed(() => !!profile.value?.must_change_password)
  const deployEnabled = computed(() => !!profile.value?.deploy_enabled)

  async function load(): Promise<SessionProfile> {
    profile.value = await authApi.me()
    siteName.value = profile.value.site_name || siteName.value
    return profile.value
  }

  function clear(): void {
    profile.value = null
  }

  async function logout(): Promise<void> {
    await authApi.logout()
    clear()
  }

  return { profile, siteName, loadSiteName, can, canAny, canNamespace, mustChangePassword, deployEnabled, load, clear, logout }
})
