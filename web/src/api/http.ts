/**
 * 统一的 HTTP 客户端。
 * - 所有请求带 X-Requested-With: XMLHttpRequest（后端用它防 CSRF，缺少时写请求返回 403）
 * - 401：跳转登录页并带上当前地址；403 + PASSWORD_CHANGE_REQUIRED：跳转改密页
 * - 错误统一抛 ApiError，message 为可直接展示的中文
 */

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
    public code?: string,
    public detail?: unknown,
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

type Handler = () => void
const handlers: { unauthorized: Handler; passwordChange: Handler } = {
  unauthorized: () => {},
  passwordChange: () => {},
}

/** 由路由模块注册：401 与强制改密时如何跳转。 */
export function onAuthFailure(unauthorized: Handler, passwordChange: Handler): void {
  handlers.unauthorized = unauthorized
  handlers.passwordChange = passwordChange
}

function errorMessage(status: number, data: unknown): string {
  if (data && typeof data === 'object' && 'detail' in data) {
    const detail = (data as { detail: unknown }).detail
    if (typeof detail === 'string') return detail
    if (Array.isArray(detail)) {
      // FastAPI 422 校验错误
      return detail.map((d) => (d && typeof d === 'object' && 'msg' in d ? String(d.msg) : JSON.stringify(d))).join('；')
    }
  }
  return `请求失败（HTTP ${status}）`
}

function toError(status: number, data: unknown): ApiError {
  const code = data && typeof data === 'object' && 'code' in data ? String((data as { code: unknown }).code) : undefined
  const detail = data && typeof data === 'object' && 'detail' in data ? (data as { detail: unknown }).detail : undefined
  const err = new ApiError(status, errorMessage(status, data), code, detail)
  if (status === 401) handlers.unauthorized()
  if (code === 'PASSWORD_CHANGE_REQUIRED') handlers.passwordChange()
  return err
}

function parse(text: string): unknown {
  if (!text) return null
  try {
    return JSON.parse(text)
  } catch {
    return null
  }
}

export interface RequestOptions {
  method?: 'GET' | 'POST' | 'PUT' | 'DELETE'
  json?: unknown
  query?: Record<string, string | number | boolean | null | undefined>
  headers?: Record<string, string>
  signal?: AbortSignal
}

export function withQuery(path: string, query?: RequestOptions['query']): string {
  if (!query) return path
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(query)) {
    if (value !== undefined && value !== null && value !== '') params.set(key, String(value))
  }
  const qs = params.toString()
  return qs ? `${path}?${qs}` : path
}

export async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const headers: Record<string, string> = { 'X-Requested-With': 'XMLHttpRequest', ...options.headers }
  let body: string | undefined
  if (options.json !== undefined) {
    headers['Content-Type'] = 'application/json'
    body = JSON.stringify(options.json)
  }
  let response: Response
  try {
    response = await fetch(withQuery(path, options.query), {
      method: options.method ?? 'GET',
      headers,
      body,
      credentials: 'same-origin',
      signal: options.signal,
    })
  } catch (e) {
    if (e instanceof DOMException && e.name === 'AbortError') throw e
    throw new ApiError(0, '网络错误，无法连接平台')
  }
  const data = response.status === 204 ? null : parse(await response.text())
  if (!response.ok) throw toError(response.status, data)
  return data as T
}

export const http = {
  get: <T>(path: string, query?: RequestOptions['query']) => request<T>(path, { query }),
  post: <T>(path: string, json: unknown = {}, headers?: Record<string, string>) =>
    request<T>(path, { method: 'POST', json, headers }),
  put: <T>(path: string, json: unknown = {}) => request<T>(path, { method: 'PUT', json }),
  del: <T = null>(path: string) => request<T>(path, { method: 'DELETE' }),
}

/** multipart 上传（用 XHR 才能拿到上传进度）。 */
export function upload<T>(path: string, form: FormData, onProgress?: (ratio: number) => void): Promise<T> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    xhr.open('POST', path)
    xhr.setRequestHeader('X-Requested-With', 'XMLHttpRequest')
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable && onProgress) onProgress(e.loaded / e.total)
    }
    xhr.onload = () => {
      const data = parse(xhr.responseText)
      if (xhr.status >= 200 && xhr.status < 300) resolve(data as T)
      else reject(toError(xhr.status, data))
    }
    xhr.onerror = () => reject(new ApiError(0, '网络错误，上传失败'))
    xhr.send(form)
  })
}

/** 每次提交发布生成一个幂等键；同一次操作重试时复用。 */
export function idempotencyKey(): string {
  if (window.crypto?.randomUUID) return window.crypto.randomUUID()
  return `deploy-${Date.now()}-${Math.random().toString(16).slice(2)}`
}
