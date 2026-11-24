import { useRef, useState } from 'react'
import { api, fmtDur } from '../api'
import FileBrowser from '../components/FileBrowser'
import { useLive } from '../ws'

type Kind = 'stream' | 'file' | 'folder' | 'upload' | 'webcam'
const KINDS: { k: Kind; label: string; hint: string }[] = [
  { k: 'stream', label: 'Stream URL', hint: 'rtsp://user:pass@host:554/stream · rtmp://host/live/key · https://host/live.m3u8 · http://host/mjpeg' },
  { k: 'file', label: 'Video file', hint: 'A video file on this machine (browse or type a path). Loops by default; change in Settings.' },
  { k: 'folder', label: 'Folder', hint: 'Adds every video in the folder as its own source. For a one-off scan of a folder use Batch instead.' },
  { k: 'upload', label: 'Upload', hint: 'Upload a video from your computer; it is stored under data/uploads and added as a source.' },
  { k: 'webcam', label: 'Webcam', hint: 'Device index on the server machine (0 is usually the built-in camera).' },
]

export default function Sources() {
  const { sources } = useLive()
  const [kind, setKind] = useState<Kind>('stream')
  const [value, setValue] = useState('')
  const [name, setName] = useState('')
  const [record, setRecord] = useState(false)
  const [recursive, setRecursive] = useState(false)
  const [browse, setBrowse] = useState(false)
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)
  const [progress, setProgress] = useState<number | null>(null)
  const fileInput = useRef<HTMLInputElement>(null)
  const [editing, setEditing] = useState<string | null>(null)
  const [editName, setEditName] = useState('')

  const submit = async (e: React.FormEvent) => {
    e.preventDefault(); setBusy(true); setMsg(null)
    try {
      if (kind === 'folder') {
        const r = await api.addFolder({ path: value.trim(), record, recursive })
        setMsg({ ok: true, text: `Added ${r.added.length} video(s) from folder` })
      } else if (kind === 'upload') {
        const f = fileInput.current?.files?.[0]
        if (!f) throw new Error('choose a file first')
        const up = await api.upload(f, setProgress)
        setProgress(null)
        await api.addSource({ source: up.path, name: name || f.name, record })
        setMsg({ ok: true, text: `Uploaded and added ${f.name}` })
        if (fileInput.current) fileInput.current.value = ''
      } else {
        const s = await api.addSource({ source: value.trim(), name, record })
        setMsg({ ok: true, text: `Added ${s.name}` })
      }
      setValue(''); setName('')
    } catch (e: any) { setMsg({ ok: false, text: e.message }); setProgress(null) } finally { setBusy(false) }
  }
  const patch = (id: string, b: Parameters<typeof api.patchSource>[1]) => api.patchSource(id, b).catch((e) => alert(e.message))
  const groups = [...new Set(sources.map((s) => s.group).filter(Boolean))]

  return (
    <>
      <header className="page-head"><h1>Sources</h1></header>
      <section className="card">
        <div className="tabs">{KINDS.map((k) => <button key={k.k} className={kind === k.k ? 'active' : ''} type="button" onClick={() => { setKind(k.k); setValue(''); setMsg(null) }}>{k.label}</button>)}</div>
        <p className="muted">{KINDS.find((k) => k.k === kind)!.hint}</p>
        <form onSubmit={submit} className="col gap">
          <div className="row gap">
            {kind === 'upload' ? (
              <input type="file" accept="video/*,.mkv,.ts,.flv" ref={fileInput} className="grow" />
            ) : kind === 'webcam' ? (
              <input type="number" min={0} value={value} onChange={(e) => setValue(e.target.value)} placeholder="0" style={{ width: 120 }} required />
            ) : (
              <input className="grow" value={value} onChange={(e) => setValue(e.target.value)} required
                placeholder={kind === 'stream' ? 'rtsp://…' : kind === 'file' ? '/path/to/video.mp4' : '/path/to/folder'} />
            )}
            {(kind === 'file' || kind === 'folder') && <button type="button" className="ghost" onClick={() => setBrowse(true)}>Browse…</button>}
          </div>
          <div className="row gap wrap">
            {kind !== 'folder' && <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Display name (optional)" style={{ width: 260 }} />}
            <label className="chk"><input type="checkbox" checked={record} onChange={(e) => setRecord(e.target.checked)} /> record continuously</label>
            {kind === 'folder' && <label className="chk"><input type="checkbox" checked={recursive} onChange={(e) => setRecursive(e.target.checked)} /> include subfolders</label>}
            <span className="grow" />
            <button disabled={busy}>{busy ? (progress != null ? `Uploading ${(progress * 100).toFixed(0)}%` : 'Adding…') : kind === 'folder' ? 'Add all videos' : 'Add source'}</button>
          </div>
        </form>
        {msg && <div className={msg.ok ? 'ok' : 'err'}>{msg.text}</div>}
      </section>

      <section className="card">
        <div className="row gap"><h2>Active sources ({sources.length})</h2><span className="grow" />
          {sources.length > 0 && <button className="danger sm" onClick={async () => { if (confirm('Remove ALL sources?')) for (const s of sources) await api.removeSource(s.id) }}>Remove all</button>}
        </div>
        {sources.length === 0 && <div className="muted pad">Nothing yet.</div>}
        {[...groups, ''].map((g) => {
          const rows = sources.filter((s) => (s.group || '') === g)
          if (!rows.length) return null
          return (
            <div key={g || '_'}>
              {g && <div className="group">📁 {g} <button className="danger sm" onClick={async () => { if (confirm(`Remove all ${rows.length} sources in ${g}?`)) for (const s of rows) await api.removeSource(s.id) }}>remove group</button></div>}
              <table>
                <thead><tr><th></th><th>Name</th><th>Source</th><th>Status</th><th>FPS</th><th>Alerts</th><th>Uptime</th><th></th></tr></thead>
                <tbody>
                  {rows.map((s) => (
                    <tr key={s.id} className={s.alarm ? 'alarm' : ''}>
                      <td><img className="thumb" src={`/api/sources/${s.id}/snapshot.jpg?t=${Math.floor(Date.now() / 5000)}`} alt="" /></td>
                      <td>
                        {editing === s.id ? (
                          <form className="row gap" onSubmit={(e) => { e.preventDefault(); patch(s.id, { name: editName }); setEditing(null) }}>
                            <input value={editName} onChange={(e) => setEditName(e.target.value)} autoFocus /><button className="sm">Save</button>
                          </form>
                        ) : <span className="link" onClick={() => { setEditing(s.id); setEditName(s.name) }} title="Rename">{s.name}</span>}
                        {s.alarm && <span className="tag fire">ALARM</span>}
                      </td>
                      <td className="mono small" title={s.source}>{s.source.length > 48 ? s.source.slice(0, 48) + '…' : s.source}</td>
                      <td><span className={'dot ' + (s.paused ? '' : s.connected ? 'on' : s.ended ? '' : 'off')} /> {s.paused ? 'paused' : s.connected ? (s.live ? 'live' : `file ${((s.progress ?? 0) * 100).toFixed(0)}%`) : s.ended ? 'ended' : s.error || 'connecting'}{s.resolution ? ` · ${s.resolution}` : ''}{s.recording ? ' · REC' : ''}</td>
                      <td>{s.fps}</td><td>{s.alerts}</td><td>{fmtDur(s.uptime)}</td>
                      <td className="row gap">
                        <button className="ghost sm" onClick={() => patch(s.id, { paused: !s.paused })}>{s.paused ? '▶ Resume' : '⏸ Pause'}</button>
                        <button className={'ghost sm' + (s.recording ? ' active' : '')} onClick={() => patch(s.id, { record: !s.recording })}>⏺ {s.recording ? 'Stop' : 'Record'}</button>
                        <button className="ghost sm" onClick={() => patch(s.id, { restart: true })}>↻</button>
                        <button className="danger sm" onClick={() => confirm(`Remove ${s.name}?`) && api.removeSource(s.id)}>✕</button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )
        })}
      </section>
      {browse && <FileBrowser mode={kind === 'folder' ? 'folder' : 'file'} initial={value} onClose={() => setBrowse(false)} onPick={(p) => { setValue(p); setBrowse(false) }} />}
    </>
  )
}
