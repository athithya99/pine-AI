import axios, { AxiosError } from 'axios'

export const api = axios.create({ baseURL: '/api/v1' })

const ACCESS = 'ps_access'
const REFRESH = 'ps_refresh'

export const tokens = {
  get access() { try { return localStorage.getItem(ACCESS) } catch { return null } },
  get refresh() { try { return localStorage.getItem(REFRESH) } catch { return null } },
  set(access: string, refresh: string) {
    try { localStorage.setItem(ACCESS, access); localStorage.setItem(REFRESH, refresh) } catch { /* ignore */ }
  },
  clear() { try { localStorage.removeItem(ACCESS); localStorage.removeItem(REFRESH) } catch { /* ignore */ } },
}

api.interceptors.request.use((config) => {
  const t = tokens.access
  if (t) config.headers.Authorization = `Bearer ${t}`
  return config
})

let refreshing: Promise<string | null> | null = null

async function refreshAccess(): Promise<string | null> {
  const rt = tokens.refresh
  if (!rt) return null
  try {
    const { data } = await axios.post('/api/v1/auth/refresh', { refresh_token: rt })
    tokens.set(data.access_token, data.refresh_token)
    return data.access_token
  } catch {
    tokens.clear()
    return null
  }
}

api.interceptors.response.use(
  (r) => r,
  async (error: AxiosError) => {
    const original = error.config as (typeof error.config & { _retry?: boolean }) | undefined
    if (error.response?.status === 401 && original && !original._retry && !original.url?.includes('/auth/')) {
      original._retry = true
      refreshing ??= refreshAccess().finally(() => { refreshing = null })
      const t = await refreshing
      if (t) {
        original.headers.Authorization = `Bearer ${t}`
        return api(original)
      }
      window.location.href = '/login'
    }
    return Promise.reject(error)
  },
)

/** Turn any API error into one readable sentence. */
export function errorMessage(err: unknown): string {
  const e = err as AxiosError<{ detail?: unknown }>
  const detail = e.response?.data?.detail
  if (typeof detail === 'string') return detail
  if (detail && typeof detail === 'object' && 'message' in detail) return String((detail as { message: unknown }).message)
  if (Array.isArray(detail)) {
    return detail
      .map((d: { loc?: string[]; msg?: string }) => {
        const field = d.loc?.filter((p) => p !== 'body').join('.')
        const msg = (d.msg ?? 'Invalid value').replace(/^Value error, /, '')
        return field ? `${field}: ${msg}` : msg
      })
      .join('; ')
  }
  return e.message || 'Something went wrong'
}

/** Machine-readable code from a 409 "blocked" response, e.g. window_closed. */
export function errorCode(err: unknown): string | null {
  const detail = (err as AxiosError<{ detail?: { code?: string } }>).response?.data?.detail
  return detail && typeof detail === 'object' && 'code' in detail ? String(detail.code) : null
}

export async function download(path: string, filename: string) {
  const res = await api.get(path, { responseType: 'blob' })
  const url = URL.createObjectURL(res.data)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  URL.revokeObjectURL(url)
}
