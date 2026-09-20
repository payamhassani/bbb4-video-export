#!/usr/bin/env python3
"""Nightly batch driver for bbb-export-video.sh.

Queries this BBB server's own getRecordings API, finds every published
recording that doesn't have a video export yet, and renders each one in
turn (never in parallel — each render drives a real browser and is as
CPU-heavy as the class was live). Only runs within a configured overnight
window (default 23:00-07:00) so a busy day of classes never lets this
bleed into the next morning's classes — if the window closes mid-list, the
rest is picked up on the next scheduled run; anything already exported is
never redone.

Configuration is via environment variables (see systemd/bbb-video-export.service):
  BBB_URL           e.g. https://bbb.example.com/bigbluebutton/api/
  BBB_SECRET        the shared secret from `bbb-conf --secret`
  OUTPUT_DIR        where finished MP4s land (default /var/bigbluebutton/video-exports)
  EXPORT_SCRIPT     path to bbb-export-video.sh
  TIMEZONE          IANA tz name for the overnight window (default Asia/Tehran)
  WINDOW_START_HOUR / WINDOW_END_HOUR  the overnight window, 24h clock (default 23 / 7)
"""
import hashlib
import os
import subprocess
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime
from zoneinfo import ZoneInfo

BBB_URL = os.environ.get("BBB_URL", "https://bbb.example.com/bigbluebutton/api/")
BBB_SECRET = os.environ["BBB_SECRET"]
OUTPUT_DIR = os.environ.get("OUTPUT_DIR", "/var/bigbluebutton/video-exports")
EXPORT_SCRIPT = os.environ.get("EXPORT_SCRIPT", "/usr/local/bin/bbb-export-video.sh")
TIMEZONE = ZoneInfo(os.environ.get("TIMEZONE", "Asia/Tehran"))
WINDOW_START_HOUR = int(os.environ.get("WINDOW_START_HOUR", "23"))
WINDOW_END_HOUR = int(os.environ.get("WINDOW_END_HOUR", "7"))
LOG_PATH = os.environ.get("LOG_PATH", "/var/log/bbb-video-export/nightly-batch.log")

# a static "the recording finished, nothing more happens" tail is harmless
# (still a correct, fully playable video) — this padding just makes sure a
# slightly-off duration never cuts off real content near the end.
DURATION_PAD_SECONDS = 10
MIN_DURATION_SECONDS = 10


def log(message: str) -> None:
    line = f"[{datetime.now(TIMEZONE).isoformat()}] {message}"
    print(line, flush=True)
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    with open(LOG_PATH, "a") as f:
        f.write(line + "\n")


def within_window(now: datetime) -> bool:
    """The window is expected to wrap past midnight (e.g. 23:00 -> 07:00)."""
    if WINDOW_START_HOUR <= WINDOW_END_HOUR:
        return WINDOW_START_HOUR <= now.hour < WINDOW_END_HOUR
    return now.hour >= WINDOW_START_HOUR or now.hour < WINDOW_END_HOUR


def bbb_api_call(action: str, params: dict) -> ET.Element:
    query = urllib.parse.urlencode(params)
    checksum = hashlib.sha256(f"{action}{query}{BBB_SECRET}".encode()).hexdigest()
    url = f"{BBB_URL}{action}?{query}&checksum={checksum}" if query else f"{BBB_URL}{action}?checksum={checksum}"
    with urllib.request.urlopen(url, timeout=30) as response:
        return ET.fromstring(response.read())


def pending_recordings():
    root = bbb_api_call("getRecordings", {})
    if root.findtext("returncode") != "SUCCESS":
        log(f"getRecordings failed: {ET.tostring(root, encoding='unicode')}")
        return

    for recording in root.findall("./recordings/recording"):
        record_id = recording.findtext("recordID")
        state = recording.findtext("state")
        if not record_id or state != "published":
            continue

        output_path = os.path.join(OUTPUT_DIR, f"{record_id}.mp4")
        if os.path.exists(output_path):
            continue  # already exported on a previous run

        playback_url = None
        for fmt in recording.findall("./playback/format"):
            if fmt.findtext("type") == "presentation":
                playback_url = fmt.findtext("url")
                break
        if not playback_url:
            continue

        start_ms = int(recording.findtext("startTime") or 0)
        end_ms = int(recording.findtext("endTime") or 0)
        duration_seconds = max(MIN_DURATION_SECONDS, round((end_ms - start_ms) / 1000) + DURATION_PAD_SECONDS)

        yield record_id, playback_url, duration_seconds, output_path


def main() -> int:
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    exported = 0
    skipped_window = 0

    for record_id, playback_url, duration, output_path in pending_recordings():
        now = datetime.now(TIMEZONE)
        if not within_window(now):
            log(
                f"Stopping — outside the {WINDOW_START_HOUR:02d}:00-{WINDOW_END_HOUR:02d}:00 "
                f"window (now {now.strftime('%H:%M')} {TIMEZONE.key})."
            )
            skipped_window += 1
            break

        log(f"Exporting {record_id} (~{duration}s)...")
        result = subprocess.run(
            [EXPORT_SCRIPT, record_id, playback_url, str(duration), output_path],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0 and os.path.exists(output_path):
            log(f"OK: {record_id}")
            exported += 1
        else:
            log(f"FAILED: {record_id} — {result.stderr.strip()[-500:]}")

    log(f"Nightly batch finished — {exported} recording(s) exported, {skipped_window} remaining for next run.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
