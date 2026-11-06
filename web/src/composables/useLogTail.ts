import { onBeforeUnmount, ref, watch, type Ref } from 'vue'
import type { LogChunk } from '@/api/types'

export interface LogTailOptions {
  /** 读取 offset 之后的日志 */
  fetch: (offset: number) => Promise<LogChunk>
  /** 每次拿到日志后调用（状态或步骤可能变化） */
  onChunk?: (chunk: LogChunk) => void | Promise<void>
  onFinish?: (chunk: LogChunk) => void
  interval?: number
}

const MAX_LINES = 20000

/**
 * 按 offset 增量轮询日志：默认 1 秒一次，单次超过 200 KB 立即续读，出错退避到 3 秒，finished 后停止。
 * 组件卸载或 key 变化时自动停止。
 */
export function useLogTail(key: Ref<number | null>, options: LogTailOptions) {
  const lines = ref<string[]>([])
  const truncated = ref(false)
  const finished = ref(false)
  const error = ref('')
  let offset = 0
  let timer: ReturnType<typeof setTimeout> | null = null
  let generation = 0

  function stop(): void {
    generation += 1
    if (timer) clearTimeout(timer)
    timer = null
  }

  function append(text: string): void {
    if (!text) return
    const incoming = text.replace(/\n$/, '').split('\n')
    const merged = lines.value.concat(incoming)
    if (merged.length > MAX_LINES) {
      truncated.value = true
      lines.value = merged.slice(merged.length - MAX_LINES)
    } else {
      lines.value = merged
    }
  }

  async function poll(current: number, id: number): Promise<void> {
    if (current !== generation) return
    let delay = options.interval ?? 1000
    try {
      const chunk = await options.fetch(offset)
      if (current !== generation || key.value !== id) return
      append(chunk.text)
      offset = chunk.offset
      error.value = ''
      await options.onChunk?.(chunk)
      if (chunk.finished) {
        finished.value = true
        options.onFinish?.(chunk)
        return
      }
      if (chunk.text.length > 200000) delay = 50
    } catch (e) {
      error.value = e instanceof Error ? e.message : String(e)
      delay = 3000
    }
    if (current === generation) timer = setTimeout(() => poll(current, id), delay)
  }

  function start(id: number | null): void {
    stop()
    lines.value = []
    truncated.value = false
    finished.value = false
    error.value = ''
    offset = 0
    if (id !== null) void poll(generation, id)
  }

  watch(key, (id) => start(id), { immediate: true })
  onBeforeUnmount(stop)

  return { lines, truncated, finished, error, restart: () => start(key.value), stop }
}
