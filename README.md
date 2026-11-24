# FireWatch: real-time fire & smoke detection

Detects **fire and smoke in real time** on RTSP / RTMP / HTTP(S) streams, webcams, local video files
and whole folders, with a web dashboard where everything is configured. One command to run.

```bash
git clone https://github.com/waseem-collab/fire-detection.git
cd fire-detection
npm run dev
```

Open **http://localhost:5173**. That's it. Python dependencies are installed into `.venv` automatically on
first run if they're missing. Requirements: Node 18+, Python 3.10+, ffmpeg on PATH (`apt install ffmpeg`).

## What's in the app

| Page | What you can do |
|---|---|
| **Live** | Grid of every source with annotated live video, red alarm highlighting, per-source pause / record / reconnect / remove, quick-add bar, stats tiles (connected, in alarm, alerts per hour, inference time) and a 24 h alert chart. |
| **Sources** | Add a **stream URL** (rtsp / rtmp / http / hls / mjpeg), a **video file** (browse the server's disk or type a path), a whole **folder** (every video becomes a source), **upload** a video from your computer, or a **webcam** index. Rename, pause/resume, toggle recording, restart, remove, grouped by folder. Sources persist across restarts. |
| **Alerts** | Every alert with snapshot, labels, confidence, source and time. Filter by source, label and time range. Click to see the saved **clip** (pre-roll + post-roll video) and the box coordinates. Delete one or clear all. |
| **Batch** | Scan a folder (or one file) as fast as possible, not in real time. Get a per-file report: which files contain fire/smoke, at which time segments, with what confidence. Optional annotated output video. Download CSV. |
| **Recordings** | Continuous recordings, alert clips and batch outputs. Play in the browser (H.264), download, delete. |
| **Settings** | Everything applies live, no restart: confidence / IoU / inference size / frame skipping / fire-only or smoke-only, alert debounce and cooldown, clip pre/post-roll, file looping, preview quality. Notifications: **webhook**, **Telegram** (with photo), **email** (SMTP, with attachment), each with a *Send test* button. |

Alerts also arrive live in the UI with a toast and sound.

## Running it other ways

```bash
npm run dev          # backend + frontend with hot reload (http://localhost:5173)
npm start            # production: builds the UI once and serves it from the backend (http://localhost:8000)
npm run build        # just build the frontend into frontend/dist
npm test             # python tests

python run.py        # backend only (serves frontend/dist if built)
python cli.py rtsp://user:pass@cam/stream --show           # headless CLI with a preview window
python cli.py video.mp4 --no-loop --output annotated.mp4   # write an annotated video
```

Docker (UI is built in the image):

```bash
cp .env.example .env
docker compose up --build        # http://localhost:8000
```

Mount folders with videos into the container (see `docker-compose.yml`) to browse them from the UI.

## Configuration

Everything in the Settings page is stored in `data/settings.json`. Defaults, ports, and the two things that
need a restart (model weights and device) live in `.env` (copy `.env.example`):

| Variable | Default | |
|---|---|---|
| `FIRE_WEIGHTS` | `models/fire-smoke-yolov8n.pt` | any Ultralytics YOLO weights with `fire` / `smoke` classes |
| `FIRE_DEVICE` | `cpu` | `cuda`, `cuda:0`, `mps` |
| `FIRE_PORT` / `FRONTEND_PORT` | `8000` / `5173` | |
| `FIRE_SOURCES` | | comma-separated sources to add on startup |
| `FIRE_DATA_DIR` | `data` | snapshots, clips, recordings, uploads, settings |

## API

The UI is a thin client over a REST + WebSocket API, so you can drive it from anything.

| Method | Path | |
|---|---|---|
| GET | `/api/health`, `/api/stats` | model / device / inference time; alert counts, 24 h histogram |
| GET / POST | `/api/sources` | list; add `{"source","name","record","paused"}` |
| POST | `/api/sources/folder` | `{"path","record","recursive"}` adds every video in a folder |
| PATCH / DELETE | `/api/sources/{id}` | `{"name","record","paused","restart"}` |
| GET | `/api/sources/{id}/stream` | annotated MJPEG |
| GET | `/api/sources/{id}/snapshot.jpg` | latest annotated frame |
| GET | `/api/events?limit&source_id&label&since` | alerts, newest first |
| DELETE | `/api/events/{id}`, `/api/events` | delete one / all |
| GET | `/api/files/browse?path=` | server-side file browser |
| POST | `/api/files/upload` | multipart upload |
| GET / DELETE | `/api/media`, `/api/media/{kind}/{name}` | recordings, clips, batch outputs |
| GET / POST | `/api/jobs` | list; create `{"path","recursive","sample_fps","annotate"}` |
| GET | `/api/jobs/{id}`, `/api/jobs/{id}/report.csv` | full results / CSV |
| POST / DELETE | `/api/jobs/{id}/cancel`, `/api/jobs/{id}` | |
| GET / PUT | `/api/settings` | read / update (applied live) |
| POST | `/api/settings/test-notification` | `{"channel": "webhook" \| "telegram" \| "email"}` |
| WS | `/ws/events` | `{"type":"alert"}` on alerts, `{"type":"status"}` every second with all sources |

Alert payload (also POSTed to the webhook):

```json
{"id": "...", "timestamp": "2026-11-24T10:32:21+00:00", "source_id": "e45e70ba", "source_name": "RTSP cam",
 "labels": ["fire"], "max_confidence": 0.84,
 "detections": [{"label": "fire", "confidence": 0.84, "x1": 176, "y1": 106, "x2": 381, "y2": 490}],
 "snapshot": "/snapshots/20261124T103221_e45e70ba_fire.jpg",
 "clip": "/media/clips/20261124T103221_e45e70ba_alert.mp4"}
```

## How it works

```
frontend/            React + Vite dashboard
scripts/dev.mjs      one-command launcher (python env, deps, backend + frontend)
app/main.py          FastAPI: REST, MJPEG, WebSocket, serves the built UI
app/pipeline.py      one worker thread per source: read → detect → annotate → alert → clip/record → publish
app/stream.py        OpenCV/FFmpeg capture with reconnect, latest-frame buffer, file pacing/looping
app/detector.py      YOLO wrapper (one shared model, thread-safe)
app/events.py        alert debounce + cooldown, event log, fan-out
app/notify.py        webhook / Telegram / email
app/jobs.py          batch folder scanning + reports
app/video_writer.py  H.264 MP4 writer via ffmpeg pipe (browser-playable)
app/files.py         file browser, uploads, media listing
app/config.py        settings: env defaults + live overrides in data/settings.json
cli.py               headless runner
```

- The model is YOLOv8n fine-tuned on fire + smoke (about 45 ms/frame on a laptop CPU, 20 ms on GPU).
  Several sources share one model; three 640 px streams run at roughly 10 fps each on CPU. Raise
  *Run detector every N frames* or lower *Inference size* in Settings for more cameras, or use a GPU.
- Live streams always process the newest frame, so latency doesn't build up when inference is slower
  than the camera. Local files are paced at their native frame rate so they behave like a camera.
- RTSP is forced to TCP with a 5 s socket timeout; dead streams reconnect automatically.
- Fire/smoke models can false-positive on sunsets, headlights, steam and fog. Tune confidence and the
  consecutive-frames setting per site, or fine-tune the model on your own footage.
- Model weights are AGPL-3.0 (Ultralytics); check licensing before commercial use.
