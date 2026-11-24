import { useEffect, useState } from 'react'
import { api, fmtBytes, type Browse } from '../api'

type Props = { mode: 'file' | 'folder' | 'any'; onPick: (path: string, isDir: boolean) => void; onClose: () => void; initial?: string }

export default function FileBrowser({ mode, onPick, onClose, initial }: Props) {
  const [data, setData] = useState<Browse | null>(null)
  const [err, setErr] = useState('')
  const [manual, setManual] = useState(initial || '')
  const go = (p: string) => api.browse(p).then((d) => { setData(d); setManual(d.path); setErr('') }).catch((e) => setErr(e.message))
  useEffect(() => { go(initial || '') }, [])
  const canPickDir = mode !== 'file'
  return (
    <div className="modal-bg" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <b>{mode === 'folder' ? 'Choose a folder' : mode === 'file' ? 'Choose a video' : 'Choose a video or folder'}</b>
          <button className="ghost sm" onClick={onClose}>✕</button>
        </div>
        <div className="row gap">
          {data?.shortcuts.map((s) => <button key={s.path} className="ghost sm" onClick={() => go(s.path)}>{s.name}</button>)}
          <button className="ghost sm" disabled={!data?.parent} onClick={() => data?.parent && go(data.parent)}>↑ Up</button>
          <form className="grow row" onSubmit={(e) => { e.preventDefault(); go(manual) }}>
            <input className="grow" value={manual} onChange={(e) => setManual(e.target.value)} placeholder="/path/to/folder" />
            <button className="ghost sm">Go</button>
          </form>
        </div>
        {err && <div className="err">{err}</div>}
        <div className="browser">
          {data?.dirs.map((d) => (
            <div key={d.path} className="brow" onDoubleClick={() => go(d.path)}>
              <span className="grow" onClick={() => go(d.path)}>📁 {d.name}</span>
              {canPickDir && <button className="sm" onClick={() => onPick(d.path, true)}>Select folder</button>}
            </div>
          ))}
          {data?.files.map((f) => (
            <div key={f.path} className="brow">
              <span className="grow">🎞 {f.name} <small className="muted">{fmtBytes(f.size)}</small></span>
              {mode !== 'folder' && <button className="sm" onClick={() => onPick(f.path, false)}>Select</button>}
            </div>
          ))}
          {data && !data.dirs.length && !data.files.length && <div className="muted pad">Empty folder</div>}
        </div>
        <div className="modal-foot">
          <span className="muted">{data?.video_count ?? 0} video file(s) here</span>
          {canPickDir && data && <button onClick={() => onPick(data.path, true)}>Use this folder</button>}
        </div>
      </div>
    </div>
  )
}
