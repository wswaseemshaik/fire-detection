import { useEffect, useState } from 'react'
import { api, fmtTime, type Event } from '../api'
import { useLive } from '../ws'

export default function Alerts() {
  const { sources, alertCount } = useLive()
  const [events, setEvents] = useState<Event[]>([])
  const [source, setSource] = useState('')
  const [label, setLabel] = useState('')
  const [range, setRange] = useState('')
  const [open, setOpen] = useState<Event | null>(null)
  const load = () => {
    const since = range ? new Date(Date.now() - Number(range) * 1000).toISOString() : ''
    api.events({ limit: 500, source_id: source, label, since }).then(setEvents).catch(() => {})
  }
  useEffect(load, [source, label, range, alertCount])
  const names = new Map(sources.map((s) => [s.id, s.name]))
  return (
    <>
      <header className="page-head">
        <h1>Alerts</h1>
        <select value={source} onChange={(e) => setSource(e.target.value)}><option value="">All sources</option>{sources.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}</select>
        <select value={label} onChange={(e) => setLabel(e.target.value)}><option value="">Fire + smoke</option><option value="fire">Fire only</option><option value="smoke">Smoke only</option></select>
        <select value={range} onChange={(e) => setRange(e.target.value)}><option value="">All time</option><option value="3600">Last hour</option><option value="86400">Last 24 h</option><option value="604800">Last 7 days</option></select>
        <span className="grow" />
        <span className="muted">{events.length} shown</span>
        <button className="danger sm" onClick={async () => { if (confirm('Delete ALL alerts, snapshots and clips?')) { await api.clearEvents(); load() } }}>Clear all</button>
      </header>
      {events.length === 0 ? <div className="empty">No alerts match.</div> : (
        <div className="evgrid">
          {events.map((e) => (
            <div key={e.id} className="ev" onClick={() => setOpen(e)}>
              <img src={e.snapshot} loading="lazy" alt="" />
              <div className="ev-body">
                <div>{e.labels.map((l) => <span key={l} className={'tag ' + l}>{l}</span>)} <b>{(e.max_confidence * 100).toFixed(0)}%</b>{e.clip && <span className="muted"> · 🎬 clip</span>}</div>
                <div>{names.get(e.source_id) ?? e.source_name}</div>
                <small className="muted">{fmtTime(e.timestamp)}</small>
              </div>
            </div>
          ))}
        </div>
      )}
      {open && (
        <div className="modal-bg" onClick={() => setOpen(null)}>
          <div className="modal wide" onClick={(e) => e.stopPropagation()}>
            <div className="modal-head">
              <b>{open.labels.join(' + ').toUpperCase()} · {open.source_name} · {fmtTime(open.timestamp)}</b>
              <span className="row gap">
                <button className="danger sm" onClick={async () => { await api.deleteEvent(open.id); setOpen(null); load() }}>Delete</button>
                <button className="ghost sm" onClick={() => setOpen(null)}>✕</button>
              </span>
            </div>
            {open.clip ? <video src={open.clip} controls autoPlay poster={open.snapshot} /> : <img src={open.snapshot} alt="" />}
            <div className="row gap wrap pad">
              {open.detections.map((d, i) => <span key={i} className={'tag ' + d.label}>{d.label} {(d.confidence * 100).toFixed(0)}% @ {d.x1},{d.y1}–{d.x2},{d.y2}</span>)}
              <span className="grow" />
              <a className="muted" href={open.snapshot} target="_blank" rel="noreferrer">snapshot ↗</a>
              {open.clip && <a className="muted" href={open.clip} download>download clip ↓</a>}
            </div>
          </div>
        </div>
      )}
    </>
  )
}
