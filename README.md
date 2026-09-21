# bbb4-video-export

Turns a BigBlueButton **presentation-format** recording into a single
downloadable MP4 — on **BigBlueButton 4.0** (mediasoup-based), where the
existing community tools (`bbb-playback-video`, `bbb-mp4`, `bbb-download`,
`bbb-render`, ...) don't apply: they were all built against BBB 2.x/3.x's
older Kurento-based recording pipeline and either raw-compose per-stream
media files that no longer exist in the same shape, or are outright
archived/unmaintained.

## How it works

BBB's presentation format is an interactive HTML5 replay (slides + audio +
chat + whiteboard, synced by timeline) — not a single file, so there's
nothing to just "download." Rather than depend on BBB's internal recording
architecture (which just changed once and can change again), this tool
treats the presentation player as a **black box**: it opens the existing
playback URL in a real browser running on a virtual display, clicks play,
and captures the screen + audio with `ffmpeg` for the recording's real
duration. Whatever renders in the player is what ends up in the MP4 — so
it's naturally resilient to BBB changing how it stores/produces recordings
internally, and needed zero changes to get working against a brand-new
BBB 4.0 (rc.3) server.

## Requirements

- A Linux BBB server (tested on Ubuntu 24.04, BBB 4.0.0-rc.3)
- `google-chrome-stable`, `xvfb`, `ffmpeg`, `xdotool`, `pulseaudio`
- Python 3.9+ (standard library only — no pip installs)

## Install

```bash
sudo cp bin/bbb-export-video.sh /usr/local/bin/
sudo cp bin/bbb-nightly-batch.py /usr/local/bin/
sudo chmod +x /usr/local/bin/bbb-export-video.sh /usr/local/bin/bbb-nightly-batch.py

sudo mkdir -p /var/bigbluebutton/video-exports
sudo chown "$(whoami)":"$(whoami)" /var/bigbluebutton/video-exports

sudo cp nginx/video-exports.nginx /etc/bigbluebutton/nginx/
sudo nginx -t && sudo systemctl reload nginx

sudo mkdir -p /etc/bbb-video-export
sudo cp env.example /etc/bbb-video-export/env
sudo "$EDITOR" /etc/bbb-video-export/env   # fill in BBB_URL / BBB_SECRET (see `bbb-conf --secret`)

sudo cp systemd/bbb-video-export.service systemd/bbb-video-export.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now bbb-video-export.timer
```

Exported files land at `/var/bigbluebutton/video-exports/<recordID>.mp4` and
are served at `https://your-bbb-domain/video-exports/<recordID>.mp4` (same
"long unguessable ID, no extra auth" model BBB's own playback URLs already
use — put your own auth in front of that URL from your application if you
need per-user access control, the way you'd already gate the presentation
playback link).

## Run it on demand (one recording)

```bash
/usr/local/bin/bbb-export-video.sh <recordID> <playbackUrl> <durationSeconds> /var/bigbluebutton/video-exports/<recordID>.mp4
```

## Why an overnight batch, not "export right after class"

Each export drives a real browser for roughly the recording's own real
duration (a 1-hour class takes ~1 hour to export) and is as CPU-heavy as
the class was live. Doing this immediately after every class competes with
whatever else is running during the school day. The nightly batch
(`bbb-nightly-batch.py`, wired to run once via the systemd timer) processes
the day's backlog sequentially inside a configurable overnight window
(default 23:00–07:00) and simply stops — leaving the rest for the next
run — if it's still working when the window closes, so it never bleeds
into the next morning's classes.

## Getting the duration right

`bbb-nightly-batch.py` reads the capture duration from the recording's own
`metadata.xml` (`<playback><duration>`, in milliseconds) rather than the
`getRecordings` API's `startTime`/`endTime` span. Those two are **not**
the same thing: a moderator who presses Stop but leaves the room open for
a few more minutes before it actually closes gets that idle time baked
into `endTime - startTime`, producing a correct-but-much-too-long export
with a long static tail. `metadata.xml` already reflects the true
recorded/rendered length, which is what you actually want.

## Known limitations

- If BBB ever changes the presentation player's DOM (e.g. moves the play
  button), the one hardcoded click coordinate in `bbb-export-video.sh`
  will need updating.
- One export at a time by design (see above) — don't run multiple
  instances of `bbb-export-video.sh` concurrently against the same virtual
  display/audio sink.

## License

MIT — see [LICENSE](LICENSE).
