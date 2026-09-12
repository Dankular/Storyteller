import { useState } from 'react'
import { useJob } from '../useJob'

const STATUS_COLORS: Record<string, string> = {
  idle: '#888',
  queued: '#888',
  running: '#2563eb',
  succeeded: '#16a34a',
  failed: '#dc2626',
}

export function JobRow({
  id,
  label,
  onDismiss,
}: {
  id: string
  label: string
  onDismiss: () => void
}) {
  const { status, progress, error } = useJob(id)
  const [expanded, setExpanded] = useState(false)
  const lastLine = progress[progress.length - 1] ?? 'Starting…'
  const isDone = status === 'succeeded' || status === 'failed'

  return (
    <div className="job-row">
      <div className="job-row-header" onClick={() => setExpanded((e) => !e)}>
        <span className="job-dot" style={{ background: STATUS_COLORS[status] }} />
        <span className="job-label">{label}</span>
        <span className="job-status">{status}</span>
        {isDone && (
          <button
            className="job-dismiss"
            onClick={(e) => {
              e.stopPropagation()
              onDismiss()
            }}
          >
            ×
          </button>
        )}
      </div>
      {!expanded && <div className="job-last-line">{error ?? lastLine}</div>}
      {expanded && (
        <div className="job-log">
          {progress.map((line, i) => (
            <div key={i}>{line}</div>
          ))}
          {error && <div className="job-error">{error}</div>}
        </div>
      )}
    </div>
  )
}
