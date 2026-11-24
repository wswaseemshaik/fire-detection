export type Detection = { label: string; confidence: number; x1: number; y1: number; x2: number; y2: number }
export type Source = {
  id: string; name: string; source: string; group: string; live: boolean; connected: boolean; ended: boolean
  error: string; resolution: string | null; source_fps: number; fps: number; frames: number
  progress: number | null; paused: boolean; recording: boolean; alarm: boolean; alerts: number
  detections: Detection[]; uptime: number
}
export type Event = {
  id: string; timestamp: string; source_id: string; source_name: string; labels: string[]
  max_confidence: number; detections: Detection[]; snapshot: string; clip: string | null
}
export type Job = {
  id: string; label: string; status: string; files: number; file_index: number; current_file: string
  file_progress: number; progress: number; created: number; started: number | null; finished: number | null
  error: string; sample_fps: number; annotate: boolean; positives: number; results?: JobResult[]
}
export type JobResult = {
  file: string; name: string; duration?: number; frames?: number; sampled?: number; detections: number
  labels?: string[]; max_confidence?: number; error?: string; annotated?: string | null
  segments: { start: number; end: number; labels: string[]; max_confidence: number }[]
}
export type Media = { name: string; kind: string; size: number; mtime: number; url: string; source_id: string | null }
export type Browse = {
  path: string; parent: string | null; dirs: { name: string; path: string }[]
  files: { name: string; path: string; size: number; mtime: number }[]; video_count: number
  shortcuts: { name: string; path: string }[]
}
export type Stats = {
  total: number; last_hour: number; last_24h: number; hourly: number[]; by_source: Record<string, number>
  by_label: Record<string, number>; sources: number; sources_connected: number; sources_alarm: number
  inference_ms: number; jobs_running: number
}

async function req<T>(url: string, init?: RequestInit): Promise<T> {
  const r = await fetch(url, { headers: { 'Content-Type': 'application/json' }, ...init })
  if (!r.ok) {
    let msg = r.statusText
    try { const j = await r.json(); msg = j.detail || JSON.stringify(j) } catch {}
    throw new Error(msg)
  }
  return r.status === 204 ? (undefined as T) : r.json()
}
const json = (body: unknown) => JSON.stringify(body)

export const api = {
  health: () => req<any>('/api/health'),
  stats: () => req<Stats>('/api/stats'),
  sources: () => req<Source[]>('/api/sources'),
  addSource: (b: { source: string; name?: string; record?: boolean; paused?: boolean }) => req<Source>('/api/sources', { method: 'POST', body: json(b) }),
  addFolder: (b: { path: string; record?: boolean; recursive?: boolean }) => req<{ added: Source[] }>('/api/sources/folder', { method: 'POST', body: json(b) }),
  patchSource: (id: string, b: { name?: string; record?: boolean; paused?: boolean; restart?: boolean }) => req<Source>(`/api/sources/${id}`, { method: 'PATCH', body: json(b) }),
  removeSource: (id: string) => req<unknown>(`/api/sources/${id}`, { method: 'DELETE' }),
  events: (q: { limit?: number; source_id?: string; label?: string; since?: string } = {}) => {
    const p = new URLSearchParams(Object.entries(q).filter(([, v]) => v !== undefined && v !== '').map(([k, v]) => [k, String(v)]))
    return req<Event[]>(`/api/events?${p}`)
  },
  deleteEvent: (id: string) => req<unknown>(`/api/events/${id}`, { method: 'DELETE' }),
  clearEvents: () => req<{ deleted: number }>('/api/events', { method: 'DELETE' }),
  browse: (path = '') => req<Browse>(`/api/files/browse?path=${encodeURIComponent(path)}`),
  upload: async (file: File, onProgress?: (p: number) => void) =>
    new Promise<{ path: string; name: string }>((resolve, reject) => {
      const xhr = new XMLHttpRequest()
      const fd = new FormData()
      fd.append('file', file)
      xhr.upload.onprogress = (e) => e.lengthComputable && onProgress?.(e.loaded / e.total)
      xhr.onload = () => (xhr.status < 300 ? resolve(JSON.parse(xhr.responseText)) : reject(new Error(xhr.responseText)))
      xhr.onerror = () => reject(new Error('upload failed'))
      xhr.open('POST', '/api/files/upload')
      xhr.send(fd)
    }),
  media: () => req<Media[]>('/api/media'),
  deleteMedia: (kind: string, name: string) => req<unknown>(`/api/media/${kind}/${encodeURIComponent(name)}`, { method: 'DELETE' }),
  jobs: () => req<Job[]>('/api/jobs'),
  job: (id: string) => req<Job>(`/api/jobs/${id}`),
  createJob: (b: { path?: string; files?: string[]; recursive?: boolean; sample_fps?: number; annotate?: boolean }) => req<Job>('/api/jobs', { method: 'POST', body: json(b) }),
  cancelJob: (id: string) => req<Job>(`/api/jobs/${id}/cancel`, { method: 'POST' }),
  deleteJob: (id: string) => req<unknown>(`/api/jobs/${id}`, { method: 'DELETE' }),
  settings: () => req<{ settings: Record<string, any>; channels: Record<string, boolean> }>('/api/settings'),
  saveSettings: (b: Record<string, any>) => req<{ changed: Record<string, any>; settings: Record<string, any>; channels: Record<string, boolean> }>('/api/settings', { method: 'PUT', body: json(b) }),
  testNotification: (channel: string) => req<{ ok: boolean; message: string }>('/api/settings/test-notification', { method: 'POST', body: json({ channel }) }),
}

export const fmtBytes = (n: number) => (n > 1e9 ? (n / 1e9).toFixed(2) + ' GB' : n > 1e6 ? (n / 1e6).toFixed(1) + ' MB' : (n / 1e3).toFixed(0) + ' KB')
export const fmtTime = (iso: string | number) => new Date(typeof iso === 'number' ? iso * 1000 : iso).toLocaleString()
export const fmtDur = (s: number) => (s >= 3600 ? `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m` : s >= 60 ? `${Math.floor(s / 60)}m ${Math.floor(s % 60)}s` : `${Math.floor(s)}s`)
