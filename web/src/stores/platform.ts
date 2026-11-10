import { defineStore } from 'pinia'
import { ref } from 'vue'
import { appApi, platformApi } from '@/api'
import type { App, AppFields, PlatformInfo } from '@/api/types'

/** 平台信息（SWR / Docker 状态）与应用列表缓存：构建页、构建记录、服务表单共用。 */
export const usePlatform = defineStore('platform', () => {
  const info = ref<PlatformInfo | null>(null)
  const apps = ref<App[]>([])
  const appDefaults = ref<AppFields | null>(null)
  const appsLoaded = ref(false)

  async function loadInfo(): Promise<void> {
    info.value = await platformApi.info()
  }

  async function loadApps(force = false): Promise<App[]> {
    if (appsLoaded.value && !force) return apps.value
    apps.value = (await appApi.list()).items
    appsLoaded.value = true
    return apps.value
  }

  async function loadAppDefaults(): Promise<AppFields> {
    if (!appDefaults.value) appDefaults.value = await appApi.defaults()
    return appDefaults.value
  }

  function reset() {
    info.value = null
    apps.value = []
    appDefaults.value = null
    appsLoaded.value = false
  }

  const appName = (id: number) => apps.value.find((a) => a.id === id)?.name ?? `#${id}`

  return { reset, info, apps, appDefaults, loadInfo, loadApps, loadAppDefaults, appName }
})
