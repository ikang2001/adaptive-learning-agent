import { useMutation, useQuery } from '@tanstack/react-query'
import { useMemo, useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { ArrowRight, CalendarDays, Clock3, School } from 'lucide-react'
import { ApiError, apiRequest } from '../../api/client'
import { newIdempotencyKey } from '../../api/client'
import type { AvailabilityItem, BackgroundJob, School as SchoolType, StudentProfile } from '../../api/types'
import { Button, ErrorState, Field, LoadingState } from '../../components/ui'
import { toIsoDate } from '../../lib/format'

export function SetupPage() {
  const navigate = useNavigate()
  const [schoolId, setSchoolId] = useState('')
  const [examDate, setExamDate] = useState(() => {
    const date = new Date()
    date.setDate(date.getDate() + 120)
    return toIsoDate(date)
  })
  const [minutes, setMinutes] = useState(120)
  const [createdProfileVersion, setCreatedProfileVersion] = useState<number | null>(null)

  const schools = useQuery({
    queryKey: ['schools'],
    queryFn: () => apiRequest<SchoolType[]>('/schools'),
  })
  const availability = useMemo<AvailabilityItem[]>(() => {
    return Array.from({ length: 7 }, (_, offset) => {
      const date = new Date()
      date.setDate(date.getDate() + offset)
      return { date: toIsoDate(date), available_minutes: minutes }
    })
  }, [minutes])

  const save = useMutation({
    mutationFn: async () => {
      const profile = await apiRequest<StudentProfile>('/me/student-profile', {
        method: 'PUT',
        body: { target_school_id: schoolId, exam_date: examDate, expected_version: createdProfileVersion },
      })
      setCreatedProfileVersion(profile.version)
      await apiRequest('/me/availability', { method: 'PUT', body: { days: availability } })
      await apiRequest('/me/availability-template', {
        method: 'PUT',
        body: { days: Array.from({ length: 7 }, (_, weekday) => ({ weekday, available_minutes: minutes })) },
      })
      const job = await apiRequest<BackgroundJob>('/me/plans', {
        method: 'POST',
        body: { start_date: toIsoDate(new Date()) },
        idempotencyKey: newIdempotencyKey('initial-learning-plan'),
      })
      for (let attempt = 0; attempt < 30; attempt += 1) {
        const state = await apiRequest<BackgroundJob>(`/jobs/${job.id}`)
        if (state.status === 'SUCCEEDED') break
        if (state.status === 'FAILED') throw new Error(`计划生成失败：${state.error_code ?? 'UNKNOWN'}`)
        await new Promise((resolve) => window.setTimeout(resolve, 400))
      }
      return profile
    },
    onSuccess: () => navigate('/', { replace: true }),
  })

  function submit(event: FormEvent) {
    event.preventDefault()
    if (!schoolId) return
    save.mutate()
  }

  if (schools.isLoading) return <LoadingState label="正在加载院校画像" />
  if (schools.isError) return <ErrorState message="院校画像暂时不可用" onRetry={() => void schools.refetch()} />

  return (
    <main className="setup-page">
      <div className="setup-card">
        <p className="eyebrow">建立初始状态</p>
        <h1>先告诉系统，你要去哪里。</h1>
        <p className="setup-card__lead">这三项信息会决定第一版七日计划。完成练习后，系统会逐步用真实表现替代初始估计。</p>
        <form onSubmit={submit}>
          <label className="field">
            <span className="field__label"><School size={15} />目标院校</span>
            <select className="field__input" value={schoolId} onChange={(event) => setSchoolId(event.target.value)} required>
              <option value="">选择一个院校画像</option>
              {schools.data?.map((school) => <option key={school.id} value={school.id}>{school.school_name} · {school.subject_code}</option>)}
            </select>
          </label>
          <Field label="考试日期" type="date" value={examDate} onChange={(event) => setExamDate(event.target.value)} min={toIsoDate(new Date())} />
          <Field label="每天专业课时间（分钟）" type="number" min={30} max={600} step={15} value={minutes} onChange={(event) => setMinutes(Number(event.target.value))} hint="先统一设置未来 7 天，之后可以按天调整。" />
          {save.isError ? <p className="inline-error">{save.error instanceof ApiError ? save.error.message : '保存没有完成'}</p> : null}
          <Button type="submit" busy={save.isPending} disabled={!schoolId} className="button--wide">生成我的起点 <ArrowRight size={17} /></Button>
        </form>
        <div className="setup-preview" aria-hidden>
          <div><CalendarDays /><span>未来 7 天</span><strong>自动排程</strong></div>
          <div><Clock3 /><span>每日上限</span><strong>{minutes} min</strong></div>
        </div>
      </div>
    </main>
  )
}
