import numpy as np
import cv2

from app.config import settings
from app.detector import Detector
from app.events import AlertState
from app.stream import is_live_source


def test_alert_state_debounce_and_cooldown():
    a = AlertState(min_consecutive=3, cooldown=1000)
    assert [a.update(True) for _ in range(3)] == [False, False, True]
    assert a.update(True) is False  # inside cooldown
    assert a.active
    assert a.update(False) is False
    assert not a.active and a.consecutive == 0


def test_is_live_source():
    assert is_live_source("rtsp://x/y")
    assert is_live_source("rtmp://x/y")
    assert is_live_source("https://x/y.m3u8")
    assert is_live_source("0")
    assert not is_live_source("/tmp/video.mp4")


def test_detector_runs_and_annotates():
    d = Detector(settings.weights, "cpu", 0.25, 0.5, 320)
    img = np.zeros((360, 640, 3), dtype=np.uint8)
    dets = d.detect(img)
    assert isinstance(dets, list)
    out = d.annotate(img, dets, "hdr")
    assert out.shape == img.shape
    assert set(d.names.values()) >= {"fire", "smoke"}
