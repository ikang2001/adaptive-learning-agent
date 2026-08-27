import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { SpecializedScope } from '../../api/types'
import { SpecializedScopeSelector } from './SpecializedScopeSelector'

const scopes: SpecializedScope[] = [
  {
    chapter_id: 'chapter-1',
    chapter_order: 1,
    chapter_code: 'SYS',
    chapter_name: '控制系统基础',
    strengthened: true,
    true_exam_total: 5,
    true_exam_completed: 5,
    specialized_unlocked: true,
    weak_points: [
      { knowledge_id: 'detail-1', knowledge_name: '传递函数', attempts: 3, accuracy: 0.33, true_exam_total: 3, true_exam_completed: 3 },
    ],
  },
  {
    chapter_id: 'chapter-2',
    chapter_order: 2,
    chapter_code: 'TIME',
    chapter_name: '时域分析',
    strengthened: false,
    true_exam_total: 4,
    true_exam_completed: 1,
    specialized_unlocked: false,
    weak_points: [],
  },
]

describe('SpecializedScopeSelector', () => {
  it('offers syllabus chapters and explains the selected chapter weak points', () => {
    const onChange = vi.fn()
    const { rerender } = render(
      <SpecializedScopeSelector scopes={scopes} value="" onChange={onChange} />,
    )

    const selector = screen.getByLabelText('目标考纲章节')
    expect(screen.getAllByRole('option').map((option) => option.textContent)).toEqual([
      '选择院校真题考纲章节',
      '第 1 章 · 控制系统基础',
      '第 2 章 · 时域分析',
    ])
    fireEvent.change(selector, { target: { value: 'chapter-1' } })
    expect(onChange).toHaveBeenCalledWith('chapter-1')

    rerender(<SpecializedScopeSelector scopes={scopes} value="chapter-1" onChange={onChange} />)
    expect(screen.getByLabelText('控制系统基础章内薄弱知识点')).toHaveTextContent('传递函数')
    expect(screen.getByText('正确率 33%')).toBeInTheDocument()
  })
})
