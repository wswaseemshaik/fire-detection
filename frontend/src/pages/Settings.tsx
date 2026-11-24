import { useEffect, useState } from 'react'
import { api } from '../api'

type S = Record<string, any>
const Field = ({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) => (
  <label className="field"><span>{label}{hint && <small className="muted">{hint}</small>}</span>{children}</label>
)

export default function Settings() {
  const [s, setS] = useState<S | null>(null)
  const [orig, setOrig] = useState<S | null>(null)
  const [channels, setChannels] = useState<Record<string, boolean>>({})
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)
  const [testing, setTesting] = useState('')
  const [health, setHealth] = useState<any>(null)
  useEffect(() => { api.settings().then((r) => { setS(r.settings); setOrig(r.settings); setChannels(r.channels) }); api.health().then(setHealth) }, [])
  if (!s) return <div className="empty">Loading…</div>
  const set = (k: string, v: any) => setS({ ...s, [k]: v })
  const dirty = JSON.stringify(s) !== JSON.stringify(orig)
  const save = async () => {
    setMsg(null)
    try {
      const r = await api.saveSettings(s)
      setS(r.settings); setOrig(r.settings); setChannels(r.channels)
      setMsg({ ok: true, text: `Saved ${Object.keys(r.changed).length} change(s), applied live.` })
    } catch (e: any) { setMsg({ ok: false, text: e.message }) }
  }
  const test = async (ch: string) => {
    setTesting(ch); setMsg(null)
    try { if (dirty) await save(); const r = await api.testNotification(ch); setMsg({ ok: r.ok, text: `${ch}: ${r.message}` }) } catch (e: any) { setMsg({ ok: false, text: e.message }) } finally { setTesting('') }
  }
  const num = (k: string, step = 1, min = 0, max?: number) => <input type="number" step={step} min={min} max={max} value={s[k]} onChange={(e) => set(k, Number(e.target.value))} />
  const txt = (k: string, ph = '', type = 'text') => <input type={type} value={s[k] ?? ''} placeholder={ph} onChange={(e) => set(k, e.target.value)} />
  const chk = (k: string) => <input type="checkbox" checked={!!s[k]} onChange={(e) => set(k, e.target.checked)} />

  return (
    <>
      <header className="page-head"><h1>Settings</h1><span className="grow" />
        {msg && <span className={msg.ok ? 'ok' : 'err'}>{msg.text}</span>}
        <button className="ghost" disabled={!dirty} onClick={() => setS(orig)}>Reset</button>
        <button disabled={!dirty} onClick={save}>Save & apply</button>
      </header>
      <div className="settings">
        <section className="card">
          <h2>Detection</h2>
          <Field label="Confidence threshold" hint="lower = more sensitive, more false alarms">{num('conf', 0.05, 0.05, 0.95)}</Field>
          <Field label="NMS IoU">{num('iou', 0.05, 0.1, 0.95)}</Field>
          <Field label="Inference size (px)" hint="320 is ~3× faster than 640">{num('imgsz', 32, 160, 1280)}</Field>
          <Field label="Run detector every N frames" hint="raise on slow CPUs; boxes are reused in between">{num('detect_every', 1, 1, 30)}</Field>
          <Field label="Detect fire">{chk('detect_fire')}</Field>
          <Field label="Detect smoke">{chk('detect_smoke')}</Field>
          <div className="muted small pad">Model: {health?.weights?.split('/').pop()} on {health?.device} · {health?.inference_ms} ms/frame. Change weights/device via FIRE_WEIGHTS / FIRE_DEVICE in <code>.env</code> and restart.</div>
        </section>
        <section className="card">
          <h2>Alerting</h2>
          <Field label="Consecutive positive frames" hint="before an alert fires (filters flicker)">{num('min_consecutive', 1, 1, 100)}</Field>
          <Field label="Cooldown between alerts (s)" hint="per source">{num('alert_cooldown', 5, 0)}</Field>
          <Field label="Save alert clips">{chk('save_clips')}</Field>
          <Field label="Clip pre-roll (s)">{num('clip_pre_seconds', 1, 0, 60)}</Field>
          <Field label="Clip post-roll (s)">{num('clip_post_seconds', 1, 1, 300)}</Field>
        </section>
        <section className="card">
          <h2>Streams</h2>
          <Field label="Loop local files">{chk('loop_files')}</Field>
          <Field label="Reconnect delay (s)">{num('reconnect_delay', 1, 1)}</Field>
          <Field label="Preview JPEG quality">{num('jpeg_quality', 5, 30, 100)}</Field>
          <Field label="Preview max width (px)" hint="0 = original">{num('stream_max_width', 160, 0)}</Field>
        </section>
        <section className="card">
          <h2>Webhook <span className={'dot ' + (channels.webhook ? 'on' : '')} /></h2>
          <Field label="URL" hint="POST JSON on every alert">{txt('webhook_url', 'https://example.com/hook')}</Field>
          <button className="ghost sm" disabled={!s.webhook_url || !!testing} onClick={() => test('webhook')}>{testing === 'webhook' ? 'Sending…' : 'Send test'}</button>
        </section>
        <section className="card">
          <h2>Telegram <span className={'dot ' + (channels.telegram ? 'on' : '')} /></h2>
          <Field label="Bot token" hint="from @BotFather">{txt('telegram_bot_token', '123456:ABC…', 'password')}</Field>
          <Field label="Chat ID" hint="user, group or channel id">{txt('telegram_chat_id', '-1001234567890')}</Field>
          <button className="ghost sm" disabled={!s.telegram_bot_token || !s.telegram_chat_id || !!testing} onClick={() => test('telegram')}>{testing === 'telegram' ? 'Sending…' : 'Send test'}</button>
        </section>
        <section className="card">
          <h2>Email <span className={'dot ' + (channels.email ? 'on' : '')} /></h2>
          <Field label="SMTP host">{txt('smtp_host', 'smtp.gmail.com')}</Field>
          <Field label="SMTP port" hint="587 STARTTLS · 465 SSL">{num('smtp_port', 1, 1, 65535)}</Field>
          <Field label="Use TLS">{chk('smtp_tls')}</Field>
          <Field label="Username">{txt('smtp_user')}</Field>
          <Field label="Password">{txt('smtp_password', '', 'password')}</Field>
          <Field label="From">{txt('smtp_from', 'alerts@example.com')}</Field>
          <Field label="To" hint="comma-separated">{txt('smtp_to', 'you@example.com')}</Field>
          <button className="ghost sm" disabled={!s.smtp_host || !s.smtp_to || !!testing} onClick={() => test('email')}>{testing === 'email' ? 'Sending…' : 'Send test'}</button>
        </section>
      </div>
    </>
  )
}
