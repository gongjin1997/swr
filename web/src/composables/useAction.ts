import { ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'

export function errorText(e: unknown): string {
  return e instanceof Error ? e.message : String(e)
}

export function notifyError(e: unknown): void {
  ElMessage({ type: 'error', message: errorText(e), duration: 6000, showClose: true })
}

export function notifySuccess(message: string): void {
  ElMessage({ type: 'success', message })
}

/** 包装异步操作：执行期间 busy 为 true，出错时提示，返回是否成功。 */
export function useAction() {
  const busy = ref(false)
  async function run(fn: () => Promise<unknown>, success?: string): Promise<boolean> {
    if (busy.value) return false
    busy.value = true
    try {
      await fn()
      if (success) notifySuccess(success)
      return true
    } catch (e) {
      notifyError(e)
      return false
    } finally {
      busy.value = false
    }
  }
  return { busy, run }
}

/** 二次确认；用户取消返回 false。 */
export async function confirmAction(message: string, title = '请确认', danger = false): Promise<boolean> {
  try {
    await ElMessageBox.confirm(message, title, {
      type: danger ? 'warning' : 'info',
      confirmButtonText: '确定',
      cancelButtonText: '取消',
      confirmButtonClass: danger ? 'el-button--danger' : undefined,
    })
    return true
  } catch {
    return false
  }
}
