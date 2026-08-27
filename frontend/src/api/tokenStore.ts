import type { AuthTokens } from './types'

const STORAGE_KEY = 'closed-loop.auth'
const AUTH_EVENT = 'closed-loop:auth-changed'

export function getTokens(): AuthTokens | null {
  const raw = window.localStorage.getItem(STORAGE_KEY)
  if (!raw) return null
  try {
    return JSON.parse(raw) as AuthTokens
  } catch {
    window.localStorage.removeItem(STORAGE_KEY)
    return null
  }
}

export function setTokens(tokens: AuthTokens): void {
  window.localStorage.setItem(STORAGE_KEY, JSON.stringify(tokens))
  window.dispatchEvent(new Event(AUTH_EVENT))
}

export function clearTokens(): void {
  window.localStorage.removeItem(STORAGE_KEY)
  window.dispatchEvent(new Event(AUTH_EVENT))
}

export function onAuthChanged(listener: () => void): () => void {
  window.addEventListener(AUTH_EVENT, listener)
  return () => window.removeEventListener(AUTH_EVENT, listener)
}

export function getTokenRoles(): string[] {
  const accessToken = getTokens()?.access_token
  if (!accessToken) return []
  try {
    const payloadSegment = accessToken.split('.')[1]
    if (!payloadSegment) return []
    const normalized = payloadSegment.replace(/-/g, '+').replace(/_/g, '/')
    const payload = JSON.parse(window.atob(normalized)) as { roles?: unknown }
    return Array.isArray(payload.roles)
      ? payload.roles.filter((role): role is string => typeof role === 'string')
      : []
  } catch {
    return []
  }
}

