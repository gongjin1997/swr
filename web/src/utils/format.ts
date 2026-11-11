export function fmtSize(n: number | null | undefined): string {
  if (n === null || n === undefined) return '-'
  const units = ['B', 'KB', 'MB', 'GB']
  let value = Number(n)
  let i = 0
  while (value >= 1024 && i < units.length - 1) {
    value /= 1024
    i += 1
  }
  return i === 0 ? `${value} B` : `${value.toFixed(1)} ${units[i]}`
}

export function fmtTime(iso: string | null | undefined): string {
  if (!iso) return '-'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  const p = (x: number) => String(x).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
}

export function fmtDuration(start: string | null | undefined, end: string | null | undefined): string {
  if (!start) return '-'
  const finish = end ? new Date(end).getTime() : Date.now()
  const seconds = Math.max(0, Math.round((finish - new Date(start).getTime()) / 1000))
  return seconds >= 60 ? `${Math.floor(seconds / 60)} 分 ${seconds % 60} 秒` : `${seconds} 秒`
}

/** 只显示镜像地址最后两段（组织/仓库:标签）。 */
export function shortImage(ref: string | null | undefined): string {
  return (ref || '').split('/').slice(-2).join('/')
}

export async function copyText(text: string): Promise<void> {
  if (navigator.clipboard && window.isSecureContext) {
    await navigator.clipboard.writeText(text)
    return
  }
  const area = document.createElement('textarea')
  area.value = text
  area.style.position = 'fixed'
  area.style.opacity = '0'
  document.body.appendChild(area)
  area.select()
  try {
    if (!document.execCommand('copy')) throw new Error('复制失败，请手动复制')
  } finally {
    area.remove()
  }
}

/** ISO 时间 → datetime-local 输入框需要的本地时间字符串。 */
export function toLocalInput(iso: string | null | undefined): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  const p = (x: number) => String(x).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`
}
