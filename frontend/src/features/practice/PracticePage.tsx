import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { BookOpen, CheckCircle2, Clock3, FileText, NotebookPen, Send, Target } from 'lucide-react'
import { useMemo, useState } from 'react'
import { ApiError, apiRequest, newIdempotencyKey } from '../../api/client'
import type { TodayTask } from '../../api/types'
import { Button, EmptyState, ErrorState, Field, LoadingState, PageHeader, Panel, StatusPill } from '../../components/ui'
import { taskLabel, toIsoDate } from '../../lib/format'

type FeedbackDraft = {
  completion: number
  minutes: number
  difficulty: number
  progress: string
  completedUnits: number
  correctUnits: number
  lookedAtSolution: boolean
  mastery: number
  summary: string
  note: string
}

const defaultDraft = (): FeedbackDraft => ({
  completion: 1,
  minutes: 30,
  difficulty: 3,
  progress: '',
  completedUnits: 0,
  correctUnits: 0,
  lookedAtSolution: false,
  mastery: 3,
  summary: '',
  note: '',
})

function taskIcon(type: string) {
  if (type === 'COURSE_LEARNING') return <BookOpen />
  if (type === 'HANDOUT_PRACTICE') return <NotebookPen />
  return <FileText />
}

export function PracticePage() {
  const queryClient = useQueryClient()
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [drafts, setDrafts] = useState<Record<string, FeedbackDraft>>({})
  const [savedId, setSavedId] = useState<string | null>(null)
  const today = toIsoDate(new Date())
  const tasks = useQuery({
    queryKey: ['today-tasks', today],
    queryFn: () => apiRequest<TodayTask[]>(`/me/tasks/today?target_date=${today}`),
    retry: false,
  })
  const selected = tasks.data?.find((task) => task.id === selectedId) ?? tasks.data?.[0]
  const draft = selected ? drafts[selected.id] ?? { ...defaultDraft(), minutes: selected.effective_minutes } : null
  const finished = useMemo(() => tasks.data?.filter((task) => task.status === 'COMPLETED').length ?? 0, [tasks.data])

  const submit = useMutation({
    mutationFn: async () => {
      if (!selected || !draft) throw new Error('请选择学习任务')
      return apiRequest(`/tasks/${selected.id}/feedback`, {
        method: 'PUT',
        idempotencyKey: newIdempotencyKey(`learning-feedback-${selected.id}`),
        body: {
          expected_version: selected.feedback_version,
          completion_ratio: draft.completion,
          actual_duration_seconds: draft.minutes * 60,
          perceived_difficulty: draft.difficulty,
          free_text: draft.note,
          progress_marker: draft.progress,
          mastery_self_score: draft.mastery,
          completed_units: draft.completedUnits,
          correct_units: selected.task_type === 'HANDOUT_PRACTICE' ? draft.correctUnits : null,
          looked_at_solution: selected.task_type === 'HANDOUT_PRACTICE' ? draft.lookedAtSolution : null,
          summary_text: ['KNOWLEDGE_SUMMARY', 'ERROR_REVIEW'].includes(selected.task_type) ? draft.summary : null,
        },
      })
    },
    onSuccess: () => {
      setSavedId(selected?.id ?? null)
      void queryClient.invalidateQueries({ queryKey: ['today-tasks'] })
      void queryClient.invalidateQueries({ queryKey: ['current-plan'] })
      void queryClient.invalidateQueries({ queryKey: ['learning-unlocks'] })
    },
  })

  function update(patch: Partial<FeedbackDraft>) {
    if (!selected) return
    setDrafts((current) => ({ ...current, [selected.id]: { ...defaultDraft(), minutes: selected.effective_minutes, ...current[selected.id], ...patch } }))
  }

  if (tasks.isLoading) return <LoadingState label="正在读取今日学习任务" />
  if (tasks.isError) return <ErrorState message={tasks.error instanceof ApiError ? tasks.error.message : '今日任务暂时无法读取'} onRetry={() => void tasks.refetch()} />
  if (!tasks.data?.length) return <div className="page"><PageHeader eyebrow="Today / 今日执行" title="今天没有待执行任务。" /><EmptyState title="计划窗口是空的" description="计划会每天滚动补充新的课程、讲义和总结任务。" /></div>

  return (
    <div className="page practice-page">
      <PageHeader eyebrow="Today / 今日执行" title="去外面学习，回来记录真实进度。" description="系统不替代课程和辅导班讲义，只负责安排知识点、记录反馈并校准后续时长。" />
      <div className="practice-layout">
        <aside className="practice-tasks">
          <div className="practice-task-summary"><Target size={17} /><span>今日完成</span><strong>{finished} / {tasks.data.length}</strong></div>
          {tasks.data.map((task, index) => (
            <button key={task.id} className={selected?.id === task.id ? 'active' : ''} onClick={() => { setSelectedId(task.id); setSavedId(null) }}>
              <span>{String(index + 1).padStart(2, '0')}</span>
              <div><strong>{task.title}</strong><small>{taskLabel(task.task_type)} · {task.effective_minutes} min</small></div>
              <StatusPill tone={task.status === 'COMPLETED' ? 'good' : 'neutral'}>{task.status === 'COMPLETED' ? '完成' : '待办'}</StatusPill>
            </button>
          ))}
        </aside>

        {selected && draft ? <section className="learning-task-panel">
          <Panel className="learning-task-brief">
            <header><div className="learning-task-icon">{taskIcon(selected.task_type)}</div><div><p className="eyebrow">{taskLabel(selected.task_type)}</p><h2>{selected.title}</h2></div><StatusPill tone={selected.is_overdue ? 'warn' : 'signal'}>{selected.is_overdue ? '已逾期' : selected.origin}</StatusPill></header>
            <p>{selected.description}</p>
            <div className="learning-resource-line"><strong>{selected.resource_title ?? '正式资源待绑定'}</strong><span>{selected.resource_section_title ?? selected.suggested_scope ?? '按知识点完成'}</span><small>{selected.planned_units ?? '—'} {selected.unit_type ?? ''} · 建议 {selected.effective_minutes} min</small></div>
          </Panel>

          <Panel className="learning-feedback-form">
            <div className="panel-heading"><div><p className="eyebrow">Feedback</p><h2>提交学习反馈</h2></div><Clock3 /></div>
            <label className="field"><span className="field__label">完成度</span><input className="field__input" type="range" min={0} max={1} step={0.1} value={draft.completion} onChange={(event) => update({ completion: Number(event.target.value) })} /><span className="field__hint">{Math.round(draft.completion * 100)}%</span></label>
            <div className="feedback-grid"><Field label="实际用时（分钟）" type="number" min={1} max={1440} value={draft.minutes} onChange={(event) => update({ minutes: Number(event.target.value) })} /><Field label="主观难度（1-5）" type="number" min={1} max={5} value={draft.difficulty} onChange={(event) => update({ difficulty: Number(event.target.value) })} /></div>
            {selected.task_type === 'COURSE_LEARNING' ? <><Field label="学习到哪一节" placeholder="例如：2.3 劳斯表构造" value={draft.progress} onChange={(event) => update({ progress: event.target.value })} /><Field label={`完成${selected.unit_type ?? '单元'}数`} type="number" min={0} value={draft.completedUnits} onChange={(event) => update({ completedUnits: Number(event.target.value) })} /></> : null}
            {selected.task_type === 'HANDOUT_PRACTICE' ? <><div className="feedback-grid"><Field label="完成题量" type="number" min={0} value={draft.completedUnits} onChange={(event) => update({ completedUnits: Number(event.target.value) })} /><Field label="正确题量" type="number" min={0} max={draft.completedUnits} value={draft.correctUnits} onChange={(event) => update({ correctUnits: Number(event.target.value) })} /></div><label className="checkbox-field"><input type="checkbox" checked={draft.lookedAtSolution} onChange={(event) => update({ lookedAtSolution: event.target.checked })} />过程中看过讲义解析</label></> : null}
            {['KNOWLEDGE_SUMMARY', 'ERROR_REVIEW'].includes(selected.task_type) ? <label className="field"><span className="field__label">总结内容</span><textarea className="field__input feedback-textarea" value={draft.summary} onChange={(event) => update({ summary: event.target.value })} placeholder="记录公式、解题步骤、易错点和仍然不确定的地方" /></label> : null}
            <Field label="掌握自评（1-5）" type="number" min={1} max={5} value={draft.mastery} onChange={(event) => update({ mastery: Number(event.target.value) })} />
            <Field label="备注" value={draft.note} onChange={(event) => update({ note: event.target.value })} placeholder="临时事务、课程卡点或需要调整的地方" />
            {submit.error ? <p className="inline-error">{submit.error instanceof Error ? submit.error.message : '反馈保存失败'}</p> : null}
            {savedId === selected.id ? <p className="inline-success"><CheckCircle2 size={16} />反馈已保存，后续时长将使用本次数据校准。</p> : null}
            <Button busy={submit.isPending} onClick={() => submit.mutate()}><Send size={16} />保存反馈</Button>
          </Panel>
        </section> : null}
      </div>
    </div>
  )
}

