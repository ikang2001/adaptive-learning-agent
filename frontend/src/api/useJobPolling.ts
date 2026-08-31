import { useQuery } from '@tanstack/react-query'
import { apiRequest } from './client'
import type { BackgroundJob } from './types'

const terminalStatuses = new Set(['SUCCEEDED', 'FAILED', 'DEAD_LETTER', 'CANCELLED', 'WAITING_FOR_REVIEW'])

export function useJobPolling(jobId: string | null) {
  return useQuery({
    queryKey: ['job', jobId],
    queryFn: () => apiRequest<BackgroundJob>(`/jobs/${jobId}`),
    enabled: Boolean(jobId),
    refetchInterval: (query) => {
      const status = query.state.data?.status
      return status && terminalStatuses.has(status) ? false : 1_000
    },
  })
}

