import { createContext, useContext, useEffect, useRef, useState } from 'react'
import type { Event, Job, Source } from './api'

export type Live = { sources: Source[]; jobs: Job[]; connected: boolean; lastAlert: Event | null; alertCount: number }
export const LiveContext = createContext<Live>({ sources: [], jobs: [], connected: false, lastAlert: null, alertCount: 0 })
export const useLive = () => useContext(LiveContext)

export function useLiveFeed(onAlert: (e: Event) => void): Live {
  const [state, setState] = useState<Live>({ sources: [], jobs: [], connected: false, lastAlert: null, alertCount: 0 })
  const cb = useRef(onAlert)
  cb.current = onAlert
  useEffect(() => {
    let ws: WebSocket | null = null
    let timer: ReturnType<typeof setTimeout>
    let closed = false
    const connect = () => {
      ws = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws/events`)
      ws.onopen = () => setState((s) => ({ ...s, connected: true }))
      ws.onmessage = (m) => {
        const d = JSON.parse(m.data)
        if (d.type === 'status') setState((s) => ({ ...s, sources: d.sources, jobs: d.jobs ?? s.jobs }))
        else if (d.type === 'alert') {
          setState((s) => ({ ...s, lastAlert: d.event, alertCount: s.alertCount + 1 }))
          cb.current(d.event)
        }
      }
      ws.onclose = () => { setState((s) => ({ ...s, connected: false })); if (!closed) timer = setTimeout(connect, 2000) }
    }
    connect()
    return () => { closed = true; clearTimeout(timer); ws?.close() }
  }, [])
  return state
}
