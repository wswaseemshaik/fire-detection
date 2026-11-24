import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, type Stats } from '../api'
import SourceCard from '../components/SourceCard'
import { useLive } from '../ws'

export default function Live() {
  const { sources } = useLive()
  const [stats, setStats] = useState<Stats | null>(null)
  const [cols, setCols] = useState(Number(localStorage.getItem('cols') || 0))
  const [quick, setQuick] = useState('')
  const [err, setErr] = useState('')
  useEffect(() => {
    const load = () => api.stats().then(setStats).catch(() => {})
    load(); const t = setInterval(load, 5000); return () => clearInterval(t)
  }, [])
  const add = async (e: React.FormEvent) => {
    e.preventDefault(); setErr('')
    try { await api.addSource({ source: quick.trim() }); setQuick('') } catch (e: any) { setErr(e.message) }
  }
  const max = Math.max(1, ...(stats?.hourly ?? [0]))
  return (
    <>
      <header className="page-head">
        <h1>Live</h1>
        <form className="row gap grow" onSubmit={add}>
          <input className="grow" value={quick} onChange={(e) => setQuick(e.target.value)} placeholder="Quick add: rtsp://… | rtmp://… | https://…m3u8 | /path/video.mp4 | 0 (webcam)" />
          <button disabled={!quick.trim()}>Add</button>
        </form>
        <select value={cols} onChange={(e) => { setCols(Number(e.target.value)); localStorage.setItem('cols', e.target.value) }}>
          <option value={0}>Auto grid</option><option value={1}>1 column</option><option value={2}>2 columns</option><option value={3}>3 columns</option><option value={4}>4 columns</option>
        </select>
      </header>
      {err && <div className="err">{err}</div>}
      {stats && (
        <div className="tiles">
          <div className="tile"><small>Sources</small><b>{stats.sources_connected}/{stats.sources}</b><span>connected</span></div>
          <div className={'tile' + (stats.sources_alarm ? ' bad' : '')}><small>In alarm</small><b>{stats.sources_alarm}</b><span>right now</span></div>
          <div className="tile"><small>Alerts</small><b>{stats.last_hour}</b><span>last hour · {stats.last_24h} today</span></div>
          <div className="tile"><small>Inference</small><b>{stats.inference_ms} ms</b><span>per frame</span></div>
          <div className="tile chart"><small>Alerts, last 24 h</small>
            <div className="bars">{stats.hourly.map((v, i) => <i key={i} style={{ height: `${(v / max) * 100}%` }} title={`${v} alerts, ${23 - i}h ago`} />)}</div>
          </div>
        </div>
      )}
      {sources.length === 0 ? (
        <div className="empty">No sources yet. Add a stream above, or go to <Link to="/sources">Sources</Link> to browse files, folders, upload videos or pick a webcam.</div>
      ) : (
        <div className="grid" style={cols ? { gridTemplateColumns: `repeat(${cols}, 1fr)` } : undefined}>
          {sources.map((s) => <SourceCard key={s.id} s={s} />)}
        </div>
      )}
    </>
  )
}
