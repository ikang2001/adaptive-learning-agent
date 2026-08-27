import { useQuery } from '@tanstack/react-query'
import { ArrowUpRight, Bot, CalendarClock, CircleGauge, Clock3, Radio, Target } from 'lucide-react'
import { Link } from 'react-router-dom'
import { ApiError, apiRequest } from '../../api/client'
import type { StudentProfile, TodayTask, WeeklyPlan } from '../../api/types'
import { EmptyState, ErrorState, LoadingState, Panel, StatusPill } from '../../components/ui'
import { formatDate, stageLabel, taskLabel, toIsoDate } from '../../lib/format'

export function DashboardPage() {
  const profile = useQuery({
    queryKey: ['student-profile'],
    queryFn: () => apiRequest<StudentProfile>('/me/student-profile'),
  })
  const plan = useQuery({
    queryKey: ['current-plan'],
    queryFn: () => apiRequest<WeeklyPlan>('/me/plans/current'),
    retry: false,
  })
  const practice = useQuery({
    queryKey: ['today-tasks', toIsoDate(new Date())],
    queryFn: () => apiRequest<TodayTask[]>(`/me/tasks/today?target_date=${toIsoDate(new Date())}`),
    retry: false,
  })

  if (profile.isLoading) return <LoadingState />
  if (profile.isError) return <ErrorState message="学生画像暂时无法读取" onRetry={() => void profile.refetch()} />
  if (!profile.data) return <ErrorState message="学生画像返回了空数据" />

  const hasPlan = plan.data && plan.data.tasks.length > 0
  const todayTasks = practice.data ?? []
  const todayMinutes = todayTasks.reduce((total, task) => total + task.estimated_max_minutes, 0)
  const todayTaskCount = todayTasks.length

  return (
    <div className="page page--dashboard">
      <section className="loop-hero">
        <div className="loop-hero__copy">
          <p className="eyebrow">Learning loop / 当前回路</p>
          <h1>今天的反馈，<br />决定明天的题。</h1>
          <div className="loop-hero__meta">
            <StatusPill tone="signal">{stageLabel(profile.data.current_stage)}</StatusPill>
            <span><Radio size={15} /> Agent Runtime 在线</span>
          </div>
        </div>
        <div className="loop-track" aria-label="学习闭环：计划、练习、反馈、重排">
          <svg viewBox="0 0 760 200" aria-hidden>
            <path className="loop-track__base" d="M50 100 H184 C230 100 230 35 278 35 H465 C513 35 513 165 562 165 H710" />
            <path className="loop-track__signal" d="M50 100 H184 C230 100 230 35 278 35 H465 C513 35 513 165 562 165 H710" />
          </svg>
          <div className="loop-node loop-node--one"><span>计划</span><small>Plan v{plan.data?.revision ?? '—'}</small></div>
          <div className="loop-node loop-node--two"><span>执行</span><small>{todayTaskCount} 项学习任务</small></div>
          <div className="loop-node loop-node--three"><span>反馈</span><small>等待输入</small></div>
          <div className="loop-node loop-node--four"><span>调整</span><small>证据驱动</small></div>
        </div>
      </section>

      <section className="metric-strip" aria-label="今日数据">
        <article><Clock3 /><span>今日时间上限</span><strong>{todayMinutes || '—'}<small> min</small></strong></article>
        <article><Target /><span>今日任务</span><strong>{todayTaskCount || '—'}<small> 项</small></strong></article>
        <article><CalendarClock /><span>计划版本</span><strong>{plan.data ? `v${plan.data.revision}` : '未生成'}</strong></article>
        <article><CircleGauge /><span>当前阶段</span><strong>{stageLabel(profile.data.current_stage)}</strong></article>
      </section>

      <div className="dashboard-grid">
        <Panel className="today-panel">
          <div className="panel-heading">
            <div><p className="eyebrow">Today / 今日</p><h2>学习任务</h2></div>
            <Link className="text-link" to="/practice">进入练习 <ArrowUpRight size={15} /></Link>
          </div>
          {todayTasks.length ? (
            <div className="task-list">
              {todayTasks.map((task, index) => (
                <article className="task-row" key={task.id}>
                  <span className="task-row__index">{String(index + 1).padStart(2, '0')}</span>
                  <div><strong>{task.title}</strong><p>{taskLabel(task.task_type)} · {task.resource_section_title ?? '正式资源'} · {task.effective_minutes} 分钟</p></div>
                  <StatusPill tone={task.status === 'COMPLETED' ? 'good' : 'neutral'}>{task.status === 'COMPLETED' ? '已完成' : '待执行'}</StatusPill>
                </article>
              ))}
            </div>
          ) : (
            <EmptyState title="今天还没有任务" description="先生成一份七日计划，系统会根据你的时间上限自动装配题目。" action={<Link className="button button--primary" to="/plan">生成七日计划</Link>} />
          )}
        </Panel>

        <Panel className="next-panel">
          <div className="panel-heading"><div><p className="eyebrow">Next / 接下来</p><h2>计划信号</h2></div><Bot size={21} /></div>
          {hasPlan ? (
            <div className="next-plan">
              <p>{formatDate(plan.data.start_date)} — {formatDate(plan.data.end_date)}</p>
              <strong>{plan.data.tasks.length} 个明确任务</strong>
              <div className="mini-bars" aria-hidden>{plan.data.tasks.slice(0, 7).map((task) => <span key={task.id} style={{ height: `${Math.max(18, task.priority * 100)}%` }} />)}</div>
              <Link className="button button--quiet" to="/plan">查看完整计划 <ArrowUpRight size={15} /></Link>
            </div>
          ) : plan.error instanceof ApiError && plan.error.status === 404 ? (
            <EmptyState title="计划尚未启动" description="完成初始化后，只需一次生成即可进入学习闭环。" />
          ) : (
            <LoadingState label="读取计划状态" />
          )}
        </Panel>
      </div>
    </div>
  )
}
