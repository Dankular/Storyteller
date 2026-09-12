import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter, Route, Routes } from 'react-router-dom'
import './index.css'
import { JobsProvider } from './JobsContext'
import { JobTray } from './components/JobTray'
import { Library } from './pages/Library'
import { Workspace } from './pages/Workspace'

// The manuscript is the primary surface: both project routes render Workspace, which resolves
// which chapter to show (explicit :chapterId, else the last one this browser had open, else the
// first in outline order) and hosts Characters/Story(Dashboard)/Plan/Continuity as drawers rather
// than separate destinations -- see Workspace.tsx.
createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <JobsProvider>
      <BrowserRouter>
        <Routes>
          <Route path="/" element={<Library />} />
          <Route path="/projects/:projectId" element={<Workspace />} />
          <Route path="/projects/:projectId/chapters/:chapterId" element={<Workspace />} />
        </Routes>
        <JobTray />
      </BrowserRouter>
    </JobsProvider>
  </StrictMode>,
)
