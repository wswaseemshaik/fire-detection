#!/usr/bin/env node
/**
 * One-command launcher.
 *   npm run dev    -> backend (FastAPI) + frontend (Vite, hot reload)
 *   npm run build  -> build the frontend into frontend/dist
 *   npm start      -> build (if needed) + backend serving the built UI on one port
 * Python dependencies are installed automatically into .venv on first run if needed.
 */
import { spawn, spawnSync } from 'node:child_process'
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const FRONTEND = path.join(ROOT, 'frontend')
const VENV = path.join(ROOT, '.venv')
const args = new Set(process.argv.slice(2))
const BUILD_ONLY = args.has('--build')
const PROD = args.has('--prod')
const BACKEND_PORT = process.env.FIRE_PORT || process.env.PORT || '8000'
const FRONTEND_PORT = process.env.FRONTEND_PORT || '5173'
const isWin = process.platform === 'win32'

const major = Number(process.versions.node.split('.')[0])
if (major < 18) {
  console.error(`\n  Node ${process.versions.node} is too old; this app needs Node 18+.\n  If you use nvm: nvm install 22 && nvm use 22\n`)
  process.exit(1)
}

const log = (tag, msg) => console.log(`\x1b[36m[${tag}]\x1b[0m ${msg}`)
const run = (cmd, cmdArgs, opts = {}) => {
  const r = spawnSync(cmd, cmdArgs, { stdio: 'inherit', shell: isWin, ...opts })
  if (r.status !== 0) { console.error(`\n  ${cmd} ${cmdArgs.join(' ')} failed`); process.exit(r.status ?? 1) }
}
const can = (cmd, cmdArgs) => spawnSync(cmd, cmdArgs, { stdio: 'ignore', shell: isWin }).status === 0

// ---- .env -------------------------------------------------------------------
if (existsSync(path.join(ROOT, '.env'))) {
  for (const line of readFileSync(path.join(ROOT, '.env'), 'utf8').split('\n')) {
    const m = line.match(/^\s*([A-Z0-9_]+)\s*=\s*(.*)\s*$/)
    if (m && !(m[1] in process.env)) process.env[m[1]] = m[2].replace(/^["']|["']$/g, '')
  }
}

// ---- python -------------------------------------------------------------------
const REQUIRED = ['ultralytics', 'fastapi', 'uvicorn', 'cv2', 'multipart']
const hasDeps = (py) => can(py, ['-c', `import ${REQUIRED.join(',')}`])
const venvPy = path.join(VENV, isWin ? 'Scripts/python.exe' : 'bin/python')

function findPython() {
  if (process.env.FIRE_PYTHON) return process.env.FIRE_PYTHON
  if (existsSync(venvPy) && hasDeps(venvPy)) return venvPy
  for (const py of ['python3', 'python']) {
    if (can(py, ['--version']) && hasDeps(py)) { log('python', `using system ${py} (deps already installed)`); return py }
  }
  const base = ['python3', 'python'].find((p) => can(p, ['--version']))
  if (!base) { console.error('\n  Python 3.10+ is required but was not found on PATH.\n'); process.exit(1) }
  if (!existsSync(venvPy)) { log('python', 'creating .venv'); run(base, ['-m', 'venv', VENV]) }
  log('python', 'installing requirements into .venv (first run only, this can take a few minutes)')
  run(venvPy, ['-m', 'pip', 'install', '--upgrade', 'pip'])
  run(venvPy, ['-m', 'pip', 'install', '-r', path.join(ROOT, 'requirements.txt')])
  return venvPy
}

if (!can('ffmpeg', ['-version'])) {
  console.warn('\x1b[33m[warn]\x1b[0m ffmpeg not found on PATH: RTSP/RTMP input and H.264 recordings need it (apt install ffmpeg / brew install ffmpeg)')
}
const python = findPython()
mkdirSync(path.join(ROOT, 'data'), { recursive: true })

// ---- frontend deps ------------------------------------------------------------------
if (!existsSync(path.join(FRONTEND, 'node_modules'))) {
  log('frontend', 'installing npm dependencies')
  run('npm', ['install', '--no-audit', '--no-fund'], { cwd: FRONTEND })
}

if (BUILD_ONLY || PROD) {
  if (BUILD_ONLY || !existsSync(path.join(FRONTEND, 'dist', 'index.html'))) {
    log('frontend', 'building')
    run(process.execPath, [path.join(FRONTEND, 'node_modules', 'typescript', 'bin', 'tsc'), '--noEmit'], { cwd: FRONTEND })
    run(process.execPath, [path.join(FRONTEND, 'node_modules', 'vite', 'bin', 'vite.js'), 'build'], { cwd: FRONTEND })
  }
  if (BUILD_ONLY) process.exit(0)
}

// ---- processes --------------------------------------------------------------------
const procs = []
const start = (tag, cmd, cmdArgs, opts = {}) => {
  const p = spawn(cmd, cmdArgs, { stdio: 'inherit', shell: isWin, ...opts })
  p.on('exit', (code) => { if (!shuttingDown) { console.error(`\n  ${tag} exited (${code}); stopping.`); shutdown(code ?? 1) } })
  procs.push(p)
  return p
}
let shuttingDown = false
const shutdown = (code = 0) => {
  if (shuttingDown) return
  shuttingDown = true
  for (const p of procs) try { p.kill('SIGTERM') } catch {}
  setTimeout(() => process.exit(code), 500)
}
process.on('SIGINT', () => shutdown(0))
process.on('SIGTERM', () => shutdown(0))

const env = { ...process.env, FIRE_PORT: BACKEND_PORT, BACKEND_PORT, FRONTEND_PORT, PYTHONUNBUFFERED: '1' }
start('backend', python, ['-m', 'uvicorn', 'app.main:app', '--host', process.env.FIRE_HOST || '0.0.0.0', '--port', BACKEND_PORT, '--log-level', 'info'], { cwd: ROOT, env })

if (PROD) {
  log('ready', `http://localhost:${BACKEND_PORT}`)
} else {
  const vite = path.join(FRONTEND, 'node_modules', 'vite', 'bin', 'vite.js')
  start('frontend', process.execPath, [vite, '--clearScreen', 'false'], { cwd: FRONTEND, env })
  setTimeout(() => log('ready', `open http://localhost:${FRONTEND_PORT}  (API on :${BACKEND_PORT})`), 2500)
}
