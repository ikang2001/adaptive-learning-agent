import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Check, FileStack, LoaderCircle, Send, Sparkles } from 'lucide-react'
import { useEffect, useState } from 'react'
import { ApiError, apiRequest, newIdempotencyKey } from '../../api/client'
import { useJobPolling } from '../../api/useJobPolling'
import type { BackgroundJob, KnowledgeUnlock, MockExam, SpecializedScope, StudentProfile } from '../../api/types'
import { Button, EmptyState, ErrorState, LoadingState, PageHeader, Panel, StatusPill } from '../../components/ui'
import { isMockJobRunning } from './mockJobState'
import { SpecializedScopeSelector } from './SpecializedScopeSelector'

type Grade = { score: number; minutes: number; lookedAtSolution: boolean }

export function MockExamsPage() {
  const queryClient = useQueryClient()
  const [mockType, setMockType] = useState<'FULL' | 'SPECIALIZED'>('FULL')
  const [targetKnowledgeId, setTargetKnowledgeId] = useState('')
  const [jobId, setJobId] = useState<string | null>(null)
  const [storedMockId, setStoredMockId] = useState(() => window.localStorage.getItem('closed-loop.last-mock') ?? '')
  const [grades, setGrades] = useState<Record<string, Grade>>({})
  const [score, setScore] = useState<number | null>(null)
  const profile = useQuery({ queryKey: ['student-profile'], queryFn: () => apiRequest<StudentProfile>('/me/student-profile') })
  const scopes = useQuery({
    queryKey: ['specialized-scopes', profile.data?.target_school_id],
    queryFn: () => apiRequest<SpecializedScope[]>('/me/specialized-scopes'),
    enabled: Boolean(profile.data?.target_school_id),
  })
  const unlocks = useQuery({ queryKey: ['learning-unlocks'], queryFn: () => apiRequest<KnowledgeUnlock[]>('/me/learning-unlocks') })
  const job = useJobPolling(jobId)
  const jobMockId = job.data?.result?.mock_exam_id
  const mockId = typeof jobMockId === 'string' ? jobMockId : storedMockId
  const mock = useQuery({
    queryKey: ['mock-exam', mockId],
    queryFn: () => apiRequest<MockExam>(`/mock-exams/${mockId}`),
    enabled: Boolean(mockId),
    refetchInterval: (query) => query.state.data?.status === 'WAITING_FOR_REVIEW' ? 3_000 : false,
  })
  const create = useMutation({
    mutationFn: () => apiRequest<BackgroundJob>('/me/mock-exams', {
      method: 'POST',
      idempotencyKey: newIdempotencyKey('mock'),
      body: { mock_type: mockType, target_knowledge_id: mockType === 'SPECIALIZED' ? targetKnowledgeId : null },
    }),
    onMutate: () => {
      setJobId(null)
      setStoredMockId('')
      setGrades({})
      setScore(null)
      window.localStorage.removeItem('closed-loop.last-mock')
    },
    onSuccess: (created) => setJobId(created.id),
  })
  const selectedScope = scopes.data?.find((item) => item.chapter_id === targetKnowledgeId)
  const allStrengthened = Boolean(unlocks.data?.length) && unlocks.data?.every((item) => item.status === 'STRENGTHENED')
  const fullUnlocked = allStrengthened && ['MOCK_EXAM', 'SPRINT'].includes(profile.data?.current_stage ?? '')
  const confirmFull = useMutation({
    mutationFn: () => apiRequest('/me/full-mock/unlock/confirm', { method: 'POST', idempotencyKey: newIdempotencyKey('full-mock-unlock') }),
    onSuccess: () => { void queryClient.invalidateQueries({ queryKey: ['student-profile'] }); void queryClient.invalidateQueries({ queryKey: ['learning-unlocks'] }) },
  })

  useEffect(() => {
    const resultId = job.data?.result?.mock_exam_id
    if (typeof resultId !== 'string') return
    window.localStorage.setItem('closed-loop.last-mock', resultId)
  }, [job.data])

  const submit = useMutation({
    mutationFn: async () => {
      if (!mock.data) throw new Error('请先生成模拟卷')
      if (mock.data.questions.some((question) => !grades[question.id])) throw new Error('请记录每道题的得分')
      return apiRequest<{ score: string }>(`/mock-exams/${mock.data.id}/attempts`, {
        method: 'POST',
        idempotencyKey: newIdempotencyKey(`mock-submit-${mock.data.id}`),
        body: { results: mock.data.questions.map((question) => ({ question_id: question.id, score_ratio: grades[question.id]?.score ?? 0, duration_seconds: (grades[question.id]?.minutes ?? 1) * 60, looked_at_solution: grades[question.id]?.lookedAtSolution ?? false })) },
      })
    },
    onSuccess: (result) => {
      setScore(Number(result.score))
      void queryClient.invalidateQueries({ queryKey: ['student-profile'] })
    },
  })

  function updateGrade(questionId: string, patch: Partial<Grade>) {
    setGrades((current) => ({ ...current, [questionId]: { score: 0, minutes: 15, lookedAtSolution: false, ...current[questionId], ...patch } }))
  }

  return (
    <div className="page mock-page">
      <PageHeader eyebrow="Mock exam / 个性化组卷" title="先选考纲章节，再对准章内弱点。" description="专项范围按目标院校真题考纲划分；章内题目根据你的真题错误与细知识点正确率推荐。" />
      <Panel className="mock-builder">
        <div className="mock-type-switch" role="group" aria-label="试卷类型">
          <button className={mockType === 'FULL' ? 'active' : ''} onClick={() => { setMockType('FULL'); setJobId(null) }}><FileStack />全真模拟<span>保持 DEMO-801 结构</span></button>
          <button className={mockType === 'SPECIALIZED' ? 'active' : ''} onClick={() => { setMockType('SPECIALIZED'); setJobId(null) }}><Sparkles />专项强化<span>按章节集中修复细知识点</span></button>
        </div>
        {mockType === 'SPECIALIZED' ? <SpecializedScopeSelector scopes={scopes.data ?? []} value={targetKnowledgeId} onChange={setTargetKnowledgeId} /> : null}
        <Button busy={create.isPending || Boolean(jobId && isMockJobRunning(job.data?.status))} disabled={mockType === 'SPECIALIZED' ? !selectedScope?.specialized_unlocked : !fullUnlocked} onClick={() => create.mutate()}><Sparkles size={16} />开始组卷</Button>
      </Panel>
      {mockType === 'SPECIALIZED' && targetKnowledgeId && !selectedScope?.specialized_unlocked ? <ErrorState message="本章仍有知识点未确认强化，或尚未完成当前版本的全部章节真题，因此专项强化保持锁定。" /> : null}
      {mockType === 'FULL' && !allStrengthened ? <ErrorState message="全部考纲知识点确认强化完成后，才可生成院校全真模拟卷。" /> : null}
      {mockType === 'FULL' && allStrengthened && !fullUnlocked ? <Panel className="job-banner"><div><strong>已满足全真模拟条件</strong><p>Agent 建议进入全真模拟阶段，确认后开放组卷。</p></div><Button busy={confirmFull.isPending} onClick={() => confirmFull.mutate()}>确认进入全真模拟</Button></Panel> : null}
      {create.error ? <ErrorState message={create.error instanceof ApiError ? create.error.message : '组卷没有完成'} /> : null}
      {job.data?.status === 'FAILED' ? <ErrorState message={`组卷任务失败：${job.data.error_code ?? 'UNKNOWN_ERROR'}`} /> : null}
      {jobId && isMockJobRunning(job.data?.status) ? <Panel className="job-banner"><LoaderCircle className="spin" /><div><strong>Mock Planner 正在求解约束</strong><p>{job.data?.status ?? 'QUEUED'} · 题型、考点、难度与个人弱点同时计算</p></div></Panel> : null}
      {mock.isLoading ? <LoadingState label="读取模拟卷" /> : null}
      {mock.data?.status === 'WAITING_FOR_REVIEW' ? <EmptyState title="候选题正在等待审核" description="现有题库不足以满足这份卷子的结构约束。AI 候选题不会自动发布，Reviewer 审核后会自动续跑组卷。" /> : null}
      {mock.data?.status === 'PUBLISHED' || mock.data?.status === 'COMPLETED' ? (
        <section className="mock-paper">
          <header className="paper-header"><div><p className="eyebrow">{mock.data.mock_type}</p><h2>DEMO-801 个性化模拟卷</h2></div><StatusPill tone="signal">{mock.data.total_score} 分 / {mock.data.duration_minutes} min</StatusPill></header>
          {mock.data.questions.map((question) => (
            <article className="paper-question" key={question.id}>
              <header><span>{question.sequence}</span><div><StatusPill>{question.difficulty}</StatusPill><strong>{question.score} 分</strong></div></header>
              <p>{question.content}</p>
              <div className="paper-grade"><div>{[0, .5, 1].map((value) => <button key={value} className={grades[question.id]?.score === value ? 'active' : ''} onClick={() => updateGrade(question.id, { score: value })}>{Math.round(value * 100)}%{grades[question.id]?.score === value ? <Check size={13} /> : null}</button>)}</div><label><input type="number" min={1} value={grades[question.id]?.minutes ?? 15} onChange={(event) => updateGrade(question.id, { minutes: Number(event.target.value) })} /> min</label><label><input type="checkbox" checked={grades[question.id]?.lookedAtSolution ?? false} onChange={(event) => updateGrade(question.id, { lookedAtSolution: event.target.checked })} />看过答案</label></div>
            </article>
          ))}
          {score !== null ? <Panel className="score-result"><FileStack /><div><span>模拟卷得分</span><strong>{score} / {mock.data.total_score}</strong><p>结果已写回学生画像，但不会污染真题画像。</p></div></Panel> : <div className="paper-submit">{submit.error ? <p className="inline-error">{submit.error instanceof Error ? submit.error.message : '提交失败'}</p> : null}<Button busy={submit.isPending} onClick={() => submit.mutate()}><Send size={16} />提交模拟卷</Button></div>}
        </section>
      ) : !mockId && !jobId ? <EmptyState title="还没有模拟卷" description="完成几次练习或真题后再组卷，个性化信号会更可靠。" /> : null}
    </div>
  )
}
