import { useState } from 'react'
import { api, type Source } from '../api'

export default function SourceCard({ s, compact }: { s: Source; compact?: boolean }) {
  const [busy, setBusy] = useState(false)
  const act = async (fn: () => Promise<unknown>) => { setBusy(true); try { await fn() } catch (e: any) { alert(e.message) } finally { setBusy(false) } }
  const labels = [...new Set(s.detections.map((d) => d.label))]
  const status = s.paused ? 'paused' : s.connected ? `${s.fps} fps` : s.ended ? 'ended' : s.error || 'connecting…'
  return (
    <div className={'cam' + (s.alarm ? ' alarm' : '')}>
      <div className="cam-img">
        <img src={`/api/sources/${s.id}/stream`} alt={s.name} />
        {s.progress != null && <div className="progress"><i style={{ width: `${s.progress * 100}%` }} /></div>}
        {s.recording && <span className="rec">● REC</span>}
      </div>
      <div className="bar">
        <span className={'dot ' + (s.paused ? '' : s.connected ? 'on' : s.ended ? '' : 'off')} />
        <span className="name" title={s.source}>{s.name}</span>
        {labels.map((l) => <span key={l} className={'tag ' + l}>{l}</span>)}
        <span className="meta">{s.resolution ? s.resolution + ' · ' : ''}{status}{s.alerts ? ` · ${s.alerts} alerts` : ''}</span>
        {!compact && (
          <span className="row gap">
            <button className="ghost sm" disabled={busy} title={s.paused ? 'Resume detection' : 'Pause detection'} onClick={() => act(() => api.patchSource(s.id, { paused: !s.paused }))}>{s.paused ? '▶' : '⏸'}</button>
            <button className={'ghost sm' + (s.recording ? ' active' : '')} disabled={busy} title="Toggle recording" onClick={() => act(() => api.patchSource(s.id, { record: !s.recording }))}>⏺</button>
            <button className="ghost sm" disabled={busy} title="Reconnect / restart" onClick={() => act(() => api.patchSource(s.id, { restart: true }))}>↻</button>
            <button className="danger sm" disabled={busy} title="Remove" onClick={() => confirm(`Remove ${s.name}?`) && act(() => api.removeSource(s.id))}>✕</button>
          </span>
        )}
      </div>
    </div>
  )
}
