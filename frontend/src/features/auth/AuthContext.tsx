import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'
import { apiRequest } from '../../api/client'
import { clearTokens, getTokenRoles, getTokens, onAuthChanged, setTokens } from '../../api/tokenStore'
import type { AuthTokens } from '../../api/types'

type AuthContextValue = {
  authenticated: boolean
  roles: string[]
  login: (tokens: AuthTokens) => void
  logout: () => Promise<void>
}

const AuthContext = createContext<AuthContextValue | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [tokens, setCurrentTokens] = useState<AuthTokens | null>(() => getTokens())

  useEffect(() => onAuthChanged(() => setCurrentTokens(getTokens())), [])

  const login = useCallback((nextTokens: AuthTokens) => {
    setTokens(nextTokens)
    setCurrentTokens(nextTokens)
  }, [])

  const logout = useCallback(async () => {
    const refreshToken = getTokens()?.refresh_token
    if (refreshToken) {
      try {
        await apiRequest<void>('/auth/sessions/current', {
          method: 'DELETE',
          body: { refresh_token: refreshToken },
          authenticated: false,
        })
      } finally {
        clearTokens()
        setCurrentTokens(null)
      }
    } else {
      clearTokens()
      setCurrentTokens(null)
    }
  }, [])

  const value = useMemo(
    () => ({ authenticated: Boolean(tokens), roles: getTokenRoles(), login, logout }),
    [tokens, login, logout],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

// This hook shares the provider's public contract by design.
// eslint-disable-next-line react-refresh/only-export-components
export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext)
  if (!context) throw new Error('useAuth must be used inside AuthProvider')
  return context
}
