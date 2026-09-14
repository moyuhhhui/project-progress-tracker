export class ApiError extends Error {
  status: number
  constructor(message: string, status: number) { super(message); this.status = status }
}
export async function api<T>(path: string, method = 'GET', body?: unknown): Promise<T> {
  const prefix = import.meta.env.BASE_URL === '/' ? '' : import.meta.env.BASE_URL.replace(/\/$/, '')
  const apiPath = `${prefix}${path}`
  const controller = new AbortController()
  const timeout = window.setTimeout(() => controller.abort(), path === '/api/messages' ? 60_000 : 20_000)
  try {
    const response = await fetch(apiPath, {
      method, headers: { ...(method !== 'GET' ? { 'Content-Type': 'application/json' } : {}) },
      ...(method !== 'GET' ? { body: JSON.stringify(body ?? {}) } : {}), signal: controller.signal, cache: 'no-store',
    })
    const result = await response.json().catch(() => null)
    if (!response.ok) {
      const detail = result?.detail
      const message = Array.isArray(detail) ? detail.map((item: { loc?: unknown[]; msg?: string }) =>
        `${item.loc?.slice(1).join('.') || '字段'}：${item.msg || '校验失败'}`).join('；') :
        typeof detail === 'string' ? detail : `请求失败（${response.status}）`
      throw new ApiError(message, response.status)
    }
    return result as T
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError')
      throw new Error('请求超时，请检查草稿或刷新数据确认结果后再重试')
    throw error
  } finally { window.clearTimeout(timeout) }
}
