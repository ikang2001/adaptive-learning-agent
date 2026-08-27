const TASK_LABELS: Record<string, string> = {
  COURSE_LEARNING: '课程学习',
  HANDOUT_PRACTICE: '讲义习题',
  ERROR_REVIEW: '错题复盘',
  KNOWLEDGE_SUMMARY: '知识总结',
  CHAPTER_TRUE_EXAM: '章节真题',
  FULL_TRUE_EXAM: '整卷真题',
  SPECIALIZED_PRACTICE: '专项强化',
  THEORY_REVIEW: '理论回看',
  BASIC_QUESTION: '基础训练',
  MEDIUM_QUESTION: '进阶训练',
  COMPREHENSIVE_QUESTION: '综合训练',
  TRUE_EXAM_QUESTION: '真题训练',
  WRONG_QUESTION_REVIEW: '错题复盘',
  MOCK_EXAM: '模拟考试',
}

const STAGE_LABELS: Record<string, string> = {
  FOUNDATION: '基础阶段',
  STRENGTHEN: '强化阶段',
  TRUE_EXAM: '真题阶段',
  MOCK_EXAM: '模拟阶段',
  SPRINT: '冲刺阶段',
}

const REASON_LABELS: Record<string, string> = {
  TIME_OVERRUN: '用时显著超出预计',
  LOW_ACCURACY: '近期正确率偏低',
  REPEATED_ERROR: '同类错误连续出现',
  LOW_COMPLETION: '连续计划完成度偏低',
}

export const taskLabel = (value: string): string => TASK_LABELS[value] ?? value
export const stageLabel = (value: string): string => STAGE_LABELS[value] ?? value
export const reasonLabel = (value: string): string => REASON_LABELS[value] ?? value

export function formatDate(value: string): string {
  return new Intl.DateTimeFormat('zh-CN', { month: 'short', day: 'numeric', weekday: 'short' }).format(
    new Date(`${value}T00:00:00`),
  )
}

export function toIsoDate(date: Date): string {
  const year = date.getFullYear()
  const month = String(date.getMonth() + 1).padStart(2, '0')
  const day = String(date.getDate()).padStart(2, '0')
  return `${year}-${month}-${day}`
}
