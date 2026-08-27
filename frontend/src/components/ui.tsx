import type { ButtonHTMLAttributes, InputHTMLAttributes, ReactNode } from 'react'
import { AlertCircle, ArrowRight, LoaderCircle } from 'lucide-react'

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: 'primary' | 'quiet' | 'danger'
  busy?: boolean
}

export function Button({ variant = 'primary', busy = false, children, disabled, ...props }: ButtonProps) {
  return (
    <button className={`button button--${variant}`} disabled={disabled || busy} {...props}>
      {busy ? <LoaderCircle aria-hidden className="spin" size={17} /> : null}
      <span>{children}</span>
    </button>
  )
}

type FieldProps = InputHTMLAttributes<HTMLInputElement> & {
  label: string
  hint?: string
}

export function Field({ label, hint, id, ...props }: FieldProps) {
  const fieldId = id ?? props.name
  return (
    <label className="field" htmlFor={fieldId}>
      <span className="field__label">{label}</span>
      <input className="field__input" id={fieldId} {...props} />
      {hint ? <span className="field__hint">{hint}</span> : null}
    </label>
  )
}

export function Panel({ children, className = '' }: { children: ReactNode; className?: string }) {
  return <section className={`panel ${className}`.trim()}>{children}</section>
}

export function StatusPill({ children, tone = 'neutral' }: { children: ReactNode; tone?: 'neutral' | 'good' | 'warn' | 'signal' }) {
  return <span className={`status-pill status-pill--${tone}`}>{children}</span>
}

export function PageHeader({ eyebrow, title, description, action }: { eyebrow: string; title: string; description?: string; action?: ReactNode }) {
  return (
    <header className="page-header">
      <div>
        <p className="eyebrow">{eyebrow}</p>
        <h1>{title}</h1>
        {description ? <p className="page-header__description">{description}</p> : null}
      </div>
      {action ? <div className="page-header__action">{action}</div> : null}
    </header>
  )
}

export function LoadingState({ label = '正在同步状态' }: { label?: string }) {
  return (
    <div className="state-card" role="status">
      <LoaderCircle aria-hidden className="spin" />
      <p>{label}</p>
    </div>
  )
}

export function ErrorState({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="state-card state-card--error" role="alert">
      <AlertCircle aria-hidden />
      <div>
        <strong>这一步没有完成</strong>
        <p>{message}</p>
      </div>
      {onRetry ? (
        <Button variant="quiet" onClick={onRetry}>
          再试一次 <ArrowRight size={15} />
        </Button>
      ) : null}
    </div>
  )
}

export function EmptyState({ title, description, action }: { title: string; description: string; action?: ReactNode }) {
  return (
    <div className="empty-state">
      <div className="empty-state__orbit" aria-hidden><span /></div>
      <h2>{title}</h2>
      <p>{description}</p>
      {action}
    </div>
  )
}

