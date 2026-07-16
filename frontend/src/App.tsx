import { HashRouter, Routes, Route } from "react-router-dom"
import Layout from "@/components/Layout"
import ErrorBoundary from "@/components/ErrorBoundary"
import UploadPage from "@/pages/UploadPage"
import ProgressPage from "@/pages/ProgressPage"
import ReportPage from "@/pages/ReportPage"
import LibraryPage from "@/pages/LibraryPage"
import StoryboardPage from "@/pages/StoryboardPage"
import SettingsPage from "@/pages/SettingsPage"

function NotFound() {
  return (
    <div className="flex flex-col items-center justify-center py-48 text-center">
      <p className="text-6xl font-serif font-light text-ink/10 mb-4">404</p>
      <p className="text-sm text-muted font-serif italic">页面不存在</p>
    </div>
  )
}

export default function App() {
  return (
    <HashRouter>
      <Routes>
        <Route path="/" element={<ErrorBoundary><Layout /></ErrorBoundary>}>
          <Route index element={<UploadPage />} />
          <Route path="/jobs/:jobId" element={<ProgressPage />} />
          <Route path="/jobs/:jobId/report" element={<ReportPage />} />
          <Route path="/library" element={<LibraryPage />} />
          <Route path="/storyboard" element={<StoryboardPage />} />
          <Route path="/settings" element={<SettingsPage />} />
          <Route path="*" element={<NotFound />} />
        </Route>
      </Routes>
    </HashRouter>
  )
}
