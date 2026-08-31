import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { CalendarRange, Edit3, Plus, Save, X } from 'lucide-react'
import { useMemo, useState } from 'react'
import { ApiError, apiRequest, newIdempotencyKey } from '../../api/client'
import type { BackgroundJob, WeeklyPlan } from '../../api/types'
import type { PublishedResourceSection } from '../../api/types'
import { Button, EmptyState, ErrorState, Field, LoadingState, PageHeader, StatusPill } from '../../components/ui'
import { formatDate, taskLabel, toIsoDate } from '../../lib/format'

export function PlanPage() {
  const queryClient = useQueryClient()
  const [editing, setEditing] = useState(false)
  const [draftTasks, setDraftTasks] = useState<WeeklyPlan['tasks']>([])
  const [saveError, setSaveError] = useState('')
  const [removedIds, setRemovedIds] = useState<string[]>([])
  const [newSectionId, setNewSectionId] = useState('')
  const [viewMode, setViewMode] = useState<'future' | 'history'>('future')
  const historyEnd = new Date()
  historyEnd.setDate(historyEnd.getDate() - 1)
  const historyStart = new Date(historyEnd)
  historyStart.setDate(historyStart.getDate() - 29)
  const planPath = viewMode === 'history'
    ? `/me/plans/current?from_date=${toIsoDate(historyStart)}&to_date=${toIsoDate(historyEnd)}`
    : '/me/plans/current'
  const plan = useQuery({
    queryKey: ['current-plan', viewMode],
    queryFn: () => apiRequest<WeeklyPlan>(planPath),
    retry: false,
  })
  const resources = useQuery({
    queryKey: ['published-resource-sections'],
    queryFn: () => apiRequest<PublishedResourceSection[]>('/learning-resources/sections'),
  })
  const save = useMutation({
    mutationFn: (allowOverBudget: boolean) => apiRequest<WeeklyPlan>(`/plans/${plan.data?.id}/tasks`, {
      method: 'PATCH',
      idempotencyKey: newIdempotencyKey('plan-edit'),
      body: {
        expected_plan_version: plan.data?.version,
        allow_over_budget: allowOverBudget,
        changes: [
        ...draftTasks.filter((task) => task.status !== 'COMPLETED').map((task) => ({
          operation: plan.data?.tasks.some((original) => original.id === task.id) ? 'UPDATE' : 'CREATE',
          task_id: plan.data?.tasks.some((original) => original.id === task.id) ? task.id : undefined,
          expected_version: plan.data?.tasks.some((original) => original.id === task.id) ? task.version : undefined,
          task_date: task.task_date, task_type: task.task_type, title: task.title,
          description: task.description, knowledge_id: task.knowledge_id,
          resource_section_id: task.resource_section_id,
          suggested_scope: task.suggested_scope, planned_units: task.planned_units,
          unit_type: task.unit_type, student_estimated_minutes: task.effective_minutes,
          sequence: task.sequence, reason: 'student edited learning plan',
        })),
        ...removedIds.map((taskId) => ({ operation: 'DELETE', task_id: taskId, expected_version: plan.data?.tasks.find((task) => task.id === taskId)?.version, reason: 'student removed task' })),
        ],
      },
    }),
    onSuccess: () => { setEditing(false); setRemovedIds([]); void queryClient.invalidateQueries({ queryKey: ['current-plan'] }); setSaveError('') },
    onError: (error) => setSaveError(error instanceof ApiError ? error.message : '计划保存失败'),
  })
  const createInitial = useMutation({
    mutationFn: async () => {
      const job = await apiRequest<BackgroundJob>('/me/plans', {
        method: 'POST',
        idempotencyKey: newIdempotencyKey('initial-plan'),
        body: { start_date: toIsoDate(new Date()) },
      })
      for (let attempt = 0; attempt < 30; attempt += 1) {
        const state = await apiRequest<BackgroundJob>(`/jobs/${job.id}`)
        if (state.status === 'SUCCEEDED') return state
        if (state.status === 'FAILED' || state.status === 'DEAD_LETTER') throw new Error(state.error_code ?? '计划生成失败')
        await new Promise((resolve) => window.setTimeout(resolve, 400))
      }
      throw new Error('计划生成超时，请稍后刷新')
    },
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['current-plan'] }),
  })

  const visibleTasks = useMemo(
    () => (editing ? draftTasks : plan.data?.tasks ?? []),
    [draftTasks, editing, plan.data?.tasks],
  )
  const grouped = useMemo(() => Array.from(new Set(visibleTasks.map((task) => task.task_date))), [visibleTasks])

  return (
    <div className="page">
      <PageHeader eyebrow="Plan / 学习计划本" title="把每个知识点，变成可完成的学习任务。" description="课程、讲义和总结按你的可用时间排进未来七天；完成后，反馈会校准下一次的时长。" action={plan.data && viewMode === 'future' ? <Button onClick={() => { if (!editing) { setDraftTasks(plan.data.tasks); setRemovedIds([]) } setEditing((value) => !value); setSaveError('') }}>{editing ? <X size={16} /> : <Edit3 size={16} />}{editing ? '取消编辑' : '编辑计划'}</Button> : null} />
      <div className="plan-view-switch"><button className={viewMode === 'future' ? 'active' : ''} onClick={() => { setViewMode('future'); setEditing(false) }}>未来 7 天</button><button className={viewMode === 'history' ? 'active' : ''} onClick={() => { setViewMode('history'); setEditing(false) }}>历史记录</button></div>
      {saveError ? <div className="capacity-confirm"><ErrorState message={saveError} />{save.error instanceof ApiError && save.error.code === 'PLAN_CAPACITY_EXCEEDED' ? <Button variant="danger" busy={save.isPending} onClick={() => save.mutate(true)}>确认超出容量并保存</Button> : null}</div> : null}
      {plan.isLoading ? <LoadingState label="读取当前计划" /> : null}
      {plan.error instanceof ApiError && plan.error.status === 404 ? (
        <EmptyState title="还没有学习计划本" description="系统会根据已发布的课程和讲义目录生成第一周的知识学习任务。" action={<Button busy={createInitial.isPending} onClick={() => createInitial.mutate()}>生成第一版学习计划</Button>} />
      ) : null}
      {plan.data ? (
        <div className="plan-board">
          <div className="plan-board__meta"><CalendarRange /><div><span>{formatDate(plan.data.start_date)} — {formatDate(plan.data.end_date)}</span><strong>活动计划本 · v{plan.data.version}</strong></div><StatusPill tone="signal">每日滚动</StatusPill></div>
          <div className="plan-days">
            {grouped.map((day) => (
              <section className="plan-day" key={day}>
                <header><span>{formatDate(day)}</span><small>{visibleTasks.filter((task) => task.task_date === day).reduce((sum, task) => sum + task.effective_minutes, 0)} min</small></header>
                <div>
                  {visibleTasks.filter((task) => task.task_date === day).map((task) => (
                    <article className="plan-task" key={task.id}>
                      <span className="plan-task__signal" style={{ opacity: Math.max(.35, task.priority) }} />
                      {editing && task.status !== 'COMPLETED' ? <div className="plan-task__edit"><Field label="任务" value={task.title} onChange={(event) => setDraftTasks((items) => items.map((item) => item.id === task.id ? { ...item, title: event.target.value } : item))} /><Field label="日期" type="date" value={task.task_date} onChange={(event) => setDraftTasks((items) => items.map((item) => item.id === task.id ? { ...item, task_date: event.target.value } : item))} /><Field label="分钟" type="number" min={1} max={1440} value={task.effective_minutes} onChange={(event) => setDraftTasks((items) => items.map((item) => item.id === task.id ? { ...item, effective_minutes: Number(event.target.value), estimated_min_minutes: Number(event.target.value), estimated_max_minutes: Number(event.target.value) } : item))} /><Button variant="quiet" onClick={() => { setRemovedIds((ids) => [...ids, task.id]); setDraftTasks((items) => items.filter((item) => item.id !== task.id)) }}>删除</Button></div> : <div><strong>{task.title}</strong><p>{taskLabel(task.task_type)} · {task.resource_section_title ?? '正式资源待绑定'}</p><p>{task.suggested_scope ?? '按章节完成'} · {task.effective_minutes} 分钟</p></div>}
                      <StatusPill tone={task.status === 'COMPLETED' ? 'good' : task.has_capacity_warning ? 'warn' : 'neutral'}>{task.status === 'COMPLETED' ? '完成' : task.has_capacity_warning ? '超出容量' : '待办'}</StatusPill>
                    </article>
                  ))}
                </div>
              </section>
            ))}
          </div>
          {editing ? <div className="plan-edit-footer"><label className="field"><span className="field__label">新增正式资源任务</span><select className="field__input" value={newSectionId} onChange={(event) => setNewSectionId(event.target.value)}><option value="">选择已发布课程/讲义章节</option>{resources.data?.map((item) => <option key={item.id} value={item.id}>{item.resource_title} / {item.title}</option>)}</select></label><Button variant="quiet" disabled={!newSectionId} onClick={() => { const section = resources.data?.find((item) => item.id === newSectionId); if (!section) return; const date = plan.data?.start_date ?? new Date().toISOString().slice(0, 10); setDraftTasks((items) => [...items, { id: crypto.randomUUID(), task_date: date, task_type: section.resource_type === 'COURSE' ? 'COURSE_LEARNING' : 'HANDOUT_PRACTICE', target_count: 0, estimated_min_minutes: 30, estimated_max_minutes: 30, priority: .5, status: 'PENDING', reason: 'student created task', sequence: items.length + 1, title: `${section.resource_type === 'COURSE' ? '学习' : '讲义'}：${section.title}`, description: '学生新增的正式资源学习任务', knowledge_id: section.knowledge_id, resource_section_id: section.id, resource_title: section.resource_title, resource_section_title: section.title, suggested_scope: section.page_start && section.page_end ? `第 ${section.page_start}-${section.page_end} 页` : null, planned_units: section.suggested_units, unit_type: section.unit_type, system_suggested_minutes: 30, student_estimated_minutes: 30, effective_minutes: 30, origin: 'STUDENT', is_personal: true, has_capacity_warning: false, version: 1 }]); setNewSectionId('') }}><Plus size={15} />新增任务</Button><span>所有修改会写入计划变更记录</span><Button busy={save.isPending} onClick={() => save.mutate(false)}><Save size={16} />保存更改</Button></div> : null}
        </div>
      ) : null}
    </div>
  )
}
