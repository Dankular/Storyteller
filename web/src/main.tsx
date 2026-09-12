import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter, Route, Routes } from 'react-router-dom'
import './index.css'
import { JobsProvider } from './JobsContext'
import { JobTray } from './components/JobTray'
import { Library } from './pages/Library'
import { ProjectLayout } from './pages/ProjectLayout'
import { Dashboard } from './pages/Dashboard'
import { Characters } from './pages/Characters'
import { Outline } from './pages/Outline'
import { ChapterDetail } from './pages/ChapterDetail'
import { Plan } from './pages/Plan'
import { Continuity } from './pages/Continuity'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <JobsProvider>
      <BrowserRouter>
        <Routes>
          <Route path="/" element={<Library />} />
          <Route path="/projects/:projectId" element={<ProjectLayout />}>
            <Route index element={<Dashboard />} />
            <Route path="characters" element={<Characters />} />
            <Route path="outline" element={<Outline />} />
            <Route path="outline/:chapterId" element={<ChapterDetail />} />
            <Route path="plan" element={<Plan />} />
            <Route path="continuity" element={<Continuity />} />
          </Route>
        </Routes>
        <JobTray />
      </BrowserRouter>
    </JobsProvider>
  </StrictMode>,
)
