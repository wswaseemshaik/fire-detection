import { useEffect, useState } from 'react'
import { api, fmtBytes, fmtTime, type Media } from '../api'
import { useLive } from '../ws'

export default function Recordings() {
  const { sources } = useLive()
  const [items, setItems] = useState<Media[]>([])
  const [kind, setKind] = useState('')
  const [play, setPlay] = useState<Media | null>(null)
  const load = () => api.media().then(setItems).catch(() => {})
  useEffect(() => { load(); const t = setInterval(load, 10000); return () => clearInterval(t) }, [])
  const names = new Map(sources.map((s) => [s.id, s.name]))
  const shown = items.filter((m) => !kind || m.kind === kind)
  const total = shown.reduce((a, m) => a + m.size, 0)
  return (
    <>
      <header className="page-head">
        <h1>Recordings</h1>
        <select value={kind} onChange={(e) => setKind(e.target.value)}><option value="">All</option><option value="recording">Continuous recordings</option><option value="clip">Alert clips</option><option value="job">Batch outputs</option></select>
        <span className="grow" /><span className="muted">{shown.length} files · {fmtBytes(total)}</span>
      </header>
      {shown.length === 0 ? <div className="empty">No recordings yet. Turn on <b>record</b> for a source, or alerts will save clips automatically when enabled in Settings.</div> : (
        <section className="card">
          <table>
            <thead><tr><th>File</th><th>Type</th><th>Source</th><th>Size</th><th>Created</th><th></th></tr></thead>
            <tbody>{shown.map((m) => (
              <tr key={m.url}>
                <td className="link" onClick={() => setPlay(m)}>▶ {m.name}</td>
                <td><span className={'tag ' + (m.kind === 'clip' ? 'fire' : '')}>{m.kind}</span></td>
                <td>{m.source_id ? names.get(m.source_id) ?? m.source_id : ''}</td>
                <td>{fmtBytes(m.size)}</td><td>{fmtTime(m.mtime)}</td>
                <td className="row gap"><a className="btn ghost sm" href={m.url} download>↓</a><button className="danger sm" onClick={() => confirm(`Delete ${m.name}?`) && api.deleteMedia(m.kind + 's', m.name).then(load)}>✕</button></td>
              </tr>
            ))}</tbody>
          </table>
        </section>
      )}
      {play && (
        <div className="modal-bg" onClick={() => setPlay(null)}>
          <div className="modal wide" onClick={(e) => e.stopPropagation()}>
            <div className="modal-head"><b>{play.name}</b><button className="ghost sm" onClick={() => setPlay(null)}>✕</button></div>
            <video src={play.url} controls autoPlay />
          </div>
        </div>
      )}
    </>
  )
}
