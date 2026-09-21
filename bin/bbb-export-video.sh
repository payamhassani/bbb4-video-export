#!/bin/bash
# Renders one BBB presentation-format recording into a single downloadable
# MP4 by driving a real (virtual-display) Chrome through the existing
# playback page and screen/audio-capturing it with ffmpeg. The presentation
# player is treated as a black box, so this works regardless of BBB's
# internal recording architecture (Kurento, mediasoup, or whatever comes
# next) — it doesn't touch BBB's recording pipeline or raw media sources at
# all, just watches the same page a real user would and records what plays.
#
# Usage: bbb-export-video.sh <record_id> <playback_url> <duration_seconds> <output_path>
set -Eeuo pipefail

RECORD_ID="$1"
PLAYBACK_URL="$2"
DURATION="$3"
OUTPUT_PATH="$4"

DISPLAY_NUM=":$((90 + RANDOM % 500))"
PROFILE_DIR="$(mktemp -d /tmp/bbb-export-profile.XXXXXX)"
SINK_NAME="bbb_export_sink_$$"
LOG_DIR="${LOG_DIR:-/var/log/bbb-video-export}"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/${RECORD_ID}.log"

cleanup() {
    # Preserve whatever exit status got us here (success or failure) - without
    # this, a hiccup in the cleanup itself (see below) would silently turn a
    # successful export into a reported failure.
    local status=$?
    [ -n "${CHROME_PID:-}" ] && kill "$CHROME_PID" 2>/dev/null || true
    # Killing the main Chrome process only sends it SIGTERM; its renderer/GPU/
    # zygote children can still be exiting and holding files open under
    # PROFILE_DIR for a moment after that. Wait for it, then make sure
    # anything left pinned to this profile dir is gone before removing it -
    # otherwise `rm -rf` can transiently race a child process and fail with
    # "Directory not empty".
    [ -n "${CHROME_PID:-}" ] && wait "$CHROME_PID" 2>/dev/null
    pkill -9 -f "$PROFILE_DIR" 2>/dev/null || true
    [ -n "${XVFB_PID:-}" ] && kill "$XVFB_PID" 2>/dev/null || true
    MODULE_ID="$(pactl list short modules 2>/dev/null | awk -v s="$SINK_NAME" '$0 ~ s {print $1}')"
    [ -n "$MODULE_ID" ] && pactl unload-module "$MODULE_ID" 2>/dev/null || true
    for _ in 1 2 3; do
        rm -rf "$PROFILE_DIR" 2>/dev/null && break
        sleep 1
    done
    exit "$status"
}
trap cleanup EXIT

echo "[$(date -Is)] Exporting $RECORD_ID (duration=${DURATION}s) -> $OUTPUT_PATH" >> "$LOG_FILE"

# Run from an unattended systemd service (no login session), so none of
# XDG_RUNTIME_DIR / a D-Bus session / a running PulseAudio daemon can be
# assumed to exist the way they would in an interactive shell — every one
# of those has to be set up explicitly, or pactl/ffmpeg's pulse capture
# and Chrome's own D-Bus calls fail with a bare "Connection refused".
export XDG_RUNTIME_DIR="/run/user/$(id -u)"
mkdir -p "$XDG_RUNTIME_DIR"

if ! pactl info >> "$LOG_FILE" 2>&1; then
    pulseaudio -D --exit-idle-time=-1 >> "$LOG_FILE" 2>&1
    sleep 2
fi

export DISPLAY="$DISPLAY_NUM"
Xvfb "$DISPLAY_NUM" -screen 0 1280x720x24 >> "$LOG_FILE" 2>&1 &
XVFB_PID=$!
sleep 2

pactl load-module module-null-sink sink_name="$SINK_NAME" >> "$LOG_FILE" 2>&1 || true
pactl set-default-sink "$SINK_NAME" >> "$LOG_FILE" 2>&1 || true

google-chrome --no-sandbox --disable-gpu --disable-dev-shm-usage \
    --no-first-run --no-default-browser-check \
    --autoplay-policy=no-user-gesture-required \
    --window-size=1280,720 --window-position=0,0 --start-maximized \
    --user-data-dir="$PROFILE_DIR" \
    "$PLAYBACK_URL" >> "$LOG_FILE" 2>&1 &
CHROME_PID=$!

# Dismiss Chrome's first-run "Additional Terms of Service" dialog — its
# default button is focused, so Enter accepts it. A no-op if the dialog
# isn't shown (e.g. a Chrome version/build that doesn't have it).
sleep 4
xdotool key Return >> "$LOG_FILE" 2>&1 || true
sleep 6

# The presentation player never autoplays on its own — there's no HTML5
# <video autoplay> the browser's autoplay policy would apply to, it's a
# custom player that needs an actual click on its own play button. That
# button is reliably in this same spot right after a fresh recording loads.
xdotool mousemove 640 430 click 1 >> "$LOG_FILE" 2>&1 || true
sleep 3

TMP_OUTPUT="${OUTPUT_PATH}.partial.mp4"
ffmpeg -y -f x11grab -video_size 1280x720 -framerate 15 -i "$DISPLAY_NUM" \
    -f pulse -i "${SINK_NAME}.monitor" \
    -t "$DURATION" -c:v libx264 -preset veryfast -pix_fmt yuv420p -c:a aac \
    "$TMP_OUTPUT" >> "$LOG_FILE" 2>&1

mv "$TMP_OUTPUT" "$OUTPUT_PATH"
echo "[$(date -Is)] Done: $(du -h "$OUTPUT_PATH" | cut -f1)" >> "$LOG_FILE"
