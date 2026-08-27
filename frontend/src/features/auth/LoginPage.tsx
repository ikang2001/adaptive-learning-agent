import { useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { Activity, ArrowRight, CheckCircle2, RadioTower } from 'lucide-react'
import { ApiError, apiRequest } from '../../api/client'
import type { AuthTokens } from '../../api/types'
import { Button, Field } from '../../components/ui'
import { useAuth } from './AuthContext'

export function LoginPage() {
  const navigate = useNavigate()
  const { login } = useAuth()
  const [phone, setPhone] = useState('+8613800138000')
  const [code, setCode] = useState('246810')
  const [codeRequested, setCodeRequested] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  async function requestCode() {
    setBusy(true)
    setError('')
    try {
      await apiRequest('/auth/sms-codes', {
        method: 'POST',
        body: { phone, purpose: 'LOGIN' },
        authenticated: false,
      })
      setCodeRequested(true)
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : '验证码请求失败')
    } finally {
      setBusy(false)
    }
  }

  async function submit(event: FormEvent) {
    event.preventDefault()
    setBusy(true)
    setError('')
    try {
      const tokens = await apiRequest<AuthTokens>('/auth/sessions', {
        method: 'POST',
        body: { phone, code },
        authenticated: false,
      })
      login(tokens)
      void navigate('/', { replace: true })
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : '登录没有完成')
    } finally {
      setBusy(false)
    }
  }

  return (
    <main className="login-page">
      <section className="login-visual" aria-label="产品介绍">
        <div className="brand-lockup">
          <span className="brand-mark"><Activity size={21} /></span>
          <span>闭环</span>
        </div>
        <div className="login-visual__copy">
          <p className="eyebrow">Adaptive control learning</p>
          <h1>让每一次练习，<br />都改变下一步。</h1>
          <p>围绕你的院校目标与真实反馈，持续诊断、选题、调整计划。</p>
        </div>
        <div className="signal-board" aria-hidden>
          <svg viewBox="0 0 720 210" role="presentation">
            <path className="signal-board__grid" d="M0 35H720M0 70H720M0 105H720M0 140H720M0 175H720M90 0V210M180 0V210M270 0V210M360 0V210M450 0V210M540 0V210M630 0V210" />
            <path className="signal-board__line" d="M0 146 C35 146 42 42 86 42 S135 166 176 166 S222 82 264 82 S308 130 350 130 S398 57 442 57 S491 152 536 152 S582 102 625 102 S670 118 720 88" />
          </svg>
          <div className="signal-board__labels"><span>输入</span><span>反馈</span><span>收敛</span></div>
        </div>
      </section>

      <section className="login-panel">
        <div className="login-card">
          <div className="login-card__icon"><RadioTower /></div>
          <p className="eyebrow">进入学习回路</p>
          <h2>手机号登录</h2>
          <p className="muted">体验环境使用固定验证码，不会发送真实短信。</p>
          <form onSubmit={(event) => void submit(event)}>
            <Field label="手机号" value={phone} onChange={(event) => setPhone(event.target.value)} autoComplete="tel" />
            <div className="code-row">
              <Field label="验证码" value={code} onChange={(event) => setCode(event.target.value)} inputMode="numeric" maxLength={6} />
              <Button type="button" variant="quiet" busy={busy} onClick={() => void requestCode()}>
                获取验证码
              </Button>
            </div>
            {codeRequested ? <p className="inline-success"><CheckCircle2 size={15} />验证码已就绪：246810</p> : null}
            {error ? <p className="inline-error">{error}</p> : null}
            <Button type="submit" busy={busy} className="button--wide">
              进入复习台 <ArrowRight size={17} />
            </Button>
          </form>
        </div>
      </section>
    </main>
  )
}
