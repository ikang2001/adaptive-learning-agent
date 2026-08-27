import { useQuery } from '@tanstack/react-query'
import { Navigate, Route, Routes } from 'react-router-dom'
import { ApiError, apiRequest } from './api/client'
import type { StudentProfile } from './api/types'
import { AppShell } from './components/AppShell'
import { ErrorState, LoadingState } from './components/ui'
import { AgentPage } from './features/agent/AgentPage'
import { LoginPage } from './features/auth/LoginPage'
import { useAuth } from './features/auth/AuthContext'
import { DashboardPage } from './features/dashboard/DashboardPage'
import { MockExamsPage } from './features/exams/MockExamsPage'
import { TrueExamsPage } from './features/exams/TrueExamsPage'
import { SetupPage } from './features/onboarding/SetupPage'
import { PlanPage } from './features/plans/PlanPage'
import { PracticePage } from './features/practice/PracticePage'
import { ReviewPage } from './features/review/ReviewPage'

function Protected({ children }: { children: React.ReactNode }) {
  const { authenticated } = useAuth()
  return authenticated ? children : <Navigate to="/login" replace />
}

function ProfileGate() {
  const profile = useQuery({
    queryKey: ['student-profile'],
    queryFn: () => apiRequest<StudentProfile>('/me/student-profile'),
    retry: false,
  })
  if (profile.isLoading) return <LoadingState label="正在恢复你的学习状态" />
  if (profile.error instanceof ApiError && profile.error.status === 404) return <Navigate to="/setup" replace />
  if (profile.isError) return <ErrorState message="学生画像暂时无法读取" onRetry={() => void profile.refetch()} />
  return <AppShell />
}

export function App() {
  const { authenticated } = useAuth()
  return (
    <Routes>
      <Route path="/login" element={authenticated ? <Navigate to="/" replace /> : <LoginPage />} />
      <Route path="/setup" element={<Protected><SetupPage /></Protected>} />
      <Route element={<Protected><ProfileGate /></Protected>}>
        <Route index element={<DashboardPage />} />
        <Route path="practice" element={<PracticePage />} />
        <Route path="plan" element={<PlanPage />} />
        <Route path="true-exams" element={<TrueExamsPage />} />
        <Route path="mock-exams" element={<MockExamsPage />} />
        <Route path="agent" element={<AgentPage />} />
        <Route path="review" element={<ReviewPage />} />
      </Route>
      <Route path="*" element={<Navigate to={authenticated ? '/' : '/login'} replace />} />
    </Routes>
  )
}
