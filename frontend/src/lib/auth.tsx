import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from 'react'
import { api, tokens } from './api'
import type { Me } from './types'

interface AuthState {
  me: Me | null
  loading: boolean
  loginWithPassword: (email: string, password: string) => Promise<void>
  requestOtp: (mobile: string) => Promise<void>
  verifyOtp: (mobile: string, code: string) => Promise<void>
  logout: () => void
  reload: () => Promise<void>
}

const AuthContext = createContext<AuthState | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<Me | null>(null)
  const [loading, setLoading] = useState(true)

  const reload = useCallback(async () => {
    if (!tokens.access) { setMe(null); setLoading(false); return }
    try {
      const { data } = await api.get<Me>('/auth/me')
      setMe(data)
    } catch {
      setMe(null)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { reload() }, [reload])

  const store = async (p: Promise<{ data: { access_token: string; refresh_token: string } }>) => {
    const { data } = await p
    tokens.set(data.access_token, data.refresh_token)
    await reload()
  }

  const value: AuthState = {
    me,
    loading,
    loginWithPassword: (email, password) => store(api.post('/auth/login', { email, password })),
    requestOtp: async (mobile) => { await api.post('/auth/otp/request', { mobile }) },
    verifyOtp: (mobile, code) => store(api.post('/auth/otp/verify', { mobile, code })),
    logout: () => { tokens.clear(); setMe(null) },
    reload,
  }
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth() {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used inside AuthProvider')
  return ctx
}
