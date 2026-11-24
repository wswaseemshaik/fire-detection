import { useEffect, useState } from 'react'
import { api, fmtDur, fmtTime, type Job } from '../api'
import FileBrowser from '../components/FileBrowser'
import { useLive } from '../ws'

export default function Batch() {
  const live = useLive()
  const [jobs, setJobs] = useState<Job[]>([])
  const [path, setPath] = useState('')
  const [recursive, setRecursive] = useState(false)
  const [fps, setFps] = useState(2)
  const [annotate, setAnnotate] = useState(false)
  const [browse, setBrowse] = useState(false)
  const [err, setErr] = useState('')
  const [open, setOpen] = useState<Job | null>(null)
  const load = () => api.jobs().then(setJobs).catch(() => {})
  useEffect(() => { load() }, [])
  useEffect(() => { load() }, [live.jobs.map((j) => j.id + j.status).join()])
  useEffect(() => {
    if (!open || open.status !== 'running') return
    const t = setInterval(() => api.job(open.id).then(setOpen), 1500)
    return () => clearInterval(t)
  }, [open?.id, open?.status])
  const merged = jobs.map((j) => live.jobs.find((l) => l.id === j.id) ?? j)
  const create = async (e: React.FormEvent) => {
    e.preventDefault(); setErr('')
    try { const j = await api.createJob({ path: path.trim(), recursive, sample_fps: fps, annotate }); setPath(''); load(); setOpen(await api.job(j.id)) } catch (e: any) { setErr(e.message) }
  }
  return (
    <>
      <header className="page-head"><h1>Batch scan</h1></header>
      <section className="card">
        <p className="muted">Scan a folder (or a single file) as fast as possible and get a report of where fire/smoke appears in each video. Does not affect live sources.</p>
        <form onSubmit={create} className="col gap">
          <div className="row gap">
            <input className="grow" value={path} onChange={(e) => setPath(e.target.value)} placeholder="/path/to/folder or /path/to/video.mp4" required />
            <button type="button" className="ghost" onClick={() => setBrowse(true)}>Browse…</button>
          </div>
          <div className="row gap wrap">
            <label className="chk"><input type="checkbox" checked={recursive} onChange={(e) => setRecursive(e.target.checked)} /> include subfolders</label>
            <label className="chk">sample <input type="number" min={0.1} step={0.5} value={fps} onChange={(e) => setFps(Number(e.target.value))} style={{ width: 70 }} /> frames/s</label>
            <label className="chk"><input type="checkbox" checked={annotate} onChange={(e) => setAnnotate(e.target.checked)} /> write annotated videos (slower)</label>
            <span className="grow" /><button>Start scan</button>
          </div>
        </form>
        {err && <div className="err">{err}</div>}
      </section>
      <section className="card">
        <h2>Jobs</h2>
        {merged.length === 0 && <div className="muted pad">No jobs yet.</div>}
        {merged.length > 0 && (
          <table>
            <thead><tr><th>Folder</th><th>Status</th><th>Progress</th><th>Files</th><th>With detections</th><th>Started</th><th>Took</th><th></th></tr></thead>
            <tbody>{merged.map((j) => (
              <tr key={j.id}>
                <td className="link" onClick={() => api.job(j.id).then(setOpen)}>{j.label}</td>
                <td><span className={'tag ' + (j.status === 'done' ? 'ok' : j.status === 'running' ? 'run' : j.status === 'failed' ? 'fire' : '')}>{j.status}</span></td>
                <td><div className="progress inline"><i style={{ width: `${j.progress * 100}%` }} /></div> {(j.progress * 100).toFixed(0)}% {j.status === 'running' && <small className="muted">{j.current_file}</small>}</td>
                <td>{j.files}</td><td>{j.positives}</td>
                <td>{j.started ? fmtTime(j.started) : '–'}</td>
                <td>{j.started ? fmtDur((j.finished ?? Date.now() / 1000) - j.started) : '–'}</td>
                <td className="row gap">
                  {j.status === 'running' ? <button className="ghost sm" onClick={() => api.cancelJob(j.id).then(load)}>Cancel</button> : <a className="btn ghost sm" href={`/api/jobs/${j.id}/report.csv`}>CSV</a>}
                  <button className="danger sm" onClick={() => confirm('Delete job and its outputs?') && api.deleteJob(j.id).then(load)}>✕</button>
                </td>
              </tr>
            ))}</tbody>
          </table>
        )}
      </section>
      {open && (
        <div className="modal-bg" onClick={() => setOpen(null)}>
          <div className="modal wide" onClick={(e) => e.stopPropagation()}>
            <div className="modal-head"><b>{open.label} · {open.status} · {(open.progress * 100).toFixed(0)}%</b><button className="ghost sm" onClick={() => setOpen(null)}>✕</button></div>
            <div className="scroll">
              <table>
                <thead><tr><th>File</th><th>Duration</th><th>Positive samples</th><th>Labels</th><th>Max conf</th><th>Segments (s)</th><th></th></tr></thead>
                <tbody>{(open.results ?? []).map((r) => (
                  <tr key={r.file} className={r.detections ? 'alarm' : ''}>
                    <td title={r.file}>{r.name}{r.error && <div className="err">{r.error}</div>}</td>
                    <td>{r.duration != null ? fmtDur(r.duration) : ''}</td>
                    <td>{r.detections}/{r.sampled ?? ''}</td>
                    <td>{r.labels?.map((l) => <span key={l} className={'tag ' + l}>{l}</span>)}</td>
                    <td>{r.max_confidence ? (r.max_confidence * 100).toFixed(0) + '%' : ''}</td>
                    <td className="small">{r.segments.map((s, i) => <span key={i} className="seg">{s.start}–{s.end} {s.labels.join('+')} {(s.max_confidence * 100).toFixed(0)}%</span>)}</td>
                    <td>{r.annotated && <a href={r.annotated} target="_blank" rel="noreferrer">▶ annotated</a>}</td>
                  </tr>
                ))}</tbody>
              </table>
              {open.status === 'running' && open.file_index < open.files && <div className="muted pad">Processing {open.current_file} … {(open.file_progress * 100).toFixed(0)}%</div>}
            </div>
          </div>
        </div>
      )}
      {browse && <FileBrowser mode="any" initial={path} onClose={() => setBrowse(false)} onPick={(p) => { setPath(p); setBrowse(false) }} />}
    </>
  )
}
