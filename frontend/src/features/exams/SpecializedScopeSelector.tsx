import type { SpecializedScope } from '../../api/types'
import { StatusPill } from '../../components/ui'

type SpecializedScopeSelectorProps = {
  scopes: SpecializedScope[]
  value: string
  onChange: (chapterId: string) => void
}

export function SpecializedScopeSelector({ scopes, value, onChange }: SpecializedScopeSelectorProps) {
  const selected = scopes.find((scope) => scope.chapter_id === value)

  return (
    <div className="specialized-scope-control">
      <label className="field">
        <span className="field__label">目标考纲章节</span>
        <select className="field__input" value={value} onChange={(event) => onChange(event.target.value)}>
          <option value="">选择院校真题考纲章节</option>
          {scopes.map((scope) => (
            <option key={scope.chapter_id} value={scope.chapter_id}>
              第 {scope.chapter_order} 章 · {scope.chapter_name}
            </option>
          ))}
        </select>
      </label>
      {selected ? <ScopeEvidence scope={selected} /> : null}
    </div>
  )
}

function ScopeEvidence({ scope }: { scope: SpecializedScope }) {
  const recommended = scope.weak_points.slice(0, 4)

  return (
    <section className="scope-evidence" aria-label={`${scope.chapter_name}章内薄弱知识点`}>
      <header>
        <div>
          <span>章内推荐依据</span>
          <strong>{scope.chapter_name}</strong>
        </div>
        <StatusPill tone={scope.specialized_unlocked ? 'good' : 'neutral'}>
          真题 {scope.true_exam_completed}/{scope.true_exam_total}
        </StatusPill>
      </header>
      <div className="scope-evidence__points">
        {recommended.map((point) => {
          const weakness = point.attempts ? Math.round((1 - point.accuracy) * 100) : 0
          return (
            <article key={point.knowledge_id}>
              <div>
                <strong>{point.knowledge_name}</strong>
                <span>{point.attempts ? `正确率 ${Math.round(point.accuracy * 100)}%` : '等待真题作答证据'}</span>
              </div>
              <i className={point.attempts ? '' : 'no-evidence'}>
                <b style={{ width: `${point.attempts ? Math.max(6, weakness) : 0}%` }} />
              </i>
            </article>
          )
        })}
      </div>
      <p>组卷时 60% 题量留在本章，并优先覆盖上面的细知识点；错误题和低正确率会提高推荐权重。</p>
    </section>
  )
}
