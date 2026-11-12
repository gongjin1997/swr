import { onBeforeUnmount, ref, type Ref } from 'vue'

/** 媒体查询是否匹配，窗口大小变化时更新（例如按宽度调整描述列表的列数）。 */
export function useMediaQuery(query: string): Ref<boolean> {
  const media = window.matchMedia(query)
  const matches = ref(media.matches)
  const update = (event: MediaQueryListEvent) => { matches.value = event.matches }
  media.addEventListener('change', update)
  onBeforeUnmount(() => media.removeEventListener('change', update))
  return matches
}
