import { createContext, useContext, useState, type ReactNode } from 'react'

export interface TrackedJob {
  id: string
  label: string
}

interface JobsContextValue {
  jobs: TrackedJob[]
  track: (id: string, label: string) => void
  dismiss: (id: string) => void
}

const JobsContext = createContext<JobsContextValue | null>(null)

/** Wraps the whole app (see main.tsx) so any page can start a job (generate/plan/audit) and hand
 * it to the global tray (JobTray.tsx) to show live progress, without prop-drilling through every
 * route. */
export function JobsProvider({ children }: { children: ReactNode }) {
  const [jobs, setJobs] = useState<TrackedJob[]>([])

  const track = (id: string, label: string) => setJobs((prev) => [...prev, { id, label }])
  const dismiss = (id: string) => setJobs((prev) => prev.filter((j) => j.id !== id))

  return <JobsContext.Provider value={{ jobs, track, dismiss }}>{children}</JobsContext.Provider>
}

export function useJobsContext(): JobsContextValue {
  const ctx = useContext(JobsContext)
  if (!ctx) throw new Error('useJobsContext must be used within a JobsProvider')
  return ctx
}
