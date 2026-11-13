import { computed, ref, watch, type ComputedRef, type Ref } from 'vue'

export interface Pagination<T> {
  page: Ref<number>
  size: Ref<number>
  total: ComputedRef<number>
  rows: ComputedRef<T[]>
}

export const PAGE_SIZES = [10, 20, 50, 100]

/**
 * 前端分页：source 返回完整列表（通常是筛选后的结果）。
 * resetOn 返回的筛选条件变化时回到第 1 页；数据变少导致当前页越界时自动退到最后一页。
 */
export function usePagination<T>(source: () => T[], options: { size?: number; resetOn?: () => unknown } = {}): Pagination<T> {
  const page = ref(1)
  const size = ref(options.size ?? 20)
  const total = computed(() => source().length)
  const rows = computed(() => source().slice((page.value - 1) * size.value, page.value * size.value))
  watch([total, size], () => {
    const last = Math.max(1, Math.ceil(total.value / size.value))
    if (page.value > last) page.value = last
  })
  if (options.resetOn) watch(options.resetOn, () => { page.value = 1 }, { deep: true })
  return { page, size, total, rows }
}

/** 后端分页的页码与每页条数：offset 由页码计算。 */
export function useServerPage(size = 20) {
  const page = ref(1)
  const pageSize = ref(size)
  const total = ref(0)
  const offset = computed(() => (page.value - 1) * pageSize.value)
  return { page, pageSize, total, offset }
}
