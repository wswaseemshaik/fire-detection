import { useCallback, useEffect, useState } from 'react'
import { NavLink, Route, Routes } from 'react-router-dom'
import type { Event } from './api'
import { LiveContext, useLiveFeed } from './ws'
import Live from './pages/Live'
import Sources from './pages/Sources'
import Alerts from './pages/Alerts'
import Batch from './pages/Batch'
import Recordings from './pages/Recordings'
import Settings from './pages/Settings'

function beep() {
  try {
    const c = new AudioContext()
    const o = c.createOscillator(), g = c.createGain()
    o.type = 'square'; o.frequency.value = 880; g.gain.value = 0.08
    o.connect(g); g.connect(c.destination); o.start(); o.stop(c.currentTime + 0.35)
  } catch {}
}

export default function App() {
  const [muted, setMuted] = useState(localStorage.getItem('muted') === '1')
  const [toast, setToast] = useState<Event | null>(null)
  const onAlert = useCallback((e: Event) => {
    setToast(e)
    if (localStorage.getItem('muted') !== '1') beep()
  }, [])
  const live = useLiveFeed(onAlert)
  useEffect(() => { if (toast) { const t = setTimeout(() => setToast(null), 6000); return () => clearTimeout(t) } }, [toast])
  const alarms = live.sources.filter((s) => s.alarm).length

  return (
    <LiveContext.Provider value={live}>
      <div className="shell">
        <nav className="side">
          <div className="brand">🔥 <span>Fire</span>Watch</div>
          <NavLink to="/" end>Live {alarms > 0 && <b className="pill bad">{alarms}</b>}</NavLink>
          <NavLink to="/sources">Sources <span className="pill">{live.sources.length}</span></NavLink>
          <NavLink to="/alerts">Alerts</NavLink>
          <NavLink to="/batch">Batch{live.jobs.length > 0 && <b className="pill">{live.jobs.length}</b>}</NavLink>
          <NavLink to="/recordings">Recordings</NavLink>
          <NavLink to="/settings">Settings</NavLink>
          <div className="side-foot">
            <span className={'dot ' + (live.connected ? 'on' : 'off')} /> {live.connected ? 'connected' : 'reconnecting…'}
            <button className="ghost sm" onClick={() => { const m = !muted; setMuted(m); localStorage.setItem('muted', m ? '1' : '0') }} title="Alert sound">{muted ? '🔕' : '🔔'}</button>
          </div>
        </nav>
        <main>
          <Routes>
            <Route path="/" element={<Live />} />
            <Route path="/sources" element={<Sources />} />
            <Route path="/alerts" element={<Alerts />} />
            <Route path="/batch" element={<Batch />} />
            <Route path="/recordings" element={<Recordings />} />
            <Route path="/settings" element={<Settings />} />
          </Routes>
        </main>
        {toast && (
          <div className="toast" onClick={() => setToast(null)}>
            <img src={toast.snapshot} alt="" />
            <div><b>🚨 {toast.labels.join(' + ').toUpperCase()}</b><br />{toast.source_name} · {(toast.max_confidence * 100).toFixed(0)}%</div>
          </div>
        )}
      </div>
    </LiveContext.Provider>
  )
}
