import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { BookOpenCheck, Check, Clock3, Send, TrendingUp } from 'lucide-react'
import { useState } from 'react'
import { apiRequest, newIdempotencyKey } from '../../api/client'
import type { ChapterSession, KnowledgeUnlock, TrueExam, TrueExamDetail, TrueExamProfile } from '../../api/types'
import { Button, EmptyState, ErrorState, LoadingState, PageHeader, Panel, StatusPill } from '../../components/ui'

type Grade = { score: number; minutes: number; lookedAtSolution: boolean }

export function TrueExamsPage() {
  const queryClient = useQueryClient()
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [grades, setGrades] = useState<Record<string, Grade>>({})
  const [resultScore, setResultScore] = useState<number | null>(null)
  const [chapterSessionId, setChapterSessionId] = useState('')
  const exams = useQuery({ queryKey: ['true-exams'], queryFn: () => apiRequest<TrueExam[]>('/me/true-exams') })
  const activeExamId = selectedId ?? exams.data?.[0]?.id ?? null
  const detail = useQuery({
    queryKey: ['true-exam', activeExamId],
    queryFn: () => apiRequest<TrueExamDetail>(`/true-exams/${activeExamId}`),
    enabled: Boolean(activeExamId),
  })
  const profile = useQuery({ queryKey: ['true-exam-profile'], queryFn: () => apiRequest<TrueExamProfile[]>('/me/true-exam-profile') })
  const unlocks = useQuery({ queryKey: ['learning-unlocks'], queryFn: () => apiRequest<KnowledgeUnlock[]>('/me/learning-unlocks') })
  const chapterSession = useQuery({ queryKey: ['chapter-session', chapterSessionId], queryFn: () => apiRequest<ChapterSession>(`/true-exam/chapter-sessions/${chapterSessionId}`), enabled: Boolean(chapterSessionId) })
  const confirm = useMutation({
    mutationFn: (item: KnowledgeUnlock) => apiRequest(`/knowledge/${item.knowledge_id}/strengthening/confirm`, { method: 'POST', idempotencyKey: newIdempotencyKey(`strengthen-${item.knowledge_id}`), body: { expected_version: item.version } }),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['learning-unlocks'] }),
  })
  const startChapter = useMutation({
    mutationFn: (knowledgeId: string) => apiRequest<ChapterSession>(`/true-exam/chapter-sessions?knowledge_id=${knowledgeId}`, { method: 'POST', idempotencyKey: newIdempotencyKey(`chapter-session-${knowledgeId}`) }),
    onSuccess: (session) => setChapterSessionId(session.id),
  })
  const submitChapter = useMutation({
    mutationFn: async () => {
      if (!chapterSession.data?.questions) throw new Error('章节真题尚未加载')
      if (chapterSession.data.questions.some((question) => !grades[question.id])) {
        throw new Error('请记录该章全部真题的得分')
      }
      return apiRequest(`/true-exam/chapter-sessions/${chapterSession.data.id}/submit`, {
        method: 'POST',
        idempotencyKey: newIdempotencyKey(`chapter-${chapterSession.data.id}`),
        body: {
          results: chapterSession.data.questions.map((question) => ({
            question_id: question.id,
            score_ratio: grades[question.id]?.score ?? 0,
            duration_seconds: (grades[question.id]?.minutes ?? 1) * 60,
            looked_at_solution: grades[question.id]?.lookedAtSolution ?? false,
          })),
        },
      })
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['learning-unlocks'] })
      void queryClient.invalidateQueries({ queryKey: ['chapter-session', chapterSessionId] })
    },
  })

  const submit = useMutation({
    mutationFn: async () => {
      if (!detail.data) throw new Error('请选择一套真题')
      if (detail.data.questions.some((question) => !grades[question.id])) throw new Error('请完成每一道题的得分记录')
      return apiRequest<{ score: string }>(`/true-exams/${detail.data.id}/attempts`, {
        method: 'POST',
        idempotencyKey: newIdempotencyKey(`true-exam-${detail.data.id}`),
        body: {
          results: detail.data.questions.map((question) => ({
            question_id: question.id,
            score_ratio: grades[question.id]?.score ?? 0,
            duration_seconds: (grades[question.id]?.minutes ?? 1) * 60,
            looked_at_solution: grades[question.id]?.lookedAtSolution ?? false,
          })),
        },
      })
    },
    onSuccess: (response) => {
      setResultScore(Number(response.score))
      void queryClient.invalidateQueries({ queryKey: ['true-exam-profile'] })
      void queryClient.invalidateQueries({ queryKey: ['student-profile'] })
    },
  })

  function updateGrade(questionId: string, patch: Partial<Grade>) {
    setGrades((current) => ({ ...current, [questionId]: { score: 0, minutes: 15, lookedAtSolution: false, ...current[questionId], ...patch } }))
  }

  if (exams.isLoading) return <LoadingState label="载入目标院校真题" />
  if (exams.isError) return <ErrorState message="真题列表暂时无法读取" />

  return (
    <div className="page exam-page">
      <PageHeader eyebrow="True exam / 院校真题" title="把普通练习和真实考场，分开看。" description="真题表现单独形成画像，不会被日常练习的高正确率掩盖。" />
      <Panel className="chapter-unlocks">
        <div className="panel-heading"><div><p className="eyebrow">Knowledge progression</p><h2>按知识点解锁真题</h2></div></div>
        <div className="unlock-grid">
          {unlocks.data?.map((item) => {
            const learningDone = item.learning_task_total > 0 && item.learning_task_completed >= item.learning_task_total
            return <article key={item.knowledge_id}><div><strong>{item.knowledge_name}</strong><small>学习 {item.learning_task_completed}/{item.learning_task_total} · 真题 {item.true_exam_completed}/{item.true_exam_total}</small></div>{!item.true_exam_unlocked ? <Button variant="quiet" disabled={!learningDone} busy={confirm.isPending} onClick={() => confirm.mutate(item)}>{learningDone ? '确认强化完成' : '学习任务未完成'}</Button> : <Button variant="quiet" busy={startChapter.isPending} onClick={() => startChapter.mutate(item.knowledge_id)}>开始该章全部真题</Button>}<StatusPill tone={item.specialized_unlocked ? 'good' : item.true_exam_unlocked ? 'signal' : 'neutral'}>{item.specialized_unlocked ? '专项已解锁' : item.true_exam_unlocked ? '真题已解锁' : '锁定'}</StatusPill></article>
          })}
        </div>
      </Panel>
      {chapterSession.data ? <Panel className="chapter-session-banner"><BookOpenCheck /><div><strong>章节真题 Session</strong><p>{chapterSession.data.total_questions} 道当前版本的全部相关真题，完成后解锁专项强化。</p></div><StatusPill tone={chapterSession.data.status === 'COMPLETED' ? 'good' : 'signal'}>{chapterSession.data.status}</StatusPill></Panel> : null}
      {chapterSession.data?.questions?.length ? <section className="exam-paper chapter-paper"><header className="paper-header"><div><p className="eyebrow">Chapter true exam</p><h2>章节历年真题全集</h2></div><StatusPill tone="signal">{chapterSession.data.total_questions} 题</StatusPill></header>{chapterSession.data.questions.map((question) => <article className="paper-question" key={question.id}><header><span>{question.sequence}</span><div><StatusPill>{question.difficulty}</StatusPill><strong>{question.score} 分</strong></div></header><p>{question.content}</p><div className="paper-grade"><div>{[0, .5, 1].map((score) => <button key={score} className={grades[question.id]?.score === score ? 'active' : ''} onClick={() => updateGrade(question.id, { score })}>{Math.round(score * 100)}%{grades[question.id]?.score === score ? <Check size={13} /> : null}</button>)}</div><label><input type="number" min={1} value={grades[question.id]?.minutes ?? 15} onChange={(event) => updateGrade(question.id, { minutes: Number(event.target.value) })} /> min</label><label><input type="checkbox" checked={grades[question.id]?.lookedAtSolution ?? false} onChange={(event) => updateGrade(question.id, { lookedAtSolution: event.target.checked })} />看过答案</label></div></article>)}{chapterSession.data.status !== 'COMPLETED' ? <div className="paper-submit">{submitChapter.error ? <p className="inline-error">{submitChapter.error instanceof Error ? submitChapter.error.message : '提交失败'}</p> : null}<Button busy={submitChapter.isPending} onClick={() => submitChapter.mutate()}><Send size={16} />提交该章全部真题</Button></div> : null}</section> : null}
      <section className="exam-stats">
        <Panel><TrendingUp /><div><span>已记录考点</span><strong>{profile.data?.length ?? 0}</strong></div></Panel>
        <Panel><BookOpenCheck /><div><span>可用真题</span><strong>{exams.data?.length ?? 0}</strong></div></Panel>
        <Panel><Clock3 /><div><span>标准时长</span><strong>{detail.data?.duration_minutes ?? '—'}<small> min</small></strong></div></Panel>
      </section>
      <div className="exam-layout">
        <aside className="exam-list">
          {exams.data?.map((exam) => (
            <button key={exam.id} className={activeExamId === exam.id ? 'active' : ''} onClick={() => { setSelectedId(exam.id); setGrades({}); setResultScore(null) }}>
              <span>{exam.year}</span><div><strong>{exam.title}</strong><small>{exam.total_score} 分 · {exam.duration_minutes} 分钟</small></div>
            </button>
          ))}
        </aside>
        <section className="exam-paper">
          {detail.isLoading ? <LoadingState label="展开试卷" /> : null}
          {detail.data ? (
            <>
              <header className="paper-header"><div><p className="eyebrow">DEMO-801</p><h2>{detail.data.title}</h2></div><StatusPill tone="signal">{detail.data.total_score} / {detail.data.duration_minutes} min</StatusPill></header>
              {detail.data.questions.map((question) => (
                <article className="paper-question" key={question.id}>
                  <header><span>{question.sequence}</span><div><StatusPill>{question.difficulty}</StatusPill><strong>{question.score} 分</strong></div></header>
                  <p>{question.content}</p>
                  <div className="paper-grade">
                    <div role="group" aria-label={`第 ${question.sequence} 题得分`}>
                      {[0, .5, 1].map((score) => <button key={score} className={grades[question.id]?.score === score ? 'active' : ''} onClick={() => updateGrade(question.id, { score })}>{score === 0 ? '0%' : score === .5 ? '50%' : '100%'}{grades[question.id]?.score === score ? <Check size={13} /> : null}</button>)}
                    </div>
                    <label><input type="number" min={1} max={180} value={grades[question.id]?.minutes ?? 15} onChange={(event) => updateGrade(question.id, { minutes: Number(event.target.value) })} /> min</label>
                    <label><input type="checkbox" checked={grades[question.id]?.lookedAtSolution ?? false} onChange={(event) => updateGrade(question.id, { lookedAtSolution: event.target.checked })} />看过答案</label>
                  </div>
                </article>
              ))}
              {resultScore !== null ? <Panel className="score-result"><BookOpenCheck /><div><span>本次得分</span><strong>{resultScore} / {detail.data.total_score}</strong><p>真题画像已更新，可到模拟卷继续训练。</p></div></Panel> : <div className="paper-submit">{submit.error ? <p className="inline-error">{submit.error instanceof Error ? submit.error.message : '提交失败'}</p> : null}<Button busy={submit.isPending} onClick={() => submit.mutate()}><Send size={16} />提交整卷结果</Button></div>}
            </>
          ) : null}
        </section>
      </div>
      {profile.data?.length ? <Panel className="profile-strip"><div className="panel-heading"><div><p className="eyebrow">TrueExamErrorProfile</p><h2>真题薄弱点</h2></div></div><div>{profile.data.slice(0, 6).map((item) => <article key={item.knowledge_id}><span>{item.knowledge_id.slice(0, 6)}</span><strong>{Math.round(item.accuracy * 100)}%</strong><div><i style={{ width: `${item.accuracy * 100}%` }} /></div></article>)}</div></Panel> : <EmptyState title="画像还没有证据" description="完成一套真题后，这里会出现按知识点拆分的真实表现。" />}
    </div>
  )
}
