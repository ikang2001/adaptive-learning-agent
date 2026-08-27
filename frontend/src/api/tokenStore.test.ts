import { beforeEach, describe, expect, it } from 'vitest'
import { clearTokens, getTokens, setTokens } from './tokenStore'

describe('token store', () => {
  beforeEach(() => window.localStorage.clear())

  it('stores and clears the current token pair', () => {
    const tokens = { access_token: 'access', refresh_token: 'refresh', token_type: 'bearer', expires_in: 900 }
    setTokens(tokens)
    expect(getTokens()).toEqual(tokens)
    clearTokens()
    expect(getTokens()).toBeNull()
  })
})

