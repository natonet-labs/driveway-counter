#!/usr/bin/env python3
"""Hailo Driveway Counter - Single zone traffic counting system.

Tracks objects entering/exiting a single driveway zone using a Hailo-8
AI accelerator for inference and GStreamer for video processing.
Detections are counted directionally (entries vs exits) and daily
statistics are persisted to JSON reports with optional hourly Cloudflare sync.

Hardware: Raspberry Pi 5 + Hailo-8 AI HAT (26 TOPS)

Dependencies:
    - gi (PyGObject): GStreamer pipeline construction
    - hailo: Hailo Python bindings (system package, not pip)
    - cv2 (OpenCV): Zone polygon mask construction
    - numpy: Zone coordinate scaling
    - python-dotenv: .env configuration loading
    - requests: Cloudflare metrics upload (background thread only)
"""

from __future__ import annotations

import ast
import json
import logging
import os
import queue
import threading
import time
from collections import deque
from datetime import datetime
from typing import Any
from urllib.parse import quote

import cv2
import gi
import numpy as np
import requests
from dotenv import load_dotenv

gi.require_version("Gst", "1.0")
from gi.repository import GLib, Gst  # noqa: E402

try:
    import hailo
except ImportError as e:
    raise SystemExit(
        "Hailo Python module not found. "
        "Ensure venv uses --system-site-packages and that "
        "/usr/lib/python3/dist-packages is in system_packages.pth. "
        f"Error: {e}"
    )

load_dotenv()

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger: logging.Logger = logging.getLogger(__name__)

IS_DEBUG: bool = os.getenv("ISDEBUG", "False").lower() == "true"
if IS_DEBUG:
    logger.setLevel(logging.DEBUG)

# ---------------------------------------------------------------------------
# Camera / stream configuration
# ---------------------------------------------------------------------------

USERNAME: str = os.getenv("USERNAME", "")
PASSWORD: str = os.getenv("PASSWORD", "")
IPADDRESS: str = os.getenv("IPADDRESS", "")
CHANNEL: int = int(os.getenv("CHANNEL", "1"))
SUBTYPE: int = int(os.getenv("SUBTYPE", "1"))  # 1 = H.264 substream (low CPU)

ORIG_W: int = int(os.getenv("ORIG_W", "704"))
ORIG_H: int = int(os.getenv("ORIG_H", "480"))
IMG_W: int = int(os.getenv("IMG_W", "704"))
IMG_H: int = int(os.getenv("IMG_H", "480"))

CONF_THRESH: float = float(os.getenv("CONF_THRESH", "0.65"))

# ---------------------------------------------------------------------------
# Detection filtering
# ---------------------------------------------------------------------------

# Minimum bounding box area in pixels (filters out tiny false positives like
# leaves, insects, dust particles). At 704x480 resolution:
#  - 1000 px² ≈ 32x32 box (small bird/leaf)
#  - 2500 px² ≈ 50x50 box (sparrow to small cat)
#  - 5000 px² ≈ 71x71 box (cat/dog size)
#  - 10000 px² ≈ 100x100 box (person/small vehicle)
MIN_BBOX_AREA: int = int(os.getenv("MIN_BBOX_AREA", "2500"))

# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------

HEF_MODEL_PATH: str = "./models/yolov8m.hef"

# YOLO postprocess shared library (installed by hailo-all / hailo-rpi5-examples)
YOLO_POST_SO: str = "/usr/local/hailo/resources/so/libyolo_hailortpp_postprocess.so"

# Required by hailocropper on GStreamer 1.26+.
# Omitting so-path causes a segfault on GStreamer 1.26.2.
WHOLE_BUFFER_SO: str = (
    "/usr/lib/aarch64-linux-gnu/hailo/tappas/post_processes"
    "/cropping_algorithms/libwhole_buffer.so"
)

# ---------------------------------------------------------------------------
# GStreamer / pipeline tuning
# ---------------------------------------------------------------------------

# 300 ms is sufficient for a local-network camera; 2000 ms adds unnecessary
# latency and slows pipeline startup.
RTSP_LATENCY_MS: int = 300

# Only relevant for UDP transport; we force TCP so this is a safety fallback.
RTSP_TIMEOUT_US: int = 5_000_000

# H.264 software decoder thread cap (hardware v4l2slh265dec ignores this)
H264_MAX_THREADS: int = 2

HAILO_BATCH_SIZE: int = 1

# Hailo tracker (Kalman filter) — tuned for driveway vehicle speeds
KALMAN_DIST_THR: float = 0.9
IOU_THR: float = 0.65
INIT_IOU_THR: float = 0.7
KEEP_TRACKED_FRAMES: int = 15
KEEP_LOST_FRAMES: int = 5

# appsink — drop oldest frames under load rather than stalling the pipeline
APPSINK_MAX_BUFFERS: int = 2
APPSINK_DROP: bool = True

# ---------------------------------------------------------------------------
# Counting / tracking tuning
# ---------------------------------------------------------------------------

# Minimum horizontal pixel displacement per frame to register a crossing.
# Prevents stationary jitter from generating false counts.
# At 15 FPS: 5 px/frame ≈ 3 mph, 10 px/frame ≈ 6 mph
# Leaves/noise jitter 1–2 px; real vehicles move 5+ px.
VELOCITY_THRESHOLD: int = int(os.getenv("VELOCITY_THRESHOLD", "5"))

# Centroid x-history depth for velocity smoothing
CENTROID_HISTORY_SIZE: int = 5

# Remove a track from state after this many seconds without a detection.
# 60s: objects disappear after 1 minute of no detections
TRACK_TIMEOUT_SEC: int = 60

# ---------------------------------------------------------------------------
# Reporting / upload
# ---------------------------------------------------------------------------

REPORT_DIR: str = os.getenv("REPORT_DIR", "./reports")
os.makedirs(REPORT_DIR, exist_ok=True)

STATS_LOG_INTERVAL_SEC: float = 10.0
UPLOAD_INTERVAL_SEC: float = 3600.0

# Detection watchdog — if Tracks=0 for this many seconds, quit and let
# systemd Restart=always respawn the service.  Catches silent Hailo freezes
# where the pipeline keeps running at 15 FPS but inference has stalled.
# Set to 0 to disable.
WATCHDOG_TIMEOUT_SEC: float = float(os.getenv("WATCHDOG_TIMEOUT_SEC", "300"))

# ---------------------------------------------------------------------------
# Zone setup
# ---------------------------------------------------------------------------


def _parse_zone(env_var: str, default: str) -> np.ndarray:
    """Parse a zone polygon from an environment variable.

    Falls back to *default* if the variable is absent or malformed.
    Uses ast.literal_eval — no arbitrary code execution risk.

    Returns:
        np.ndarray: shape (N, 2), dtype int32
    """
    raw = os.getenv(env_var, default)
    try:
        pts = ast.literal_eval(raw)
    except (ValueError, SyntaxError) as exc:
        logger.warning("Invalid %s — using default. Error: %s", env_var, exc)
        pts = ast.literal_eval(default)
    return np.array(pts, dtype=np.int32)


# Zone defined in original camera resolution, scaled to processing resolution
TRACKING_ZONE_ORIG: np.ndarray = _parse_zone(
    "TRACKING_ZONE",
    "[[100,10],[600,10],[600,460],[100,460]]",
)

_sx: float = IMG_W / ORIG_W
_sy: float = IMG_H / ORIG_H
TRACKING_ZONE: np.ndarray = (TRACKING_ZONE_ORIG * np.array([_sx, _sy])).astype(np.int32)

# 2-D polygon mask — used for accurate point-in-polygon zone checks.
ZONE_MASK: np.ndarray = cv2.fillPoly(
    np.zeros((IMG_H, IMG_W), dtype=np.uint8),
    [TRACKING_ZONE],
    255,
)


def point_in_zone(cx: int, cy: int) -> bool:
    """Return True if pixel (cx, cy) lies inside the tracking zone polygon."""
    if not (0 <= cx < IMG_W and 0 <= cy < IMG_H):
        return False
    return bool(ZONE_MASK[cy, cx])


# ---------------------------------------------------------------------------
# Cloudflare upload — dedicated background thread
# ---------------------------------------------------------------------------

_upload_queue: queue.Queue = queue.Queue()
_last_queued_snapshot: tuple[int, int, str] | None = None  # (entries, exits, date)


def _cloudflare_upload_worker() -> None:
    """Drain the upload queue and POST snapshots to Cloudflare Workers KV.

    Exits cleanly when it receives a None sentinel on the queue.
    """
    worker_url = os.getenv("WORKER_URL", "")
    cf_token = os.getenv("CLOUDFLARE_TOKEN", "")

    if not worker_url or not cf_token:
        logger.warning(
            "WORKER_URL or CLOUDFLARE_TOKEN not set — Cloudflare sync disabled"
        )

    while True:
        stats = _upload_queue.get()
        if stats is None:
            _upload_queue.task_done()
            break

        if not worker_url or not cf_token:
            _upload_queue.task_done()
            continue

        try:
            payload = {
                "key": f"driveway:{stats['date']}",
                "value": {
                    **stats,
                    "hour": datetime.now().hour,
                },
            }
            response = requests.post(
                worker_url,
                json=payload,
                headers={
                    "Authorization": f"Bearer {cf_token}",
                    "Content-Type": "application/json",
                },
                timeout=10,
            )
            response.raise_for_status()
            logger.info("Metrics synced to Cloudflare: %s", stats["date"])
        except requests.RequestException as exc:
            logger.error("Cloudflare sync failed: %s", exc)
        finally:
            _upload_queue.task_done()


# ---------------------------------------------------------------------------
# Daily stats — persisted across restarts
# ---------------------------------------------------------------------------


def _load_daily_stats() -> dict[str, Any]:
    """Load today's JSON report from disk if it exists."""
    today = datetime.now().strftime("%Y-%m-%d")
    path = f"{REPORT_DIR}/driveway_{today}.json"
    try:
        with open(path) as fh:
            data = json.load(fh)
        if data.get("date") == today:
            logger.info(
                "📂 Resumed from existing report: entries=%d exits=%d",
                data["entries"],
                data["exits"],
            )
            return data
    except (OSError, json.JSONDecodeError):
        pass
    return {"date": today, "entries": 0, "exits": 0}


daily_stats: dict[str, Any] = _load_daily_stats()


def _save_report() -> None:
    """Write daily_stats to disk and queue a Cloudflare upload if data changed."""
    global _last_queued_snapshot
    path = f"{REPORT_DIR}/driveway_{daily_stats['date']}.json"
    try:
        with open(path, "w") as fh:
            json.dump(daily_stats, fh, indent=2)
        logger.info("Report saved: %s", path)
        snapshot = (daily_stats["entries"], daily_stats["exits"], daily_stats["date"])
        if snapshot != _last_queued_snapshot:
            _upload_queue.put(dict(daily_stats))
            _last_queued_snapshot = snapshot
        else:
            logger.debug("Cloudflare upload skipped — data unchanged since last upload")
    except OSError as exc:
        logger.error("Failed to save report to %s: %s", path, exc)


# ---------------------------------------------------------------------------
# Per-track state
# ---------------------------------------------------------------------------

# track_id → {"in_zone": bool, "counted_entry": bool, "counted_exit": bool}
_tracked: dict[int, dict[str, Any]] = {}

# track_id → deque of recent centroid x-values (newest at right)
_cx_history: dict[int, deque] = {}

# track_id → datetime of last detection (for stale-track cleanup)
_last_seen: dict[int, datetime] = {}

# Frame counter and timing for stats logging
_frame_count: int = 0
_last_log_time: float = time.monotonic()
_last_upload_time: float = time.monotonic()

# Watchdog: timestamp of the last frame that contained at least one tracked object.
_last_track_time: float = time.monotonic()

# GLib main loop reference — set in main() so the watchdog can quit it.
_main_loop: GLib.MainLoop | None = None


# ---------------------------------------------------------------------------
# GStreamer frame callback
# ---------------------------------------------------------------------------


def _on_new_sample(sink: Any) -> Gst.FlowReturn:
    """Process one frame's Hailo detections and update zone crossing counts.

    Called by GStreamer's appsink for every decoded frame.  Keeps all
    per-track state in module-level dicts so no object allocation occurs
    on the hot path beyond what Hailo returns.

    Zone crossing logic
    -------------------
    Containment  : point_in_zone(cx, cy) — full 2-D polygon check via mask
    Direction    : sign of velocity_x (horizontal centroid displacement)
    Entry        : was outside → now inside, moving inward
    Exit         : was inside  → now outside, moving outward
    Latch reset  : flags reset when a track goes outside the zone so the
                   same vehicle can be counted again on a subsequent pass
    """
    global daily_stats, _frame_count, _last_log_time, _last_upload_time, _last_track_time

    sample = sink.emit("pull-sample")
    if sample is None:
        return Gst.FlowReturn.OK

    _frame_count += 1
    buf = sample.get_buffer()

    try:
        roi = hailo.get_roi_from_buffer(buf)
        detections = roi.get_objects_typed(hailo.HAILO_DETECTION)

        for det in detections:
            if det.get_confidence() < CONF_THRESH:
                continue

            bbox = det.get_bbox()
            # Calculate bbox area in pixels
            bbox_w = (bbox.xmax() - bbox.xmin()) * IMG_W
            bbox_h = (bbox.ymax() - bbox.ymin()) * IMG_H
            bbox_area = int(bbox_w * bbox_h)

            # Filter out tiny detections (noise, leaves, insects)
            if bbox_area < MIN_BBOX_AREA:
                if IS_DEBUG:
                    logger.debug(
                        "Filtered: %s area=%d px² (below %d)",
                        det.get_label(),
                        bbox_area,
                        MIN_BBOX_AREA,
                    )
                continue

            uid_list = det.get_objects_typed(hailo.HAILO_UNIQUE_ID)
            if not uid_list:
                continue
            tid: int = uid_list[0].get_id()

            cx = int((bbox.xmin() + bbox.xmax()) / 2 * IMG_W)
            cy = int((bbox.ymin() + bbox.ymax()) / 2 * IMG_H)

            if not (0 <= cx < IMG_W and 0 <= cy < IMG_H):
                continue

            _last_seen[tid] = datetime.now()

            # Update watchdog — we have at least one real detection
            if tid not in _tracked:
                _last_track_time = time.monotonic()

            # Initialise state for new tracks
            if tid not in _tracked:
                _tracked[tid] = {
                    "in_zone": point_in_zone(cx, cy),
                    "counted_entry": False,
                    "counted_exit": False,
                }

            state = _tracked[tid]

            # Build x-history for velocity
            history = _cx_history.setdefault(tid, deque(maxlen=CENTROID_HISTORY_SIZE))
            history.append(cx)

            in_zone_now = point_in_zone(cx, cy)
            was_in_zone = state["in_zone"]

            if len(history) >= 2:
                vx = history[-1] - history[-2]

                # Reset latches when the object is clearly outside the zone
                # so it can be counted again on a new approach.
                if not in_zone_now:
                    state["counted_entry"] = False
                    state["counted_exit"] = False

                # ENTRY: outside → inside
                if not was_in_zone and in_zone_now and abs(vx) > VELOCITY_THRESHOLD:
                    if not state["counted_entry"]:
                        daily_stats["entries"] += 1
                        state["counted_entry"] = True
                        logger.info(
                            "➡️  ENTRY vx=%.1f cx=%d %s ID:%d conf=%.2f area=%d",
                            vx,
                            cx,
                            det.get_label(),
                            tid,
                            det.get_confidence(),
                            bbox_area,
                        )

                # EXIT: inside → outside
                elif was_in_zone and not in_zone_now and abs(vx) > VELOCITY_THRESHOLD:
                    if not state["counted_exit"]:
                        daily_stats["exits"] += 1
                        state["counted_exit"] = True
                        logger.info(
                            "⬅️  EXIT  vx=%.1f cx=%d %s ID:%d conf=%.2f area=%d",
                            vx,
                            cx,
                            det.get_label(),
                            tid,
                            det.get_confidence(),
                            bbox_area,
                        )

            state["in_zone"] = in_zone_now

            if IS_DEBUG:
                vx_debug = (history[-1] - history[-2]) if len(history) >= 2 else 0
                logger.debug(
                    "Track %d %s@%.2f area=%d in_zone=%s cx=%d vx=%.1f",
                    tid,
                    det.get_label(),
                    det.get_confidence(),
                    bbox_area,
                    in_zone_now,
                    cx,
                    vx_debug,
                )

    except Exception as exc:  # noqa: BLE001
        if IS_DEBUG:
            logger.exception("Detection error: %s", exc)

    # ------------------------------------------------------------------
    # Stale track cleanup runs EVERY FRAME, not only when detections exist
    # ------------------------------------------------------------------
    cutoff = datetime.now()
    stale = [
        t
        for t, ts in _last_seen.items()
        if (cutoff - ts).total_seconds() > TRACK_TIMEOUT_SEC
    ]
    for t in stale:
        _tracked.pop(t, None)
        _cx_history.pop(t, None)
        _last_seen.pop(t, None)

    # ------------------------------------------------------------------
    # Periodic housekeeping
    # ------------------------------------------------------------------
    now = time.monotonic()

    if now - _last_log_time >= STATS_LOG_INTERVAL_SEC:
        elapsed = now - _last_log_time
        fps = _frame_count / elapsed if elapsed > 0 else 0.0

        logger.info(
            "📊 Entries=%d Exits=%d | FPS=%.0f | Tracks=%d",
            daily_stats["entries"],
            daily_stats["exits"],
            fps,
            len(_tracked),
        )
        _last_log_time = now
        _frame_count = 0

    # Hourly Cloudflare snapshot
    if now - _last_upload_time >= UPLOAD_INTERVAL_SEC:
        _upload_queue.put(dict(daily_stats))
        _last_upload_time = now
        _last_queued_snapshot = (daily_stats["entries"], daily_stats["exits"], daily_stats["date"])
        logger.info("📤 Hourly metrics queued for Cloudflare upload")

    # Midnight rollover
    today = datetime.now().strftime("%Y-%m-%d")
    if daily_stats["date"] != today:
        _save_report()
        daily_stats.update({"date": today, "entries": 0, "exits": 0})
        _tracked.clear()
        _cx_history.clear()
        _last_seen.clear()
        _last_upload_time = now
        logger.info("🌅 New day: %s", today)

    # Watchdog: detect silent Hailo freezes
    if WATCHDOG_TIMEOUT_SEC > 0:
        if len(_tracked) == 0:
            now_mono = time.monotonic()
            if now_mono - _last_track_time > WATCHDOG_TIMEOUT_SEC:
                logger.error(
                    "❌ Watchdog timeout: no tracks for %.0f seconds, restarting",
                    WATCHDOG_TIMEOUT_SEC,
                )
                if _main_loop is not None:
                    _main_loop.quit()

    return Gst.FlowReturn.OK


# ---------------------------------------------------------------------------
# GStreamer pipeline
# ---------------------------------------------------------------------------


def _build_pipeline(rtsp_url: str) -> str:
    """Return the GStreamer pipeline launch string."""
    if SUBTYPE == 1:
        decode = (
            f"rtph264depay ! h264parse ! " f"avdec_h264 max-threads={H264_MAX_THREADS}"
        )
    else:
        decode = (
            "rtph265depay ! h265parse ! "
            "video/x-h265,stream-format=byte-stream,alignment=au ! "
            "v4l2slh265dec ! video/x-raw,format=NV12"
        )

    return " ".join(
        [
            # Source
            f'rtspsrc location="{rtsp_url}"'
            f" latency={RTSP_LATENCY_MS}"
            f" buffer-mode=auto protocols=tcp"
            f" drop-on-latency=true timeout={RTSP_TIMEOUT_US} name=src !",
            f"application/x-rtp,media=video ! {decode} !",
            f"videoconvert ! video/x-raw,format=RGB !",
            f"videoscale ! video/x-raw,format=RGB,width={IMG_W},height={IMG_H} !",
            # hailocropper requires so-path on GStreamer 1.26+
            f"hailocropper name=cropper"
            f" so-path={WHOLE_BUFFER_SO}"
            f" function-name=create_crops"
            f" use-letterbox=true resize-method=inter-area internal-offset=true",
            # Aggregator collects both branches
            "hailoaggregator name=agg",
            # Branch 0: passthrough reference frames
            "cropper. ! queue leaky=downstream max-size-buffers=2 ! agg.sink_0",
            # Branch 1: inference
            "cropper. ! queue leaky=downstream max-size-buffers=2 ! videoconvert !",
            f"hailonet hef-path={HEF_MODEL_PATH} batch-size={HAILO_BATCH_SIZE} !",
            f"hailofilter so-path={YOLO_POST_SO}" f" function-name=filter qos=false !",
            "queue leaky=downstream max-size-buffers=2 ! agg.sink_1",
            # Tracker + output
            "agg. ! queue leaky=downstream max-size-buffers=2 !",
            f"hailotracker name=tracker class-id=-1"
            f" kalman-dist-thr={KALMAN_DIST_THR}"
            f" iou-thr={IOU_THR}"
            f" init-iou-thr={INIT_IOU_THR}"
            f" keep-tracked-frames={KEEP_TRACKED_FRAMES}"
            f" keep-lost-frames={KEEP_LOST_FRAMES}"
            f" qos=false !",
            "queue leaky=downstream max-size-buffers=2 !",
            f"appsink name=sink emit-signals=true sync=false"
            f" max-buffers={APPSINK_MAX_BUFFERS}"
            f" drop={str(APPSINK_DROP).lower()}",
        ]
    )


def _on_bus_message(
    bus: Any, message: Any, loop: GLib.MainLoop, had_error: list[bool]
) -> None:
    """Handle GStreamer ERROR and EOS bus messages."""
    if message.type == Gst.MessageType.ERROR:
        err, dbg = message.parse_error()
        logger.error("GStreamer error: %s", err)
        if dbg:
            logger.debug("GStreamer debug: %s", dbg)
        had_error[0] = True
        loop.quit()
    elif message.type == Gst.MessageType.EOS:
        logger.info("End of stream")
        loop.quit()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> int:
    """Initialise pipeline, run event loop, shut down cleanly.

    Returns:
        0 on clean exit or keyboard interrupt
        1 on configuration / pipeline error
    """
    global _main_loop

    logger.info("🚗 Driveway Counter (Hailo-8 26 TOPS)")
    logger.info("📍 Tracking Zone (processing res): %s", TRACKING_ZONE.tolist())
    logger.info(
        "📋 Original %dx%d → processing %dx%d",
        ORIG_W,
        ORIG_H,
        IMG_W,
        IMG_H,
    )
    logger.info("📊 Confidence threshold: %.2f", CONF_THRESH)
    logger.info("📏 Min detection size: %d px²", MIN_BBOX_AREA)
    logger.info(
        "📈 Tracking: timeout=%ds  velocity_thr=%d px/frame",
        TRACK_TIMEOUT_SEC,
        VELOCITY_THRESHOLD,
    )

    # Preflight checks
    for label, path in [
        ("HEF model", HEF_MODEL_PATH),
        ("YOLO postprocess SO", YOLO_POST_SO),
        ("Whole-buffer cropper SO", WHOLE_BUFFER_SO),
    ]:
        if not os.path.exists(path):
            logger.error("❌ %s not found: %s", label, path)
            return 1

    # Background Cloudflare upload thread
    upload_thread = threading.Thread(
        target=_cloudflare_upload_worker,
        name="cf-uploader",
        daemon=True,
    )
    upload_thread.start()
    logger.info(
        "☁️  Cloudflare upload thread started (interval=%ds)", int(UPLOAD_INTERVAL_SEC)
    )

    # GStreamer init
    try:
        Gst.init(None)
    except Exception as exc:
        logger.error("GStreamer init failed: %s", exc)
        return 1

    rtsp_url = (
        f"rtsp://{quote(USERNAME, safe='')}:{quote(PASSWORD, safe='')}"
        f"@{IPADDRESS}:554"
        f"/cam/realmonitor?channel={CHANNEL}&subtype={SUBTYPE}"
    )

    # Pipeline retry loop with exponential backoff for RTSP failures.
    # The process stays alive so Cloudflare uploads are not triggered on every
    # reconnect attempt — only when counts actually change.
    _BACKOFF_INITIAL = 30   # seconds before first retry
    _BACKOFF_MAX = 300      # cap at 5 minutes
    _GOOD_RUN_SEC = 60      # runs longer than this reset the failure counter
    consecutive_failures = 0

    try:
        while True:
            pipeline: Gst.Pipeline | None = None
            had_error: list[bool] = [False]
            run_start = time.monotonic()

            try:
                pipeline_str = _build_pipeline(rtsp_url)
                logger.debug("Pipeline:\n%s", pipeline_str)

                pipeline = Gst.parse_launch(pipeline_str)

                sink = pipeline.get_by_name("sink")
                if sink is None:
                    logger.error("❌ appsink element 'sink' not found in pipeline")
                    return 1
                sink.connect("new-sample", _on_new_sample)

                loop = GLib.MainLoop()
                _main_loop = loop
                bus = pipeline.get_bus()
                bus.add_signal_watch()
                bus.connect(
                    "message",
                    lambda b, m, he=had_error: _on_bus_message(b, m, loop, he),
                )

                ret = pipeline.set_state(Gst.State.PLAYING)
                if ret == Gst.StateChangeReturn.FAILURE:
                    logger.error("❌ Pipeline failed to enter PLAYING state")
                    had_error[0] = True
                else:
                    logger.info("🚀 Pipeline running — press Ctrl+C to stop")
                    loop.run()

            except KeyboardInterrupt:
                raise
            except Exception as exc:
                logger.error("Fatal error: %s", exc)
                if IS_DEBUG:
                    logger.exception("Traceback:")
                had_error[0] = True
            finally:
                if pipeline is not None:
                    pipeline.set_state(Gst.State.NULL)

            if not had_error[0]:
                # Clean exit (EOS, watchdog-triggered quit, etc.)
                break

            # Pipeline error — apply exponential backoff before retrying.
            run_duration = time.monotonic() - run_start
            if run_duration >= _GOOD_RUN_SEC:
                consecutive_failures = 0
            else:
                consecutive_failures += 1

            wait = min(_BACKOFF_INITIAL * (2 ** (consecutive_failures - 1)), _BACKOFF_MAX)
            logger.warning(
                "RTSP/pipeline error (failure #%d) — retrying in %ds",
                consecutive_failures,
                wait,
            )
            try:
                time.sleep(wait)
            except (KeyboardInterrupt, SystemExit):
                break

    except KeyboardInterrupt:
        logger.info("Keyboard interrupt received")
    finally:
        _save_report()
        _upload_queue.put(None)  # sentinel — tells worker to exit
        upload_thread.join(timeout=15)
        logger.info("✅ Shutdown complete")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
