import { onBeforeUnmount } from 'vue'

/**
 * 列表自动刷新：refresh 返回 true 表示仍有进行中的任务，interval 毫秒后再刷新一次。
 * 每次调用 run() 都会作废之前的请求结果；组件卸载后自动停止。
 */
export function usePolling(refresh: (isCurrent: () => boolean) => Promise<boolean>, interval = 4000) {
  let timer: ReturnType<typeof setTimeout> | null = null
  let generation = 0
  let disposed = false

  function stop(): void {
    generation += 1
    if (timer) clearTimeout(timer)
    timer = null
  }

  async function run(): Promise<void> {
    stop()
    const current = generation
    const isCurrent = () => !disposed && current === generation
    let again = false
    try {
      again = await refresh(isCurrent)
    } finally {
      if (again && isCurrent()) timer = setTimeout(() => void run(), interval)
    }
  }

  onBeforeUnmount(() => {
    disposed = true
    stop()
  })

  return { run, stop }
}
