import { defineStore } from 'pinia'
import { ref, watch } from 'vue'

export type Theme = 'light' | 'dark' | 'system'
function read(key: string): string | null {
  try { return localStorage.getItem(key) } catch { return null }
}
function persist(key: string, value: string) {
  try { localStorage.setItem(key, value) } catch { /* 浏览器禁止存储时仍可使用当前会话设置。 */ }
}

export const usePreferences = defineStore('preferences', () => {
  const collapsed = ref(read('swr.ui.collapsed') === '1')
  const saved = read('swr.ui.theme')
  const theme = ref<Theme>(saved === 'dark' || saved === 'system' ? saved : 'light')
  const media = window.matchMedia('(prefers-color-scheme: dark)')
  const apply = () => document.documentElement.classList.toggle('dark', theme.value === 'dark' || (theme.value === 'system' && media.matches))
  watch(theme, (value) => { persist('swr.ui.theme', value); apply() }, { immediate: true })
  watch(collapsed, (value) => persist('swr.ui.collapsed', value ? '1' : '0'))
  media.addEventListener('change', apply)
  return { collapsed, theme }
})
