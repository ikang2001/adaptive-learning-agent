import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { CheckCircle2, ShieldCheck, XCircle } from 'lucide-react'
import { useState, type FormEvent } from 'react'
import { apiRequest, newIdempotencyKey } from '../../api/client'
import type { GeneratedQuestion, LearningResource, ResourceSectionReview } from '../../api/types'
import { Button, EmptyState, ErrorState, LoadingState, PageHeader, Panel, StatusPill } from '../../components/ui'
import { useAuth } from '../auth/AuthContext'

export function ReviewPage() {
  const queryClient = useQueryClient()
  const { roles } = useAuth()
  const allowed = roles.some((role) => role === 'REVIEWER' || role === 'ADMIN')
  const [uploading, setUploading] = useState(false)
  const [selectedResourceId, setSelectedResourceId] = useState('')
  const candidates = useQuery({
    queryKey: ['generated-question-review'],
    queryFn: () => apiRequest<GeneratedQuestion[]>('/review/generated-questions'),
    enabled: allowed,
  })
  const decide = useMutation({
    mutationFn: ({ candidate, approve }: { candidate: GeneratedQuestion; approve: boolean }) => apiRequest(`/review/generated-questions/${candidate.id}/${approve ? 'approve' : 'reject'}`, {
      method: 'POST',
      idempotencyKey: newIdempotencyKey(`review-${candidate.id}`),
      body: { reason: approve ? '体验前端审核通过' : '题目质量不符合发布要求' },
    }),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['generated-question-review'] }),
  })
  const resources = useQuery({ queryKey: ['resource-review'], queryFn: () => apiRequest<LearningResource[]>('/review/resources'), enabled: allowed })
  const sections = useQuery({ queryKey: ['resource-sections-review', selectedResourceId], queryFn: () => apiRequest<ResourceSectionReview[]>(`/review/resources/${selectedResourceId}/sections`), enabled: Boolean(selectedResourceId) })
  const confirmSection = useMutation({
    mutationFn: (section: ResourceSectionReview) => apiRequest(`/review/resource-sections/${section.id}`, {
      method: 'PATCH', idempotencyKey: newIdempotencyKey(`section-${section.id}`),
      body: { expected_version: section.version, title: section.title, page_start: section.page_start, page_end: section.page_end, knowledge_ids: section.mappings.map((item) => item.knowledge_id) },
    }),
    onSuccess: () => void sections.refetch(),
  })
  const publishResource = useMutation({
    mutationFn: (resourceId: string) => apiRequest(`/review/resources/${resourceId}/publish`, { method: 'POST', idempotencyKey: newIdempotencyKey(`publish-resource-${resourceId}`), body: { reason: '目录与知识点映射已人工确认' } }),
    onSuccess: () => { setSelectedResourceId(''); void resources.refetch() },
  })

  async function uploadResource(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const form = event.currentTarget
    const formData = new FormData(form)
    setUploading(true)
    try {
      const tokens = JSON.parse(localStorage.getItem('closed-loop.auth') ?? '{}') as { access_token?: string }
      const response = await fetch('/api/v1/resources/uploads', { method: 'POST', headers: { Authorization: `Bearer ${tokens.access_token ?? ''}` }, body: formData })
      if (!response.ok) throw new Error('资源上传失败')
      form.reset()
      void resources.refetch()
    } finally { setUploading(false) }
  }

  if (!allowed) return <div className="page"><PageHeader eyebrow="Review / 候选题审核" title="发布权和生成权必须分开。" /><EmptyState title="当前账号没有审核权限" description="先用 CLI 为已登录手机号授予 REVIEWER 或 ADMIN，再刷新登录令牌。" /></div>
  if (candidates.isLoading) return <LoadingState label="读取待审候选题" />
  if (candidates.isError) return <ErrorState message="候选题列表暂时无法读取" />

  return (
    <div className="page review-page">
      <PageHeader eyebrow="Review / 候选题审核" title="AI 可以出题，但不能自己发布。" description="核对题干完整性、可解性、答案一致性和目标知识点，再决定是否进入正式题库。" />
      <Panel className="resource-upload-panel"><div><p className="eyebrow">Learning resources</p><h2>导入课程与讲义目录</h2><p>支持 PDF、DOCX、Markdown，或最多 50 张 JPG/PNG 按顺序合并；解析后需确认知识点映射再发布。</p></div><form onSubmit={(event) => void uploadResource(event)}><input name="title" placeholder="资源标题" required /><select name="resource_type"><option value="COURSE">课程</option><option value="HANDOUT">讲义</option></select><input name="files" type="file" multiple accept=".pdf,.docx,.md,.markdown,.jpg,.jpeg,.png" required /><Button busy={uploading} type="submit">上传并解析</Button></form></Panel>
      {resources.data?.length ? <Panel><div className="panel-heading"><div><p className="eyebrow">Resource review</p><h2>待发布资源</h2></div></div>{resources.data.map((resource) => <button className="resource-review-row" key={resource.id} onClick={() => setSelectedResourceId(resource.id)}><ShieldCheck /><div><strong>{resource.title}</strong><p>{resource.resource_type} · {resource.status}</p></div><StatusPill tone="warn">审核目录</StatusPill></button>)}</Panel> : null}
      {selectedResourceId ? <Panel className="resource-sections-panel"><div className="panel-heading"><div><p className="eyebrow">Parsed outline</p><h2>确认目录与知识点映射</h2></div></div>{sections.isLoading ? <LoadingState label="读取解析目录" /> : sections.data?.map((section) => <article className="resource-section-row" key={section.id}><div><strong>{section.sequence}. {section.title}</strong><p>{section.page_start ? `第 ${section.page_start}-${section.page_end ?? section.page_start} 页` : section.section_path}</p><small>{section.mappings.map((item) => `${item.knowledge_name} ${Math.round(item.confidence * 100)}%`).join(' · ')}</small></div><Button variant="quiet" disabled={!section.mappings.length || section.mappings.every((item) => item.confirmed)} busy={confirmSection.isPending} onClick={() => confirmSection.mutate(section)}>{section.mappings.every((item) => item.confirmed) ? '已确认' : '确认映射'}</Button></article>)}<Button busy={publishResource.isPending} disabled={!sections.data?.length || sections.data.some((section) => !section.mappings.length || section.mappings.some((item) => !item.confirmed))} onClick={() => publishResource.mutate(selectedResourceId)}>发布为正式学习资源</Button></Panel> : null}
      {candidates.data?.length ? <div className="review-grid">{candidates.data.map((candidate) => (
        <Panel className="review-card" key={candidate.id}>
          <header><div><ShieldCheck /><span>{typeof candidate.metadata_json.knowledge_code === 'string' ? candidate.metadata_json.knowledge_code : 'GENERATED'}</span></div><StatusPill tone="warn">待人工审核</StatusPill></header>
          <section><p className="eyebrow">题干</p><h2>{candidate.content}</h2></section>
          <section><p className="eyebrow">答案</p><p>{candidate.answer}</p></section>
          <details><summary>查看完整解析</summary><p>{candidate.solution}</p></details>
          <footer><small>{candidate.generator_model} · {candidate.prompt_version}</small><div><Button variant="quiet" busy={decide.isPending} onClick={() => decide.mutate({ candidate, approve: false })}><XCircle size={15} />驳回</Button><Button busy={decide.isPending} onClick={() => decide.mutate({ candidate, approve: true })}><CheckCircle2 size={15} />批准发布</Button></div></footer>
        </Panel>
      ))}</div> : <EmptyState title="没有待审候选题" description="当现有题库无法满足模拟卷约束时，生成题会出现在这里。" />}
    </div>
  )
}
