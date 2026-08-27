import { describe, expect, it } from 'vitest'
import { reasonLabel, stageLabel, taskLabel, toIsoDate } from './format'

describe('format helpers', () => {
  it('translates domain enum values into learner-facing labels', () => {
    expect(taskLabel('BASIC_QUESTION')).toBe('基础训练')
    expect(stageLabel('TRUE_EXAM')).toBe('真题阶段')
    expect(reasonLabel('TIME_OVERRUN')).toBe('用时显著超出预计')
  })

  it('formats a local date without UTC drift', () => {
    expect(toIsoDate(new Date(2026, 7, 27, 8))).toBe('2026-08-27')
  })
})

