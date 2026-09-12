import { useEffect, useRef, useState } from 'react'
import { jobWebSocketUrl } from './api'
import type { JobStatus } from './types'

export interface JobLiveState {
  status: JobStatus | 'idle'
  progress: string[]
  /** Raw content streamed via "chunk" messages (see webapi/jobs.py's on_chunk) -- only
   * continue_chapter's job actually sends these; every other job leaves this empty. Unlike
   * `progress`, this is NOT replayed on reconnect (chunks are live-only, see ws.py) -- once a job
   * is done, `result` holds the authoritative final text instead. */
  streamedText: string
  result: unknown
  error: string | null
}

const INITIAL: JobLiveState = { status: 'idle', progress: [], streamedText: '', result: null, error: null }

/** Subscribes to a job's live progress over WebSocket (see novel_harness/webapi/ws.py). Pass
 * `null` to not subscribe to anything yet. Re-subscribes automatically if `jobId` changes. */
export function useJob(jobId: string | null): JobLiveState {
  const [state, setState] = useState<JobLiveState>(INITIAL)
  const socketRef = useRef<WebSocket | null>(null)

  useEffect(() => {
    if (!jobId) return
    setState({ ...INITIAL, status: 'queued' })
    const ws = new WebSocket(jobWebSocketUrl(jobId))
    socketRef.current = ws

    ws.onmessage = (event) => {
      const msg = JSON.parse(event.data)
      if (msg.type === 'progress') {
        setState((s) => ({ ...s, progress: [...s.progress, msg.line] }))
      } else if (msg.type === 'chunk') {
        setState((s) => ({ ...s, streamedText: s.streamedText + msg.text }))
      } else if (msg.type === 'status') {
        setState((s) => ({ ...s, status: msg.status }))
      } else if (msg.type === 'done') {
        setState((s) => ({ ...s, status: msg.status, result: msg.result, error: msg.error }))
      } else if (msg.type === 'error') {
        setState((s) => ({ ...s, status: 'failed', error: msg.message }))
      }
    }
    ws.onerror = () => setState((s) => ({ ...s, error: s.error ?? 'WebSocket connection error' }))

    return () => ws.close()
  }, [jobId])

  return state
}
