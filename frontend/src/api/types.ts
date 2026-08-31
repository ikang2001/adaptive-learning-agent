export type AuthTokens = {
  access_token: string
  refresh_token: string
  token_type: string
  expires_in: number
}

export type ProblemDetails = {
  title?: string
  detail?: string
  status?: number
  errors?: unknown
}

export type School = {
  id: string
  code: string
  school_name: string
  major: string
  subject_code: string
  subject_name: string
  syllabus_version: string
}

export type KnowledgeNode = {
  id: string
  code: string
  parent_id: string | null
  level: number
  name: string
  description: string
  tree_version: string
}

export type StudentProfile = {
  id: string
  target_school_id: string | null
  exam_subject: string
  exam_date: string | null
  current_stage: string
  version: number
}

export type AvailabilityItem = {
  date: string
  available_minutes: number
}

export type BackgroundJob = {
  id: string
  job_type: string
  status: 'QUEUED' | 'RUNNING' | 'RETRY_WAIT' | 'SUCCEEDED' | 'FAILED' | 'DEAD_LETTER' | 'CANCELLED' | 'WAITING_FOR_REVIEW'
  result: Record<string, unknown> | null
  error_code: string | null
  created_at: string
  finished_at: string | null
  attempt_count: number
  max_attempts: number
  next_retry_at: string | null
  dead_lettered_at: string | null
}

export type PlanTask = {
  id: string
  task_date: string
  task_type: string
  target_count: number
  estimated_min_minutes: number
  estimated_max_minutes: number
  priority: number
  status: string
  reason: string
  sequence: number
  title: string
  description: string
  knowledge_id: string | null
  resource_section_id: string | null
  resource_title: string | null
  resource_section_title: string | null
  suggested_scope: string | null
  planned_units: number | null
  unit_type: string | null
  system_suggested_minutes: number
  student_estimated_minutes: number | null
  effective_minutes: number
  origin: string
  is_personal: boolean
  has_capacity_warning: boolean
  version: number
}

export type WeeklyPlan = {
  id: string
  start_date: string
  end_date: string
  revision: number
  status: string
  planner_version: string
  timezone: string
  version: number
  tasks: PlanTask[]
}

export type TodayTask = PlanTask & {
  is_overdue: boolean
  feedback_version: number | null
}

export type KnowledgeUnlock = {
  knowledge_id: string
  knowledge_code: string
  knowledge_name: string
  status: string
  learning_task_total: number
  learning_task_completed: number
  true_exam_total: number
  true_exam_completed: number
  true_exam_unlocked: boolean
  specialized_unlocked: boolean
  version: number
}

export type WeakKnowledgePoint = {
  knowledge_id: string
  knowledge_name: string
  attempts: number
  accuracy: number
  true_exam_total: number
  true_exam_completed: number
}

export type SpecializedScope = {
  chapter_id: string
  chapter_order: number
  chapter_code: string
  chapter_name: string
  strengthened: boolean
  true_exam_total: number
  true_exam_completed: number
  specialized_unlocked: boolean
  weak_points: WeakKnowledgePoint[]
}

export type ResourceMapping = {
  knowledge_id: string
  knowledge_name: string
  confidence: number
  confirmed: boolean
}

export type ResourceSectionReview = {
  id: string
  title: string
  section_path: string
  level: number
  sequence: number
  page_start: number | null
  page_end: number | null
  version: number
  mappings: ResourceMapping[]
}

export type LearningResource = {
  id: string
  title: string
  resource_type: string
  status: string
  description: string
  version: number
  published_at: string | null
}

export type PublishedResourceSection = {
  id: string
  title: string
  resource_id: string
  resource_title: string
  resource_type: string
  knowledge_id: string
  page_start: number | null
  page_end: number | null
  suggested_units: number | null
  unit_type: string | null
}

export type ChapterSessionQuestion = ExamQuestion & { completed_at: string | null }
export type ChapterSession = {
  id: string
  knowledge_id: string
  question_snapshot_version: string
  status: string
  total_questions: number
  completed_questions: number
  completed_at: string | null
  questions?: ChapterSessionQuestion[]
}

export type PracticeQuestion = {
  id: string
  code: string
  content: string
  question_type: string
  difficulty: string
  score: number
}

export type PracticeTask = {
  id: string
  task_type: string
  estimated_min_minutes: number
  estimated_max_minutes: number
  status: string
  questions: PracticeQuestion[]
}

export type FeedbackResult = {
  feedback_id: string
  requires_agent: boolean
  reason_codes: string[]
  agent_job_id: string | null
}

export type Proposal = {
  id: string
  proposal_type: string
  status: string
  payload: Record<string, unknown>
  reason_codes: string[]
  confidence: number
  evidence_refs: string[]
  evidence_snapshot: Array<Record<string, unknown>>
  approval_expires_at: string | null
  reviewer_user_id: string | null
  review_reason: string | null
  applied_at: string | null
  apply_error_code: string | null
}

export type AgentRun = {
  id: string
  goal: string
  status: string
  model_version: string
  prompt_version: string
  policy_version: string
  loop_count: number
  model_call_count: number
  tool_call_count: number
  input_tokens: number
  output_tokens: number
  resumed_count: number
  termination_reason: string | null
  steps: Array<{
    step_number: number
    action: Record<string, unknown>
    model_name: string
    input_tokens: number
    output_tokens: number
    latency_ms: number
  }>
  tools: Array<{
    id: string
    tool_name: string
    tool_version: string
    status: string
    latency_ms: number
    retry_count: number
    error_code: string | null
    replayed: boolean
    created_at: string
  }>
  proposals: Proposal[]
}

export type AgentReplay = {
  run_id: string
  status: string
  termination_reason: string | null
  read_only: true
  side_effects_executed: false
  timeline: Array<{
    step_number: number
    action: Record<string, unknown>
    model_attempts: Array<{
      attempt_number: number
      purpose: string
      model_name: string
      status: string
      input_tokens: number
      output_tokens: number
      latency_ms: number
      error_code: string | null
    }>
    tool: null | {
      name: string
      status: string
      retry_count: number
      error_code: string | null
      replayed: boolean
      observation: string | null
    }
    guardrails: Array<{ decision: string; reason_code: string; tool_name: string | null }>
    checkpoint: null | {
      version: number
      state_hash: string
      resume_safe: boolean
      fencing_token: number
    }
  }>
  proposal_ids: string[]
}

export type ShadowEvaluation = {
  id: string
  source_run_id: string
  job_id: string | null
  status: string
  baseline_model: string
  baseline_prompt_version: string
  baseline_decision: string | null
  baseline_confidence: number | null
  candidate_model: string
  candidate_prompt_version: string
  candidate_decision: string | null
  candidate_confidence: number | null
  comparison: Record<string, unknown> | null
  error_code: string | null
}

export type TrueExam = {
  id: string
  year: number
  title: string
  total_score: number
  duration_minutes: number
}

export type ExamQuestion = {
  id: string
  sequence: number
  code: string
  content: string
  question_type: string
  difficulty: string
  score: number
}

export type TrueExamDetail = TrueExam & { questions: ExamQuestion[] }

export type TrueExamProfile = {
  knowledge_id: string
  attempt_count: number
  accuracy: number
  average_score_ratio: number
  average_duration_seconds: number
}

export type MockExam = {
  id: string
  mock_type: string
  status: string
  total_score: number
  duration_minutes: number
  target_knowledge_id: string | null
  strategy_version: string
  validation_result: Record<string, unknown> | null
  questions: ExamQuestion[]
}

export type GeneratedQuestion = {
  id: string
  mock_exam_id: string | null
  content: string
  answer: string
  solution: string
  metadata_json: Record<string, unknown>
  generator_model: string
  prompt_version: string
  validation_result: Record<string, unknown>
  quality_status: string
  created_at: string
}
