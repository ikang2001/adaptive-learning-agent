import { describe, expect, it } from 'vitest'
import { isMockJobRunning } from './mockJobState'

describe('Mock Planner job state', () => {
  it('shows progress only while the job is queued or running', () => {
    expect(isMockJobRunning(undefined)).toBe(true)
    expect(isMockJobRunning('QUEUED')).toBe(true)
    expect(isMockJobRunning('RUNNING')).toBe(true)
    expect(isMockJobRunning('RETRY_WAIT')).toBe(true)
    expect(isMockJobRunning('SUCCEEDED')).toBe(false)
    expect(isMockJobRunning('WAITING_FOR_REVIEW')).toBe(false)
    expect(isMockJobRunning('FAILED')).toBe(false)
  })
})
