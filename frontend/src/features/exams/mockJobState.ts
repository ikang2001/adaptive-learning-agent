export function isMockJobRunning(status: string | undefined): boolean {
  return status === undefined || status === 'QUEUED' || status === 'RUNNING'
}
