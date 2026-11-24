import json
import os
import tempfile
from pathlib import Path

import cv2
import numpy as np
import pytest

os.environ.setdefault("FIRE_DATA_DIR", tempfile.mkdtemp(prefix="firetest-"))

from app.config import Settings, settings  # noqa: E402
from app.detector import Detector  # noqa: E402
from app.events import AlertState, EventBus  # noqa: E402
from app.files import browse, safe_name  # noqa: E402
from app.jobs import JobManager  # noqa: E402
from app.stream import is_live_source  # noqa: E402
from app.video_writer import VideoWriter  # noqa: E402


@pytest.fixture(scope="module")
def detector():
    return Detector(settings.weights, "cpu", 0.25, 0.5, 320)


def test_alert_state_debounce_and_cooldown():
    s = Settings()
    s.min_consecutive, s.alert_cooldown = 3, 1000
    a = AlertState(s)
    assert [a.update(True) for _ in range(3)] == [False, False, True]
    assert a.update(True) is False
    assert a.active
    assert a.update(False) is False
    assert not a.active and a.consecutive == 0


def test_settings_update_persists_and_validates():
    s = Settings()
    changed = s.update({"conf": "0.5", "detect_every": 0, "weights": "nope.pt", "bogus": 1, "detect_fire": "false"})
    assert changed["conf"] == 0.5 and s.detect_every == 1 and changed["detect_fire"] is False
    assert "weights" not in changed and "bogus" not in changed
    assert json.loads(s.settings_file.read_text())["conf"] == 0.5
    assert s.to_dict()["smtp_password"] == ""
    s.update({"smtp_password": "secret123"})
    assert s.to_dict()["smtp_password"].endswith("t123") and s.to_dict()["smtp_password"].startswith("••")


def test_is_live_source():
    assert is_live_source("rtsp://x/y") and is_live_source("rtmp://x/y") and is_live_source("https://x/y.m3u8")
    assert is_live_source("0") and not is_live_source("/tmp/video.mp4")


def test_detector_runs_and_annotates(detector):
    img = np.zeros((360, 640, 3), dtype=np.uint8)
    dets = detector.detect(img)
    assert isinstance(dets, list)
    assert detector.annotate(img, dets, "hdr").shape == img.shape
    assert set(detector.names.values()) >= {"fire", "smoke"}
    detector.configure(labels={"smoke"})
    assert all(d.label == "smoke" for d in detector.detect(img))
    detector.configure(labels=None)


def test_video_writer_produces_playable_mp4(tmp_path):
    out = tmp_path / "t.mp4"
    w = VideoWriter(out, 10, (64, 48))
    for _ in range(10):
        w.write(np.zeros((48, 64, 3), dtype=np.uint8))
    w.close()
    cap = cv2.VideoCapture(str(out))
    assert cap.isOpened() and cap.get(cv2.CAP_PROP_FRAME_COUNT) >= 9


def test_event_bus_emit_delete_stats(detector):
    bus = EventBus(settings)
    from app.detector import Detection
    ev = bus.emit("abc", "cam", np.zeros((48, 64, 3), dtype=np.uint8), [Detection("fire", 0.9, 1, 1, 10, 10)])
    assert (settings.snapshot_dir / ev["snapshot"].rsplit("/", 1)[-1]).exists()
    assert bus.stats()["last_hour"] >= 1 and bus.recent(10, label="fire")[0]["id"] == ev["id"]
    assert bus.delete(ev["id"]) and not bus.delete(ev["id"])


def test_files_browse_and_safe_name(tmp_path):
    (tmp_path / "a.mp4").write_bytes(b"x")
    (tmp_path / "sub").mkdir()
    d = browse(str(tmp_path))
    assert [f["name"] for f in d["files"]] == ["a.mp4"] and [x["name"] for x in d["dirs"]] == ["sub"]
    assert safe_name("../../evil name.mp4").endswith("evil_name.mp4")


def test_batch_job_on_folder(detector, tmp_path):
    w = VideoWriter(tmp_path / "blank.mp4", 10, (64, 48))
    for _ in range(20):
        w.write(np.zeros((48, 64, 3), dtype=np.uint8))
    w.close()
    jm = JobManager(detector, settings)
    job = jm.create(path=str(tmp_path), sample_fps=5)
    job._thread.join(timeout=60)
    assert job.status == "done" and job.to_dict()["progress"] == 1.0
    assert job.results[0]["name"] == "blank.mp4" and job.results[0]["detections"] == 0
    assert "blank.mp4" in job.csv()
    assert jm.delete(job.id)
