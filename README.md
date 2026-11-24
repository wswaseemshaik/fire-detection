# Fire & Smoke Detection

Real-time fire and smoke detection on **RTSP / RTMP / HTTP(S) streams, local video files, or webcams**,
with a live web dashboard, alerting, snapshots and optional recording.

- Detector: YOLOv8n fine-tuned on fire + smoke (`models/fire-smoke-yolov8n.pt`, ~45 ms/frame on CPU).
  Swap in any Ultralytics-compatible weights via `FIRE_WEIGHTS`.
- Any number of sources at once, each in its own thread, auto-reconnecting if a stream drops.
- Web UI: live annotated MJPEG per source, alarm highlighting, alert feed with snapshots, sound.
- Alerts are debounced (N consecutive positive frames) and rate-limited (cooldown), saved as JPEG
  snapshots + `data/events.jsonl`, pushed over WebSocket, and optionally POSTed to a webhook.
- CLI for headless use: preview window, write an annotated MP4, print alerts to stdout.

## Quick start

```bash
pip install -r requirements.txt        # needs ffmpeg installed for RTSP/RTMP (apt install ffmpeg)
python run.py                          # dashboard at http://localhost:8000
```

Add a source in the UI, or via the API:

```bash
curl -X POST localhost:8000/api/sources -H 'Content-Type: application/json' \
  -d '{"source":"rtsp://user:pass@192.168.1.10:554/stream1","name":"Warehouse cam"}'
curl -X POST localhost:8000/api/sources -H 'Content-Type: application/json' \
  -d '{"source":"/videos/test.mp4","name":"Test clip","record":true}'
```

Sources are persisted to `sources.json` and restored on restart. You can also pre-load them with
`FIRE_SOURCES=rtsp://...,rtmp://...,/path/a.mp4`.

### CLI (no server)

```bash
python cli.py rtsp://user:pass@cam/stream --show        # preview window, q to quit
python cli.py input.mp4 --no-loop --output annotated.mp4
python cli.py 0 --show --conf 0.4                        # webcam
```

### Docker

```bash
cp .env.example .env
docker compose up --build        # http://localhost:8000
```

## API

| Method | Path | Description |
|---|---|---|
| GET | `/api/health` | model + device info |
| GET | `/api/sources` | all sources with status, fps, current detections, alarm flag |
| POST | `/api/sources` | `{"source": "...", "name": "...", "record": false}` |
| DELETE | `/api/sources/{id}` | stop and remove |
| GET | `/api/sources/{id}/stream` | annotated MJPEG stream |
| GET | `/api/sources/{id}/snapshot.jpg` | latest annotated frame |
| GET | `/api/events?limit=100` | recent alerts (newest first) |
| WS | `/ws/events` | `{"type":"alert",...}` on each alert, `{"type":"status",...}` every 2 s |
| GET | `/snapshots/<file>` | alert snapshot images |

Alert payload (also sent to `FIRE_WEBHOOK_URL`):

```json
{"id": "...", "timestamp": "2026-11-24T10:32:21Z", "source_id": "e45e70ba", "source_name": "RTSP cam",
 "labels": ["fire"], "max_confidence": 0.84,
 "detections": [{"label": "fire", "confidence": 0.84, "x1": 176, "y1": 106, "x2": 381, "y2": 490}],
 "snapshot": "/snapshots/20261124T103221_e45e70ba_fire.jpg"}
```

## Configuration

All settings are environment variables; see `.env.example`. The important ones:

| Variable | Default | Meaning |
|---|---|---|
| `FIRE_WEIGHTS` | bundled yolov8n | YOLO weights path |
| `FIRE_DEVICE` | `cpu` | `cuda`, `cuda:0`, `mps` for GPU |
| `FIRE_CONF` | `0.35` | confidence threshold |
| `FIRE_DETECT_EVERY` | `1` | run the model every N frames (raise on slow CPUs; boxes are re-used between) |
| `FIRE_MIN_CONSECUTIVE` | `3` | positive passes needed before an alert |
| `FIRE_ALERT_COOLDOWN` | `30` | seconds between alerts per source |
| `FIRE_WEBHOOK_URL` | empty | POST alerts here |
| `FIRE_LOOP_FILES` | `true` | loop local files (set false to stop at EOF) |

RTSP is forced to TCP with a 5 s socket timeout via `OPENCV_FFMPEG_CAPTURE_OPTIONS` (set in `app/config.py`).

## Layout

```
app/config.py     settings (env vars)
app/stream.py     threaded capture w/ reconnect, latest-frame buffer, file pacing/looping
app/detector.py   YOLO wrapper + drawing
app/events.py     alert debounce/cooldown, snapshot + jsonl log, webhook, subscribers
app/pipeline.py   per-source worker (read -> detect -> annotate -> alert -> MJPEG/record) + manager
app/main.py       FastAPI REST / MJPEG / WebSocket
app/static/       dashboard
cli.py            headless runner        run.py  server launcher
tests/            pytest
```

Run tests: `python -m pytest tests -q`

## Tips

- CPU budget is shared across sources (one model instance). Three 640px streams run at ~10 fps each
  on a laptop CPU; use `FIRE_DETECT_EVERY=2` or a GPU for more cameras.
- Fire/smoke models false-positive on sunsets, headlights, steam and fog; tune `FIRE_CONF` and
  `FIRE_MIN_CONSECUTIVE` for your scene, or fine-tune the model on your own footage.
- Model weights are AGPL-3.0 (Ultralytics); check licensing for commercial deployment.
