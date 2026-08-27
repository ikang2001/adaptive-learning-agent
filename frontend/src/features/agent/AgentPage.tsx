import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Bot, CheckCircle2, GitBranch, Search, ShieldCheck, Wrench, XCircle } from 'lucide-react'
import { useState } from 'react'
import { apiRequest, newIdempotencyKey } from '../../api/client'
import type { AgentRun, Proposal } from '../../api/types'
import { Button, EmptyState, ErrorState, LoadingState, PageHeader, Panel, StatusPill } from '../../components/ui'
import { reasonLabel } from '../../lib/format'

export function AgentPage() {
  const queryClient = useQueryClient()
  const [runId, setRunId] = useState(() => window.localStorage.getItem('closed-loop.last-agent-run') ?? '')
  const run = useQuery({
    queryKey: ['agent-run', runId],
    queryFn: () => apiRequest<AgentRun>(`/agent-runs/${runId}`),
    enabled: Boolean(runId),
    retry: false,
  })
  const decide = useMutation({
    mutationFn: ({ proposal, approve }: { proposal: Proposal; approve: boolean }) => apiRequest(`/proposals/${proposal.id}/${approve ? 'approve' : 'reject'}`, {
      method: 'POST',
      idempotencyKey: newIdempotencyKey(`proposal-${proposal.id}`),
    }),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['agent-run', runId] }),
  })

  return (
    <div className="page agent-page">
      <PageHeader eyebrow="Agent / 决策轨迹" title="每一个结论，都能追到证据。" description="查看 Agent 为什么调用工具、何时停止，以及哪些修改仍在等待你的确认。" />
      <Panel className="run-search"><Search size={17} /><input aria-label="Agent Run ID" placeholder="输入 Agent Run ID" value={runId} onChange={(event) => setRunId(event.target.value)} /><Button variant="quiet" onClick={() => void run.refetch()}>读取轨迹</Button></Panel>
      {!runId ? <EmptyState title="还没有诊断轨迹" description="当反馈出现明显超时、低正确率或连续同类错误时，Agent 才会进入决策循环。" /> : null}
      {run.isLoading ? <LoadingState label="正在还原 Agent 轨迹" /> : null}
      {run.isError ? <ErrorState message="这个 Run 不存在，或不属于当前用户" /> : null}
      {run.data ? (
        <>
          <section className="run-summary">
            <article><Bot /><span>决策状态</span><strong>{run.data.status}</strong></article>
            <article><GitBranch /><span>循环步数</span><strong>{run.data.loop_count}</strong></article>
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
            </Panel>
            <Panel>
              <div className="panel-heading"><div><p className="eyebrow">Proposals</p><h2>调整提案</h2></div></div>
              {run.data.proposals.length ? run.data.proposals.map((proposal) => (
                <article className="proposal-card" key={proposal.id}>
                  <header><strong>{proposal.proposal_type}</strong><StatusPill tone={proposal.status === 'AWAITING_CONFIRMATION' ? 'warn' : 'good'}>{proposal.status}</StatusPill></header>
                  <p>{proposal.reason_codes.map(reasonLabel).join(' · ') || '策略性调整'}</p>
                  <div className="confidence"><span style={{ width: `${proposal.confidence * 100}%` }} /></div>
                  <small>置信度 {Math.round(proposal.confidence * 100)}% · {proposal.evidence_refs.length} 条证据</small>
                  {proposal.status === 'AWAITING_CONFIRMATION' ? <div className="proposal-actions"><Button busy={decide.isPending} onClick={() => decide.mutate({ proposal, approve: true })}><CheckCircle2 size={15} />批准</Button><Button variant="quiet" busy={decide.isPending} onClick={() => decide.mutate({ proposal, approve: false })}><XCircle size={15} />拒绝</Button></div> : null}
                </article>
              )) : <EmptyState title="本轮没有待确认提案" description="正常反馈或低风险微调不会要求人工确认。" />}
            </Panel>
          </div>
        </>
      ) : null}
    </div>
  )
}

