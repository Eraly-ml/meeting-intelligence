import { lazy, Suspense } from 'react'
import { Routes, Route, Navigate } from 'react-router-dom'
import { meetingStationMode } from '@/lib/stationMode'

// Lazy load route components for better performance
const Dashboard = lazy(() => import("@/features/transcription/components/Dashboard").then(module => ({ default: module.Dashboard })));
const AudioDetailView = lazy(() => import("@/features/transcription/components/AudioDetailView").then(module => ({ default: module.AudioDetailView })));
const Settings = lazy(() => import('@/features/settings/pages/SettingsPage').then(module => ({ default: module.Settings })))
const CLISettings = lazy(() => import('@/features/settings/pages/CLISettingsPage').then(module => ({ default: module.CLISettings })))
const CLIAuthConfirmation = lazy(() => import('./features/auth/components/CLIAuthConfirmation').then(module => ({ default: module.CLIAuthConfirmation })))
const MeetingIntelligence = lazy(() => import('./features/meeting-intelligence/MeetingIntelligencePage').then(module => ({ default: module.MeetingIntelligencePage })))


// Loading component
const PageLoader = () => (
  <div className="flex items-center justify-center min-h-screen">
    <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-600"></div>
  </div>
)

function App() {
  return (
    <Suspense fallback={<PageLoader />}>
      <Routes>
        <Route path="/" element={meetingStationMode ? <Navigate to="/meeting-intelligence" replace /> : <Dashboard />} />
        {!meetingStationMode && <Route path="/audio/:audioId" element={<AudioDetailView />} />}
        <Route path="/meeting-intelligence" element={<MeetingIntelligence />} />

        {!meetingStationMode && <Route path="/settings" element={<Settings />} />}
        {!meetingStationMode && <Route path="/settings/cli" element={<CLISettings />} />}
        {!meetingStationMode && <Route path="/auth/cli/authorize" element={<CLIAuthConfirmation />} />}

        {/* Fallback */}
        <Route path="*" element={<Navigate to={meetingStationMode ? '/meeting-intelligence' : '/'} replace />} />
      </Routes>
    </Suspense>
  )
}

export default App
