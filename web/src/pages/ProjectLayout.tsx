import { NavLink, Outlet, useParams } from 'react-router-dom'

/** Wraps every project-scoped page (dashboard/characters/outline/plan/continuity) with the tab
 * nav. The project id comes from the route (/projects/:projectId/...); each page reads it via
 * useParams() itself rather than this layout fetching+passing project state down, so a page can
 * refetch on its own schedule after an edit. */
export function ProjectLayout() {
  const { projectId } = useParams<{ projectId: string }>()

  return (
    <div className="project-layout">
      <nav className="project-nav">
        <NavLink to={`/projects/${projectId}`} end>
          Dashboard
        </NavLink>
        <NavLink to={`/projects/${projectId}/characters`}>Characters</NavLink>
        <NavLink to={`/projects/${projectId}/outline`}>Outline</NavLink>
        <NavLink to={`/projects/${projectId}/plan`}>Plan</NavLink>
        <NavLink to={`/projects/${projectId}/continuity`}>Continuity</NavLink>
        <NavLink to="/">← Library</NavLink>
      </nav>
      <div className="project-content">
        <Outlet />
      </div>
    </div>
  )
}
