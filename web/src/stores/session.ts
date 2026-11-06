import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { authApi } from '@/api'
import type { SessionProfile } from '@/api/types'

/** 当前登录用户与权限。前端只用权限控制显示，后端会再次校验。 */
export const useSession = defineStore('session', () => {
  const profile = ref<SessionProfile | null>(null)
  const siteName = ref('SWR 镜像构建平台')

  const permissions = computed(() => new Set(profile.value?.permissions ?? []))
  const can = (permission: string) => permissions.value.has(permission)
  const canAny = (...list: string[]) => list.some(can)
  const mustChangePassword = computed(() => !!profile.value?.must_change_password)
  const deployEnabled = computed(() => !!profile.value?.deploy_enabled)

  async function load(): Promise<SessionProfile> {
    profile.value = await authApi.me()
    siteName.value = profile.value.site_name
    document.title = profile.value.site_name
    return profile.value
  }

  async function loadSiteName(): Promise<void> {
    try {
      const config = await authApi.config()
      siteName.value = config.site_name
    } catch {
      /* 登录页拿不到站点名时使用默认值 */
    }
  }

  function clear(): void {
    profile.value = null
  }

  async function logout(): Promise<void> {
    await authApi.logout()
    clear()
  }

  return { profile, siteName, can, canAny, mustChangePassword, deployEnabled, load, loadSiteName, clear, logout }
})
