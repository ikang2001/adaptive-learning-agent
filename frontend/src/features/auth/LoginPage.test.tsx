import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import { AuthProvider } from './AuthContext'
import { LoginPage } from './LoginPage'

describe('LoginPage', () => {
  it('shows the local demo credentials and clear entry action', () => {
    render(
      <MemoryRouter>
        <AuthProvider>
          <LoginPage />
        </AuthProvider>
      </MemoryRouter>,
    )

    expect(screen.getByRole('heading', { name: '手机号登录' })).toBeInTheDocument()
    expect(screen.getByLabelText('手机号')).toHaveValue('+8613800138000')
    expect(screen.getByLabelText('验证码')).toHaveValue('246810')
    expect(screen.getByRole('button', { name: /进入复习台/ })).toBeEnabled()
  })
})

