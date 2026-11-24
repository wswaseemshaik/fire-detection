#!/usr/bin/env python3
"""Headless / desktop CLI: run detection on a single source without the web server.

Examples:
  python cli.py rtsp://user:pass@cam/stream --show
  python cli.py video.mp4 --output annotated.mp4 --no-loop
  python cli.py 0 --show --conf 0.4
"""
from __future__ import annotations

import argparse
import logging
import signal
import sys
import time

from app.config import settings  # noqa: F401  (sets FFmpeg capture options before cv2 import)
import cv2

from app.detector import Detector
from app.events import EventBus
from app.pipeline import SourceWorker


def main() -> int:
    p = argparse.ArgumentParser(description="Real-time fire & smoke detection")
    p.add_argument("source", help="rtsp://, rtmp://, http(s)://, file path, or webcam index")
    p.add_argument("--name", default="")
    p.add_argument("--weights", default=settings.weights)
    p.add_argument("--device", default=settings.device)
    p.add_argument("--conf", type=float, default=settings.conf)
    p.add_argument("--imgsz", type=int, default=settings.imgsz)
    p.add_argument("--detect-every", type=int, default=settings.detect_every)
    p.add_argument("--show", action="store_true", help="open a preview window")
    p.add_argument("--output", help="write annotated video to this .mp4 path")
    p.add_argument("--no-loop", action="store_true", help="stop when a local file ends")
    p.add_argument("--webhook", default=settings.webhook_url)
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings.detect_every = args.detect_every
    settings.loop_files = not args.no_loop

    detector = Detector(args.weights, args.device, args.conf, settings.iou, args.imgsz)
    bus = EventBus(settings.snapshot_dir, args.webhook)
    bus.subscribe(lambda e: print(f"\a>>> ALERT {e['labels']} conf={e['max_confidence']} snapshot={e['snapshot']}", flush=True))
    worker = SourceWorker(args.source, args.name, detector, bus, settings).start()

    writer = None
    stop = {"flag": False}
    signal.signal(signal.SIGINT, lambda *_: stop.__setitem__("flag", True))

    seq = -1
    try:
        while not stop["flag"]:
            seq, data = worker.jpeg(seq, 1.0)
            if worker.reader.ended:
                break
            if data is None:
                continue
            if args.show or args.output:
                import numpy as np
                frame = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
                if args.output and worker.frames_processed > 0:  # skip "connecting" placeholder frames
                    if writer is None:
                        fps = worker.reader.fps if 0 < worker.reader.fps < 120 else 15.0
                        size = (frame.shape[1], frame.shape[0])
                        writer = cv2.VideoWriter(args.output, cv2.VideoWriter_fourcc(*"mp4v"), fps, size)
                    if (frame.shape[1], frame.shape[0]) == size:
                        writer.write(frame)
                if args.show:
                    cv2.imshow("fire-detection", frame)
                    if cv2.waitKey(1) & 0xFF in (27, ord("q")):
                        break
            else:
                s = worker.status()
                labels = sorted({d["label"] for d in s["detections"]})
                print(f"\r{s['fps']:5.1f} fps  frames={s['frames']}  {'ALARM ' + ','.join(labels) if s['alarm'] else 'clear'}      ", end="", flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        print()
        worker.stop()
        if writer:
            writer.release()
        if args.show:
            cv2.destroyAllWindows()
    return 0


if __name__ == "__main__":
    sys.exit(main())
