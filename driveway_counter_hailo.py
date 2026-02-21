#!/usr/bin/env python3
"""Hailo Driveway Counter - Single zone motion detection.

Tracks objects entering/exiting a single zone using Hailo inference and GStreamer.
Counts are reported per day and saved to JSON reports.
"""

from __future__ import annotations

import ast
import json
import logging
import os
from collections import deque
from datetime import datetime
from typing import Any
from urllib.parse import quote

import cv2
import gi
import numpy as np
from dotenv import load_dotenv

gi.require_version("Gst", "1.0")
from gi.repository import Gst, GLib  # noqa: E402

# Load environment variables
load_dotenv()

# ============================================================================
# Logging Configuration
# ============================================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger: logging.Logger = logging.getLogger(__name__)

# ============================================================================
# Camera Configuration
# ============================================================================

USERNAME: str = os.getenv("USERNAME", "")
PASSWORD: str = os.getenv("PASSWORD", "")
IPADDRESS: str = os.getenv("IPADDRESS", "")
CHANNEL: int = int(os.getenv("CHANNEL", "1"))
SUBTYPE: int = int(os.getenv("SUBTYPE", "0"))

ORIG_W: int = int(os.getenv("ORIG_W", "704"))
ORIG_H: int = int(os.getenv("ORIG_H", "480"))
IMG_W: int = int(os.getenv("IMG_W", "704"))
IMG_H: int = int(os.getenv("IMG_H", "480"))

# Detection Settings
CONF_THRESH: float = float(os.getenv("CONF_THRESH", "0.3"))

# ============================================================================
# Zone Configuration
# ============================================================================


def _parse_zone(env_var: str, default: str) -> np.ndarray:
    """Safely parse zone coordinates from environment variable.

    Args:
        env_var: Environment variable name
        default: Default zone string if env var is invalid

    Returns:
        np.ndarray: Zone coordinates as int32 array
    """
    val = os.getenv(env_var, default)
    try:
        pts = ast.literal_eval(val)
    except (ValueError, SyntaxError):
        logger.warning("Invalid %s, using default", env_var)
        pts = ast.literal_eval(default)
    return np.array(pts, dtype=np.int32)


# Single tracking zone (covers driveway centerline)
TRACKING_ZONE: np.ndarray = _parse_zone(
    "TRACKING_ZONE", "[[250,10],[490,10],[490,460],[250,460]]"
)

# Scale zone to processing resolution
scale_x: float = IMG_W / ORIG_W
scale_y: float = IMG_H / ORIG_H
TRACKING_ZONE = (TRACKING_ZONE * [scale_x, scale_y]).astype(np.int32)

# ============================================================================
# Application Settings
# ============================================================================

# Report Generation
REPORT_DIR: str = os.getenv("REPORT_DIR", "./reports")
os.makedirs(REPORT_DIR, exist_ok=True)

# Debug and Tracking
ISDEBUG: bool = os.getenv("ISDEBUG", "False").lower() == "true"
TRACK_TIMEOUT: int = 60
STATS_LOG_INTERVAL: float = 10.0

# Model Configuration
HEF_MODEL_PATH: str = "./models/yolov8m.hef"

if ISDEBUG:
    logger.setLevel(logging.DEBUG)

# ============================================================================
# Global State
# ============================================================================

# Tracking state
tracked_zones: dict[int, dict[str, Any]] = {}  # "in" or "out"
centroid_history: dict[int, deque[tuple[int, int]]] = {}  # New for velocity
track_last_seen: dict[int, datetime] = {}

# Daily statistics
dailystats: dict[str, Any] = {
    "date": datetime.now().strftime("%Y-%m-%d"),
    "entries": 0,
    "exits": 0,
}

# Frame processing metrics
frame_count: int = 0
last_log: float = datetime.now().timestamp()

# Pre-computed zone mask
tracking_mask: np.ndarray = cv2.fillPoly(
    np.zeros((IMG_H, IMG_W), np.uint8), [TRACKING_ZONE], 255
)

# ============================================================================
# Utility Functions
# ============================================================================


def save_report() -> None:
    """Save daily statistics to JSON file."""
    path = f"{REPORT_DIR}/driveway_{dailystats['date']}.json"
    with open(path, "w") as f:
        json.dump(dailystats, f, indent=2)
    logger.info("Report saved: %s", path)

# ============================================================================
# GStreamer Callback Functions
# ============================================================================


def on_new_sample(sink: Any) -> Gst.FlowReturn:
    """Process detections: count zone crossings by edge detection (bidirectional)."""
    global dailystats, frame_count, last_log, tracked_zones, track_last_seen, centroid_history

    sample = sink.emit("pull-sample")
    if sample is None:
        return Gst.FlowReturn.OK

    frame_count += 1
    buffer = sample.get_buffer()
    try:
        import hailo
        roi = hailo.get_roi_from_buffer(buffer)
        detections = roi.get_objects_typed(hailo.HAILO_DETECTION)

        for detection in detections:
            label: str = detection.get_label()
            confidence: float = detection.get_confidence()
            if confidence < CONF_THRESH:
                continue

            unique_ids = detection.get_objects_typed(hailo.HAILO_UNIQUE_ID)
            if not unique_ids:
                if ISDEBUG:
                    logger.debug("No unique_id for %s, skipping", label)
                continue

            track_id: int = unique_ids[0].get_id()
            bbox = detection.get_bbox()
            cx: int = int((bbox.xmin() + bbox.xmax()) / 2 * IMG_W)
            cy: int = int((bbox.ymin() + bbox.ymax()) / 2 * IMG_H)

            if not (0 <= cx < IMG_W and 0 <= cy < IMG_H):
                continue

            track_last_seen[track_id] = datetime.now()

            # Ensure persistent state
            if track_id not in tracked_zones:
                tracked_zones[track_id] = {'zone': None, 'entered': False, 'exited': False}
            track_state = tracked_zones[track_id]

            # Centroid history (cx only for horizontal velocity)
            history = centroid_history.get(track_id, deque(maxlen=5))
            history.append(cx)
            centroid_history[track_id] = history

            if len(history) >= 2:
                prev_cx = history[-2]
                vx = cx - prev_cx

                # ENTRY: crossed left zone edge (250) rightward
                if prev_cx < TRACKING_ZONE[0][0] <= cx and vx > 3:
                    if not track_state['entered']:
                        dailystats["entries"] += 1
                        track_state['entered'] = True
                        logger.info(
                            "➡️ ENTER ZONE (vx=%.1f cx=%d): %s ID:%d conf=%.2f",
                            vx, cx, label, track_id, confidence
                        )

                # EXIT: crossed right zone edge (490) leftward
                elif prev_cx > TRACKING_ZONE[1][0] >= cx and vx < -3:
                    if not track_state['exited']:
                        dailystats["exits"] += 1
                        track_state['exited'] = True
                        logger.info(
                            "➡️ EXIT ZONE (vx=%.1f cx=%d):  %s ID:%d conf=%.2f",
                            vx, cx, label, track_id, confidence
                        )

            track_state['zone'] = 'in' if (TRACKING_ZONE[0][0] <= cx <= TRACKING_ZONE[1][0]) else 'out'
            tracked_zones[track_id] = track_state

            if ISDEBUG:
                logger.debug(
                    "Track %d %s@%.2f zone=%s cx=%d vx=%.1f",
                    track_id, label, confidence, track_state['zone'],
                    cx, (history[-1] - history[-2]) if len(history) >= 2 else 0
                )

    except Exception as e:
        if ISDEBUG:
            logger.exception("Detection error: %s", e)

    # Stats + stale cleanup
    now: float = datetime.now().timestamp()
    if now - last_log >= STATS_LOG_INTERVAL:
        fps: float = frame_count / (now - last_log) if now != last_log else 0
        now_dt: datetime = datetime.now()
        stale_ids: list[int] = [
            tid for tid, last_seen in track_last_seen.items()
            if (now_dt - last_seen).total_seconds() > TRACK_TIMEOUT
        ]
        for tid in stale_ids:
            tracked_zones.pop(tid, None)
            track_last_seen.pop(tid, None)
            centroid_history.pop(tid, None)

        logger.info(
            "📊 Entries=%d Exits=%d | FPS=%.0f | Tracks=%d",
            dailystats["entries"], dailystats["exits"], fps, len(tracked_zones)
        )
        last_log = now
        frame_count = 0

    # Date rollover
    today: str = datetime.now().strftime("%Y-%m-%d")
    if dailystats["date"] != today:
        save_report()
        dailystats["date"] = today
        dailystats["entries"] = 0
        dailystats["exits"] = 0
        tracked_zones.clear()
        track_last_seen.clear()
        centroid_history.clear()
        logger.info("New day started: %s", today)

    return Gst.FlowReturn.OK


# ============================================================================
# Pipeline Construction
# ============================================================================


def build_pipeline_string(
    rtsp_url: str, hef_path: str, width: int, height: int, subtype: int
) -> str:
    """Build GStreamer pipeline string for Hailo processing.

    Args:
        rtsp_url: RTSP stream URL
        hef_path: Path to Hailo model file
        width: Target frame width
        height: Target frame height
        subtype: RTSP subtype (0=H.265, 1=H.264)

    Returns:
        str: GStreamer pipeline string
    """
    # H.264 for substream (subtype=1), H.265 for main stream (subtype=0)
    if subtype == 1:
        decoder = "rtph264depay ! h264parse ! avdec_h264 max-threads=2"
    else:
        decoder = "rtph265depay ! h265parse ! avdec_h265 max-threads=2"

    return f"""
    rtspsrc location="{rtsp_url}" latency=2000 buffer-mode=auto protocols=tcp \
drop-on-latency=true timeout=5000000 name=src \
src. ! application/x-rtp,media=video ! {decoder} ! \
videoconvert ! video/x-raw,format=RGB ! \
videoscale ! video/x-raw,width={width},height={height} ! \
hailocropper name=cropper \
so-path=/usr/lib/aarch64-linux-gnu/hailo/tappas/post_processes/cropping_algorithms/libwhole_buffer.so \
function-name=create_crops use-letterbox=true resize-method=inter-area internal-offset=true
    hailoaggregator name=agg
    cropper. ! queue ! agg.sink_0
    cropper. ! queue ! videoconvert !
    hailonet hef-path={hef_path} batch-size=1 !
    hailofilter \
so-path=/usr/local/hailo/resources/so/libyolo_hailortpp_postprocess.so \
config-path=/usr/local/hailo/resources/barcode_labels/coco_80.json \
function-name=filter qos=false !
    queue ! agg.sink_1
    agg. ! queue !
    hailotracker name=tracker class-id=-1 kalman-dist-thr=1 iou-thr=0.65 \
init-iou-thr=0.7 keep-tracked-frames=10 keep-lost-frames=2 qos=false !
    queue !
    appsink name=sink emit-signals=true sync=false max-buffers=2 drop=true
    """


def on_bus_message(bus: Any, message: Any, loop: Any) -> None:
    """Handle GStreamer bus messages.

    Args:
        bus: GStreamer bus element
        message: GStreamer message
        loop: GLib main loop
    """
    msg_type: Gst.MessageType = message.type
    if msg_type == Gst.MessageType.ERROR:
        err, debug = message.parse_error()
        logger.error("GStreamer error: %s", err)
        loop.quit()
    elif msg_type == Gst.MessageType.EOS:
        logger.info("End of stream")
        loop.quit()


# ============================================================================
# Main Application
# ============================================================================


def main() -> int:
    """Start the driveway counter pipeline.

    Returns:
        int: Exit code (0 for success, 1 for error)
    """
    logger.info("🚗 Driveway Counter (Hailo 26 TOPS)")
    logger.info("📍 Zone: %s (scaled to %dx%d)", TRACKING_ZONE.tolist(), IMG_W, IMG_H)

    # Initialize GStreamer
    Gst.init(None)

    # Build RTSP URL with URL-encoded credentials
    username_enc: str = quote(USERNAME, safe="")
    password_enc: str = quote(PASSWORD, safe="")
    rtsp_url: str = (
        f"rtsp://{username_enc}:{password_enc}@{IPADDRESS}:554"
        f"/cam/realmonitor?channel={CHANNEL}&subtype={SUBTYPE}"
    )

    logger.info("✅ Model: %s", HEF_MODEL_PATH)
    logger.info("📈 CONF_THRESH=%.2f (low for %dx%d)", CONF_THRESH, IMG_W, IMG_H)


    # Validate model file exists
    if not os.path.exists(HEF_MODEL_PATH):
        logger.error("HEF model not found: %s", HEF_MODEL_PATH)
        return 1

    # Build and create pipeline
    pipeline_str: str = build_pipeline_string(rtsp_url, HEF_MODEL_PATH, IMG_W, IMG_H, SUBTYPE)

    try:
        pipeline: Gst.Pipeline = Gst.parse_launch(pipeline_str)
    except Exception as e:
        logger.exception("Pipeline creation error: %s", e)
        return 1

    # Connect callback to appsink
    sink: Any = pipeline.get_by_name("sink")
    sink.connect("new-sample", on_new_sample)

    # Set up bus message handling
    bus: Gst.Bus = pipeline.get_bus()
    bus.add_signal_watch()

    # Create main loop and connect handler
    loop: GLib.MainLoop = GLib.MainLoop()

    def bus_message_handler(bus: Any, message: Any) -> None:
        """Wrapper for on_bus_message to pass loop reference."""
        on_bus_message(bus, message, loop)

    bus.connect("message", bus_message_handler)

    # Start pipeline
    logger.info("🚀 Starting pipeline...")
    pipeline.set_state(Gst.State.PLAYING)

    # Run main loop
    try:
        loop.run()
    except KeyboardInterrupt:
        logger.info("Stopping (KeyboardInterrupt)")
        return 0
    finally:
        pipeline.set_state(Gst.State.NULL)
        save_report()
        logger.info("Shutdown complete")

    return 0


# ============================================================================
# Application Entry Point
# ============================================================================


if __name__ == "__main__":
    exit(main())
