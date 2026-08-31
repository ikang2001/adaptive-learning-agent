import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Ban, Bot, CheckCircle2, FlaskConical, GitBranch, History, Search, ShieldCheck, Wrench, XCircle } from 'lucide-react'
import { useState } from 'react'
import { apiRequest, newIdempotencyKey } from '../../api/client'
import type { AgentReplay, AgentRun, Proposal, ShadowEvaluation } from '../../api/types'
import { Button, EmptyState, ErrorState, LoadingState, PageHeader, Panel, StatusPill } from '../../components/ui'
import { reasonLabel } from '../../lib/format'
import { useAuth } from '../auth/AuthContext'

type ShadowCreateResponse = { evaluation: ShadowEvaluation; job_id: string }

export function AgentPage() {
  const queryClient = useQueryClient()
  const { roles } = useAuth()
  const isAdmin = roles.includes('ADMIN')
  const [runId, setRunId] = useState(() => window.localStorage.getItem('closed-loop.last-agent-run') ?? '')
  const [showReplay, setShowReplay] = useState(false)
  const [shadowId, setShadowId] = useState('')
  const run = useQuery({
    queryKey: ['agent-run', runId],
    queryFn: () => apiRequest<AgentRun>(`/agent-runs/${runId}`),
    enabled: Boolean(runId),
    retry: false,
  })
  const replay = useQuery({
    queryKey: ['agent-replay', runId],
    queryFn: () => apiRequest<AgentReplay>(`/agent-runs/${runId}/replay`),
    enabled: Boolean(runId) && showReplay,
    retry: false,
  })
  const shadow = useQuery({
    queryKey: ['shadow-evaluation', shadowId],
    queryFn: () => apiRequest<ShadowEvaluation>(`/shadow-evaluations/${shadowId}`),
    enabled: Boolean(shadowId),
    refetchInterval: (query) => ['QUEUED', 'RUNNING'].includes(query.state.data?.status ?? '') ? 1500 : false,
  })
  const decide = useMutation({
    mutationFn: ({ proposal, approve }: { proposal: Proposal; approve: boolean }) => apiRequest(`/proposals/${proposal.id}/${approve ? 'approve' : 'reject'}`, {
      method: 'POST',
      body: { reason: approve ? 'student approved in Agent console' : 'student rejected in Agent console' },
      idempotencyKey: newIdempotencyKey(`proposal-${proposal.id}`),
    }),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['agent-run', runId] }),
  })
  const cancel = useMutation({
    mutationFn: () => apiRequest(`/agent-runs/${runId}/cancel`, {
      method: 'POST',
      idempotencyKey: newIdempotencyKey(`cancel-${runId}`),
    }),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['agent-run', runId] }),
  })
  const createShadow = useMutation({
    mutationFn: () => apiRequest<ShadowCreateResponse>(`/agent-runs/${runId}/shadow-evaluations`, {
      method: 'POST',
      idempotencyKey: newIdempotencyKey(`shadow-${runId}`),
    }),
    onSuccess: (result) => setShadowId(result.evaluation.id),
  })

  return (
    <div className="page agent-page">
      <PageHeader eyebrow="Agent / 决策轨迹" title="每一个结论，都能追到证据。" description="查看模型调用、工具重试、Checkpoint、Guardrail，以及仍需确认的业务提案。" />
      <Panel className="run-search">
        <Search size={17} />
        <input aria-label="Agent Run ID" placeholder="输入 Agent Run ID" value={runId} onChange={(event) => setRunId(event.target.value)} />
        <Button variant="quiet" onClick={() => void run.refetch()}>读取轨迹</Button>
        <Button variant="quiet" disabled={!run.data} onClick={() => setShowReplay((value) => !value)}><History size={15} />{showReplay ? '关闭重放' : '只读重放'}</Button>
        {run.data && ['PENDING', 'RUNNING'].includes(run.data.status) ? <Button variant="quiet" busy={cancel.isPending} onClick={() => cancel.mutate()}><Ban size={15} />请求取消</Button> : null}
      </Panel>
      {!runId ? <EmptyState title="还没有诊断轨迹" description="当反馈出现明显超时、低正确率或连续同类错误时，Agent 才会进入决策循环。" /> : null}
      {run.isLoading ? <LoadingState label="正在还原 Agent 轨迹" /> : null}
      {run.isError ? <ErrorState message="这个 Run 不存在，或不属于当前用户" /> : null}
      {run.data ? (
        <>
          <section className="run-summary">
            <article><Bot /><span>决策状态</span><strong>{run.data.status}</strong></article>
            <article><GitBranch /><span>模型 / 循环</span><strong>{run.data.model_call_count} / {run.data.loop_count}</strong></article>
            <article><Wrench /><span>工具调用</span><strong>{run.data.tool_call_count}</strong></article>
            <article><ShieldCheck /><span>终止原因</span><strong>{run.data.termination_reason ?? '—'}</strong></article>
          </section>
          <div className="agent-grid">
            <Panel>
              <div className="panel-heading"><div><p className="eyebrow">Trace</p><h2>执行时间线</h2></div><StatusPill tone="signal">{run.data.model_version}</StatusPill></div>
              <div className="trace-list">
                {run.data.steps.map((step) => (
                  <article key={step.step_number}><span>{step.step_number}</span><div><strong>{typeof step.action.tool_name === 'string' ? step.action.tool_name : typeof step.action.decision === 'string' ? step.action.decision : '模型判断'}</strong><p>{step.latency_ms}ms · {step.input_tokens + step.output_tokens} tokens</p></div></article>
                ))}
              </div>
              <small>累计 {run.data.input_tokens + run.data.output_tokens} tokens · Resume {run.data.resumed_count} 次</small>
            </Panel>
            <Panel>
              <div className="panel-heading"><div><p className="eyebrow">Proposals</p><h2>调整提案</h2></div></div>
              {run.data.proposals.length ? run.data.proposals.map((proposal) => (
                <article className="proposal-card" key={proposal.id}>
                  <header><strong>{proposal.proposal_type}</strong><StatusPill tone={proposal.status === 'AWAITING_CONFIRMATION' || proposal.status === 'APPLY_FAILED' ? 'warn' : 'good'}>{proposal.status}</StatusPill></header>
                  <p>{proposal.reason_codes.map(reasonLabel).join(' · ') || '策略性调整'}</p>
                  <div className="confidence"><span style={{ width: `${proposal.confidence * 100}%` }} /></div>
                  <small>置信度 {Math.round(proposal.confidence * 100)}% · {proposal.evidence_refs.length} 条证据{proposal.apply_error_code ? ` · ${proposal.apply_error_code}` : ''}</small>
                  {proposal.status === 'AWAITING_CONFIRMATION' ? <div className="proposal-actions"><Button busy={decide.isPending} onClick={() => decide.mutate({ proposal, approve: true })}><CheckCircle2 size={15} />批准</Button><Button variant="quiet" busy={decide.isPending} onClick={() => decide.mutate({ proposal, approve: false })}><XCircle size={15} />拒绝</Button></div> : null}
                </article>
              )) : <EmptyState title="本轮没有待确认提案" description="正常反馈或低风险微调不会要求人工确认。" />}
            </Panel>
          </div>
          {showReplay ? <ReplayPanel replay={replay.data} loading={replay.isLoading} failed={replay.isError} /> : null}
          {isAdmin ? (
            <Panel>
              <div className="panel-heading"><div><p className="eyebrow">Shadow Evaluation</p><h2>候选模型影子评测</h2></div><Button variant="quiet" busy={createShadow.isPending} onClick={() => createShadow.mutate()}><FlaskConical size={15} />创建影子评测</Button></div>
              <p>默认关闭且绝不执行提案副作用；需服务端显式开启后，管理员才能按需运行。</p>
              {createShadow.isError ? <ErrorState message="Shadow 未启用，或当前 Run 不满足评测条件" /> : null}
              {shadow.data ? <p><strong>{shadow.data.status}</strong> · {shadow.data.baseline_decision ?? '—'} → {shadow.data.candidate_decision ?? '—'} · {shadow.data.candidate_model}</p> : null}
            </Panel>
          ) : null}
        </>
      ) : null}
    </div>
  )
}

function ReplayPanel({ replay, loading, failed }: { replay: AgentReplay | undefined; loading: boolean; failed: boolean }) {
  if (loading) return <LoadingState label="正在构造只读重放" />
  if (failed) return <ErrorState message="无法读取 Replay 数据" />
  if (!replay) return null
  return (
    <Panel>
      <div className="panel-heading"><div><p className="eyebrow">Read-only Replay</p><h2>恢复与防重轨迹</h2></div><StatusPill tone="signal">零副作用</StatusPill></div>
      <div className="trace-list">
        {replay.timeline.map((item) => (
          <article key={item.step_number}>
            <span>{item.step_number}</span>
            <div><strong>{item.tool?.name ?? (typeof item.action.decision === 'string' ? item.action.decision : '模型步骤')}</strong><p>{item.model_attempts.length} 次模型调用 · Tool retry {item.tool?.retry_count ?? 0} · Checkpoint {item.checkpoint?.resume_safe ? 'safe' : '—'}</p></div>
          </article>
        ))}
      </div>
    </Panel>
  )
}
