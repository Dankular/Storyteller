import { useJobsContext } from '../JobsContext'
import { JobRow } from './JobRow'

/** The global "responsive engine" surface: every in-flight or recently finished job (generate a
 * chapter, plan the outline/book, run an audit) shows here with live progress, regardless of
 * which page started it or which page is currently open. */
export function JobTray() {
  const { jobs, dismiss } = useJobsContext()
  if (jobs.length === 0) return null

  return (
    <div className="job-tray">
      {jobs.map((j) => (
        <JobRow key={j.id} id={j.id} label={j.label} onDismiss={() => dismiss(j.id)} />
      ))}
    </div>
  )
}
