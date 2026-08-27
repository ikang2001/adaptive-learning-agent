import { clearTokens, getTokens, setTokens } from './tokenStore'
import type { AuthTokens, ProblemDetails } from './types'

const API_ROOT = '/api/v1'
let refreshInFlight: Promise<AuthTokens> | null = null

type RequestOptions = Omit<RequestInit, 'body'> & {
  body?: unknown
  authenticated?: boolean
  idempotencyKey?: string
}

export class ApiError extends Error {
  readonly status: number
  readonly code: string

  constructor(status: number, problem: ProblemDetails) {
    super(problem.detail ?? '请求未完成，请稍后重试')
    this.name = 'ApiError'
    this.status = status
    this.code = problem.title ?? 'UNKNOWN_ERROR'
  }
}

async function refreshAccessToken(): Promise<AuthTokens> {
  const refreshToken = getTokens()?.refresh_token
  if (!refreshToken) throw new ApiError(401, { detail: '登录状态已失效' })
  const response = await fetch(`${API_ROOT}/auth/token/refresh`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ refresh_token: refreshToken }),
  })
  if (!response.ok) {
    clearTokens()
    throw new ApiError(response.status, await readProblem(response))
  }
  const tokens = (await response.json()) as AuthTokens
  setTokens(tokens)
  return tokens
}

async function readProblem(response: Response): Promise<ProblemDetails> {
  try {
    return (await response.json()) as ProblemDetails
  } catch {
    return { status: response.status, detail: response.statusText }
  }
}

export async function apiRequest<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { body, authenticated = true, idempotencyKey, headers, ...requestInit } = options
  const requestHeaders = new Headers(headers)
  if (body !== undefined) requestHeaders.set('Content-Type', 'application/json')
  if (idempotencyKey) requestHeaders.set('Idempotency-Key', idempotencyKey)
  const accessToken = getTokens()?.access_token
  if (authenticated && accessToken) requestHeaders.set('Authorization', `Bearer ${accessToken}`)

  const response = await fetch(`${API_ROOT}${path}`, {
    ...requestInit,
    headers: requestHeaders,
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  if (response.status === 401 && authenticated && getTokens()?.refresh_token) {
    refreshInFlight ??= refreshAccessToken().finally(() => {
      refreshInFlight = null
    })
    await refreshInFlight
    return apiRequest<T>(path, options)
  }
  if (!response.ok) throw new ApiError(response.status, await readProblem(response))
  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}

export function newIdempotencyKey(prefix: string): string {
  return `${prefix}-${crypto.randomUUID()}`
}

