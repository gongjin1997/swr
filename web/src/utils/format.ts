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

/** 镜像摘要缩写：sha256:abcdef123456… */
export function shortDigest(ref: string | null | undefined): string {
  const value = ref || ''
  const index = value.indexOf('sha256:')
  return index < 0 ? value : `${value.slice(index, index + 19)}…`
}

/** 与 kubectl 的 AGE 一致的时长：45 秒、12 分钟、3 小时 5 分、2 天 4 小时。 */
export function fmtAge(iso: string | null | undefined, now: number = Date.now()): string {
  if (!iso) return '-'
  const time = new Date(iso).getTime()
  if (Number.isNaN(time)) return '-'
  const seconds = Math.max(0, Math.floor((now - time) / 1000))
  if (seconds < 60) return `${seconds} 秒`
  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return `${minutes} 分钟`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return minutes % 60 ? `${hours} 小时 ${minutes % 60} 分` : `${hours} 小时`
  const days = Math.floor(hours / 24)
  return hours % 24 ? `${days} 天 ${hours % 24} 小时` : `${days} 天`
}

/** 相对时间：3 分钟前。 */
export function fmtAgo(iso: string | null | undefined, now: number = Date.now()): string {
  const age = fmtAge(iso, now)
  return age === '-' ? '-' : `${age}前`
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

/** 读取文件为 Base64（不含 data: 前缀），用于上传证书与密钥文件。 */
export function readFileBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onerror = () => reject(reader.error ?? new Error(`读取 ${file.name} 失败`))
    reader.onload = () => {
      const result = String(reader.result ?? '')
      resolve(result.slice(result.indexOf(',') + 1))
    }
    reader.readAsDataURL(file)
  })
}

/** 把接口返回的 Base64 内容保存为本地文件。 */
export function downloadBase64(filename: string, base64: string): void {
  const bytes = Uint8Array.from(atob(base64), (c) => c.charCodeAt(0))
  const url = URL.createObjectURL(new Blob([bytes]))
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  link.click()
  URL.revokeObjectURL(url)
}
