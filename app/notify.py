"""Outbound notifications: webhook, Telegram, email. All run in background threads."""
from __future__ import annotations

import json
import logging
import mimetypes
import smtplib
import threading
import urllib.request
import uuid
from email.message import EmailMessage
from pathlib import Path

from .config import Settings

log = logging.getLogger(__name__)
UA = "fire-detection/1.0"


class Notifier:
    def __init__(self, settings: Settings):
        self.s = settings

    def channels(self) -> dict[str, bool]:
        return {
            "webhook": bool(self.s.webhook_url),
            "telegram": bool(self.s.telegram_bot_token and self.s.telegram_chat_id),
            "email": bool(self.s.smtp_host and self.s.smtp_to),
        }

    def send(self, event: dict, snapshot_path: Path | None = None) -> None:
        for name, enabled in self.channels().items():
            if enabled:
                threading.Thread(target=self._safe, args=(name, event, snapshot_path), daemon=True).start()

    def test(self, channel: str) -> tuple[bool, str]:
        event = {"id": "test", "timestamp": "now", "source_name": "Test source", "labels": ["fire"],
                 "max_confidence": 0.99, "detections": [], "snapshot": None, "test": True}
        try:
            getattr(self, f"_{channel}")(event, None)
            return True, "sent"
        except Exception as e:
            return False, f"{type(e).__name__}: {e}"

    def _safe(self, channel: str, event: dict, snap: Path | None) -> None:
        try:
            getattr(self, f"_{channel}")(event, snap)
            log.info("notification sent via %s", channel)
        except Exception as e:
            log.error("%s notification failed: %s", channel, e)

    # channels ---------------------------------------------------------------
    def _webhook(self, event: dict, snap: Path | None) -> None:
        req = urllib.request.Request(self.s.webhook_url, data=json.dumps(event).encode(),
                                     headers={"Content-Type": "application/json", "User-Agent": UA}, method="POST")
        with urllib.request.urlopen(req, timeout=10) as r:
            if r.status >= 300:
                raise RuntimeError(f"HTTP {r.status}")

    def _text(self, event: dict) -> str:
        labels = " + ".join(event["labels"]).upper()
        return f"🔥 {labels} detected on {event['source_name']} (confidence {event['max_confidence']:.0%}) at {event['timestamp']}"

    def _telegram(self, event: dict, snap: Path | None) -> None:
        base = f"https://api.telegram.org/bot{self.s.telegram_bot_token}"
        if snap and snap.exists():
            boundary = uuid.uuid4().hex
            body = b""
            for k, v in (("chat_id", self.s.telegram_chat_id), ("caption", self._text(event))):
                body += f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode()
            body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"photo\"; filename=\"{snap.name}\"\r\n"
                     f"Content-Type: image/jpeg\r\n\r\n").encode() + snap.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
            req = urllib.request.Request(f"{base}/sendPhoto", data=body, method="POST",
                                         headers={"Content-Type": f"multipart/form-data; boundary={boundary}", "User-Agent": UA})
        else:
            req = urllib.request.Request(f"{base}/sendMessage", method="POST",
                                         data=json.dumps({"chat_id": self.s.telegram_chat_id, "text": self._text(event)}).encode(),
                                         headers={"Content-Type": "application/json", "User-Agent": UA})
        with urllib.request.urlopen(req, timeout=15) as r:
            resp = json.loads(r.read())
            if not resp.get("ok"):
                raise RuntimeError(resp.get("description", "telegram error"))

    def _email(self, event: dict, snap: Path | None) -> None:
        msg = EmailMessage()
        msg["Subject"] = f"[FIRE ALERT] {' + '.join(event['labels']).upper()} on {event['source_name']}"
        msg["From"] = self.s.smtp_from or self.s.smtp_user
        msg["To"] = self.s.smtp_to
        msg.set_content(self._text(event) + "\n\n" + json.dumps(event, indent=2))
        if snap and snap.exists():
            ctype = mimetypes.guess_type(str(snap))[0] or "image/jpeg"
            maintype, subtype = ctype.split("/")
            msg.add_attachment(snap.read_bytes(), maintype=maintype, subtype=subtype, filename=snap.name)
        if self.s.smtp_tls and self.s.smtp_port == 465:
            server = smtplib.SMTP_SSL(self.s.smtp_host, self.s.smtp_port, timeout=20)
        else:
            server = smtplib.SMTP(self.s.smtp_host, self.s.smtp_port, timeout=20)
            if self.s.smtp_tls:
                server.starttls()
        with server:
            if self.s.smtp_user:
                server.login(self.s.smtp_user, self.s.smtp_password)
            server.send_message(msg)
