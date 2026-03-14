#!/usr/bin/env python3
"""Hailo Driveway Counter - Single zone traffic counting system.

This application tracks vehicles entering/exiting a single driveway zone using
Hailo AI accelerator for inference and GStreamer for video processing. Detections
are counted directionally (entries vs exits) and daily statistics are reported.

Features:
    - Real-time object detection using Hailo YOLOv8m model
    - Bidirectional zone crossing detection with velocity analysis
    - Frame-by-frame tracking with stale track cleanup
    - Daily statistics logging and JSON report generation
    - Configurable confidence thresholds and zone boundaries

Dependencies:
    - cv2 (OpenCV): Video frame processing
    - gi (PyGObject): GStreamer bindings for pipeline construction
    - numpy: Numerical operations and zone coordinate handling
    - python-dotenv: Environment variable configuration loading
"""

from __future__ import annotations

import ast
import json
import logging
import os
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
from gi.repository import Gst, GLib  # noqa: E402

try:
    import hailo
except ImportError as e:
    raise SystemExit(
        f"Hailo Python module not found. "
        f"Ensure venv uses --system-site-packages. Error: {e}"
    )

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
# Camera Configuration (from .env)
# ============================================================================

# RTSP Camera credentials and connection
USERNAME: str = os.getenv("USERNAME", "")
PASSWORD: str = os.getenv("PASSWORD", "")
IPADDRESS: str = os.getenv("IPADDRESS", "")
CHANNEL: int = int(os.getenv("CHANNEL", "1"))
SUBTYPE: int = int(os.getenv("SUBTYPE", "0"))  # 0=H.265, 1=H.264

# Camera resolution settings
ORIG_W: int = int(os.getenv("ORIG_W", "704"))  # Original camera width
ORIG_H: int = int(os.getenv("ORIG_H", "480"))  # Original camera height
IMG_W: int = int(os.getenv("IMG_W", "704"))  # Processing frame width
IMG_H: int = int(os.getenv("IMG_H", "480"))  # Processing frame height

# Detection Settings
CONF_THRESH: float = float(os.getenv("CONF_THRESH", "0.45"))

# ============================================================================
# Model and Pipeline Configuration
# ============================================================================

# Hailo model file path
HEF_MODEL_PATH: str = "./models/yolov8m.hef"

# GStreamer RTSP source parameters
RTSP_LATENCY_MS: int = 2000
RTSP_TIMEOUT_US: int = 5000000

# Video processing thread and buffer settings
VIDEO_MAX_THREADS: int = 2
APPSINK_MAX_BUFFERS: int = 2
APPSINK_DROP_MODE: bool = True

# Hailo inference parameters
HAILO_BATCH_SIZE: int = 1

# Hailo tracker configuration parameters
KALMAN_DIST_THR: float = 1.0
IOU_THR: float = 0.65
INIT_IOU_THR: float = 0.7
KEEP_TRACKED_FRAMES: int = 10
KEEP_LOST_FRAMES: int = 2

# Velocity detection and tracking
VELOCITY_THRESHOLD: int = 2  # Horizontal pixel displacement threshold
CENTROID_HISTORY_SIZE: int = 5  # Number of frames to track for velocity
TRACK_TIMEOUT_SEC: int = 60  # Remove tracks not seen for this duration

# Statistics reporting
STATS_LOG_INTERVAL_SEC: float = 10.0

# Debug mode
IS_DEBUG: bool = os.getenv("ISDEBUG", "False").lower() == "true"
if IS_DEBUG:
    logger.setLevel(logging.DEBUG)

# ============================================================================
# Zone Configuration
# ============================================================================


def _parse_zone(env_var: str, default: str) -> np.ndarray:
    """Safely parse zone coordinates from environment variable.

    Uses ast.literal_eval for safe parsing of coordinate strings, falling back
    to a default zone if parsing fails. This prevents arbitrary code execution
    from untrusted configuration sources.

    Args:
        env_var: Environment variable name to retrieve zone coordinates from
        default: Default zone coordinate string if env var is invalid or missing

    Returns:
        np.ndarray: Zone vertices as Nx2 int32 array with shape (n_points, 2)

    Example:
        >>> zone = _parse_zone("ZONE", "[[100,50],[200,50],[200,400],[100,400]]")
        >>> zone.shape
        (4, 2)
    """
    val = os.getenv(env_var, default)
    try:
        pts = ast.literal_eval(val)
    except (ValueError, SyntaxError) as e:
        logger.warning("Invalid %s format: %s, using default", env_var, e)
        pts = ast.literal_eval(default)
    return np.array(pts, dtype=np.int32)


# Single tracking zone (covers driveway centerline) in ORIGINAL resolution
TRACKING_ZONE_ORIG: np.ndarray = _parse_zone(
    "TRACKING_ZONE",
    "[[100,10],[600,10],[600,460],[100,460]]",
)

# Scale zone from original to processing resolution
_scale_x: float = IMG_W / ORIG_W
_scale_y: float = IMG_H / ORIG_H
TRACKING_ZONE: np.ndarray = (
    TRACKING_ZONE_ORIG * np.array([_scale_x, _scale_y])
).astype(np.int32)

# Pre-computed zone mask for efficient spatial queries
tracking_mask: np.ndarray = cv2.fillPoly(
    np.zeros((IMG_H, IMG_W), dtype=np.uint8),
    [TRACKING_ZONE],
    255,
)

# ============================================================================
# Application Settings
# ============================================================================

# Report output directory
REPORT_DIR: str = os.getenv("REPORT_DIR", "./reports")
os.makedirs(REPORT_DIR, exist_ok=True)

# ============================================================================
# Global State
# ============================================================================

# Object tracking state
# Maps track_id -> {"zone": "in"|"out", "entered": bool, "exited": bool}
tracked_zones: dict[int, dict[str, Any]] = {}

# Centroid history for velocity calculation
# Maps track_id -> deque of x-coordinates (newest at right)
centroid_history: dict[int, deque[tuple[int, int]]] = {}

# Track visibility timestamps for stale track cleanup
# Maps track_id -> datetime of last detection
track_last_seen: dict[int, datetime] = {}

# Daily statistics accumulator
daily_stats: dict[str, Any] = {
    "date": datetime.now().strftime("%Y-%m-%d"),
    "entries": 0,
    "exits": 0,
}

# Frame processing metrics
frame_count: int = 0
last_log: float = time.monotonic()

# ============================================================================
# Utility Functions
# ============================================================================


def save_report() -> None:
    """Save daily statistics to JSON report file.

    Writes the current daily_stats dictionary to a JSON file with a filename
    based on the current date. Reports are saved to REPORT_DIR directory.

    File naming: driveway_YYYY-MM-DD.json

    Returns:
        None

    Raises:
        OSError: If file write fails
    """
    path = f"{REPORT_DIR}/driveway_{daily_stats['date']}.json"
    try:
        with open(path, "w") as f:
            json.dump(daily_stats, f, indent=2)
        logger.info("Report saved: %s", path)

        # NEW: Sync to Cloudflare Workers KV
        worker_url = os.getenv("WORKER_URL")
        cloudflare_token = os.getenv("CLOUDFLARE_TOKEN")
        if not worker_url or not cloudflare_token:
            logger.warning(
                "WORKER_URL or CLOUDFLARE_TOKEN not set, skipping Cloudflare sync"
            )
            return
        payload = {"key": f"driveway:{daily_stats['date']}", "value": daily_stats}
        headers = {
            "Authorization": f"Bearer {cloudflare_token}",
            "Content-Type": "application/json",
        }
        try:
            response = requests.post(
                worker_url, json=payload, headers=headers, timeout=10
            )
            response.raise_for_status()
            logger.info("Metrics synced to Cloudflare: %s", daily_stats["date"])
        except requests.RequestException as e:
            logger.error("Cloudflare sync failed: %s", e)

    except OSError as e:
        logger.error("Failed to save report to %s: %s", path, e)


# ============================================================================
# ============================================================================
# GStreamer Callback Functions
# ============================================================================


def process_frame_detections(sink: Any) -> Gst.FlowReturn:
    """Process Hailo detections and count bidirectional zone crossings.

    Processes each frame's detections from Hailo, tracks object centroids,
    and detects zone entries/exits by analyzing velocity across the zone
    boundaries. Supports bidirectional counting (left-to-right and right-to-left).

    Entry/Exit Detection Logic:
        - ENTRY: Object crosses zone boundary moving toward zone center
        - EXIT: Object crosses zone boundary moving away from zone center
        - Velocity threshold (VELOCITY_THRESHOLD pixels) prevents false counts
        - Each object counted only once per crossing (state machine)

    Stale Track Cleanup:
        - Removes tracked objects not detected for > TRACK_TIMEOUT_SEC
        - Runs at STATS_LOG_INTERVAL_SEC interval
        - Prevents memory accumulation over long runs

    Args:
        sink: GStreamer appsink element emitting sample signals

    Returns:
        Gst.FlowReturn: OK on successful processing, OK on error to continue
    """
    global daily_stats, frame_count, last_log

    sample = sink.emit("pull-sample")
    if sample is None:
        return Gst.FlowReturn.OK

    frame_count += 1
    buffer = sample.get_buffer()

    try:
        roi = hailo.get_roi_from_buffer(buffer)
        detections = roi.get_objects_typed(hailo.HAILO_DETECTION)

        for detection in detections:
            label: str = detection.get_label()
            confidence: float = detection.get_confidence()

            # Skip low-confidence detections
            if confidence < CONF_THRESH:
                continue

            # Extract unique tracking ID
            unique_ids = detection.get_objects_typed(hailo.HAILO_UNIQUE_ID)
            if not unique_ids:
                if IS_DEBUG:
                    logger.debug("No unique_id for %s, skipping", label)
                continue

            track_id: int = unique_ids[0].get_id()

            # Calculate centroid position from bounding box
            bbox = detection.get_bbox()
            cx: int = int((bbox.xmin() + bbox.xmax()) / 2 * IMG_W)
            cy: int = int((bbox.ymin() + bbox.ymax()) / 2 * IMG_H)

            # Validate centroid is within frame
            if not (0 <= cx < IMG_W and 0 <= cy < IMG_H):
                continue

            # Update last seen timestamp
            track_last_seen[track_id] = datetime.now()

            # Initialize tracking state for new objects
            if track_id not in tracked_zones:
                tracked_zones[track_id] = {
                    "zone": None,
                    "entered": False,
                    "exited": False,
                }
            track_state = tracked_zones[track_id]

            # Build centroid history for velocity calculation
            history = centroid_history.get(
                track_id,
                deque(maxlen=CENTROID_HISTORY_SIZE),
            )
            history.append(cx)
            centroid_history[track_id] = history

            # Detect crossing using velocity (x displacement between frames)
            if len(history) >= 2:
                prev_cx = history[-2]
                velocity_x = cx - prev_cx

                # Zone lateral boundaries
                zone_left = TRACKING_ZONE[0][0]
                zone_right = TRACKING_ZONE[1][0]

                # Position relative to zone
                in_zone_now = zone_left <= cx <= zone_right
                in_zone_prev = zone_left <= prev_cx <= zone_right

                # ENTRY: Crossed left boundary rightward (left→right entry)
                if not in_zone_prev and in_zone_now and velocity_x > VELOCITY_THRESHOLD:
                    if not track_state["entered"]:
                        daily_stats["entries"] += 1
                        track_state["entered"] = True
                        logger.info(
                            "➡️ ENTRY (vx=%.1f cx=%d): %s ID:%d conf=%.2f",
                            velocity_x,
                            cx,
                            label,
                            track_id,
                            confidence,
                        )

                # EXIT RIGHT: Crossed right boundary rightward (left→right exit)
                elif (
                    in_zone_prev and not in_zone_now and velocity_x > VELOCITY_THRESHOLD
                ):
                    if not track_state["exited"]:
                        daily_stats["exits"] += 1
                        track_state["exited"] = True
                        logger.info(
                            "➡️ EXIT (vx=%.1f cx=%d): %s ID:%d conf=%.2f",
                            velocity_x,
                            cx,
                            label,
                            track_id,
                            confidence,
                        )

                # EXIT LEFT: Crossed left boundary leftward (right→left exit)
                elif (
                    in_zone_prev
                    and not in_zone_now
                    and velocity_x < -VELOCITY_THRESHOLD
                ):
                    if not track_state["exited"]:
                        daily_stats["exits"] += 1
                        track_state["exited"] = True
                        logger.info(
                            "➡️ EXIT (vx=%.1f cx=%d): %s ID:%d conf=%.2f",
                            velocity_x,
                            cx,
                            label,
                            track_id,
                            confidence,
                        )

                # ENTRY LEFT: Crossed right boundary leftward (right→left entry)
                elif (
                    not in_zone_prev
                    and in_zone_now
                    and velocity_x < -VELOCITY_THRESHOLD
                ):
                    if not track_state["entered"]:
                        daily_stats["entries"] += 1
                        track_state["entered"] = True
                        logger.info(
                            "➡️ ENTRY (vx=%.1f cx=%d): %s ID:%d conf=%.2f",
                            velocity_x,
                            cx,
                            label,
                            track_id,
                            confidence,
                        )

            # Update zone membership
            track_state["zone"] = "in" if in_zone_now else "out"
            tracked_zones[track_id] = track_state

            # Debug tracking details
            if IS_DEBUG:
                velocity_debug = (history[-1] - history[-2]) if len(history) >= 2 else 0
                logger.debug(
                    "Track %d %s@%.2f zone=%s cx=%d vx=%.1f",
                    track_id,
                    label,
                    confidence,
                    track_state["zone"],
                    cx,
                    velocity_debug,
                )

    except Exception as e:
        if IS_DEBUG:
            logger.exception("Detection processing error: %s", e)
        # Continue processing even on error

    # Periodic statistics reporting and stale track cleanup
    now: float = datetime.now().timestamp()
    if now - last_log >= STATS_LOG_INTERVAL_SEC:
        # Calculate frame rate
        time_delta = now - last_log
        fps: float = frame_count / time_delta if time_delta > 0 else 0

        # Find and remove stale tracks
        now_dt: datetime = datetime.now()
        stale_ids: list[int] = [
            tid
            for tid, last_seen in track_last_seen.items()
            if (now_dt - last_seen).total_seconds() > TRACK_TIMEOUT_SEC
        ]
        for tid in stale_ids:
            tracked_zones.pop(tid, None)
            track_last_seen.pop(tid, None)
            centroid_history.pop(tid, None)

        # Log statistics
        logger.info(
            "📊 Entries=%d Exits=%d | FPS=%.0f | Tracks=%d",
            daily_stats["entries"],
            daily_stats["exits"],
            fps,
            len(tracked_zones),
        )
        last_log = now
        frame_count = 0

    # Check for date rollover (midnight)
    today: str = datetime.now().strftime("%Y-%m-%d")
    if daily_stats["date"] != today:
        save_report()
        daily_stats["date"] = today
        daily_stats["entries"] = 0
        daily_stats["exits"] = 0
        tracked_zones.clear()
        track_last_seen.clear()
        centroid_history.clear()
        logger.info("🌅 New day started: %s", today)

    return Gst.FlowReturn.OK


# ============================================================================
# ============================================================================
# Pipeline Construction
# ============================================================================


def build_pipeline_string(
    rtsp_url: str,
    hef_path: str,
    width: int,
    height: int,
    subtype: int,
) -> str:
    """Build GStreamer pipeline string for Hailo YOLOv8 inference.

    Constructs a complex GStreamer pipeline for:
        1. RTSP video capture from IP camera
        2. Decoding (H.264 or H.265 based on subtype)
        3. Scaling to target resolution
        4. Hailo preprocessing (cropping, normalization)
        5. YOLOv8 inference on Hailo accelerator
        6. Post-processing (YOLO COCO format)
        7. Object tracking with Kalman filter
        8. Frame-by-frame delivery to Python callback

    Args:
        rtsp_url: RTSP stream URL from camera
        hef_path: Path to Hailo Executable Format (.hef) model file
        width: Target frame width for inference
        height: Target frame height for inference
        subtype: RTSP stream subtype (0=H.265 main, 1=H.264 substream)

    Returns:
        str: Complete GStreamer pipeline launch string

    Notes:
        - H.264 used for substream (1), H.265 for main stream (0)
        - Codec-specific decoders and depayloaders selected based on subtype
        - Video max threads limited to VIDEO_MAX_THREADS for efficiency
        - Hailo tracker uses Kalman distance threshold and IOU threshold
    """
    # Select appropriate decoder based on stream subtype
    if subtype == 1:
        # H.264 substream decoding
        decoder = (
            f"rtph264depay ! h264parse ! avdec_h264 max-threads={VIDEO_MAX_THREADS}"
        )
    else:
        # H.265 main stream decoding
        decoder = (
            f"rtph265depay ! h265parse ! avdec_h265 max-threads={VIDEO_MAX_THREADS}"
        )

    # Construct complete GStreamer pipeline string
    # Multi-branch structure: source→cropper splits into reference and inference paths,
    # aggregated, then tracked and output to appsink
    #
    # Pipeline segments:
    # 1. Source and preprocessing (RTSP → decode → scale → cropper)
    # 2. Aggregator element (combines reference and detection paths)
    # 3. Reference branch (cropper pad 0 → agg.sink_0)
    # 4. Detection branch (cropper pad 1 → inference → agg.sink_1)
    # 5. Output branch (agg → tracker → appsink)
    parts = [
        (
            f'rtspsrc location="{rtsp_url}" '
            f"latency={RTSP_LATENCY_MS} buffer-mode=auto protocols=tcp "
            f"drop-on-latency=true timeout={RTSP_TIMEOUT_US} name=src ! "
            f"application/x-rtp,media=video ! {decoder} ! "
            f"videoconvert ! video/x-raw,format=RGB ! "
            f"videoscale ! video/x-raw,width={width},height={height} ! "
            f"hailocropper name=cropper "
            f"so-path=/usr/lib/aarch64-linux-gnu/hailo/tappas/post_processes/cropping_algorithms/libwhole_buffer.so "
            f"function-name=create_crops use-letterbox=true resize-method=inter-area internal-offset=true"
        ),
        "hailoaggregator name=agg",
        "cropper. ! queue ! agg.sink_0",
        (
            "cropper. ! queue ! videoconvert ! "
            f"hailonet hef-path={hef_path} batch-size={HAILO_BATCH_SIZE} ! "
            f"hailofilter "
            f"so-path=/usr/local/hailo/resources/so/libyolo_hailortpp_postprocess.so "
            f"config-path=/usr/local/hailo/resources/barcode_labels/coco_80.json "
            f"function-name=filter qos=false ! queue ! agg.sink_1"
        ),
        (
            "agg. ! queue ! "
            f"hailotracker name=tracker class-id=-1 "
            f"kalman-dist-thr={KALMAN_DIST_THR} iou-thr={IOU_THR} "
            f"init-iou-thr={INIT_IOU_THR} "
            f"keep-tracked-frames={KEEP_TRACKED_FRAMES} keep-lost-frames={KEEP_LOST_FRAMES} "
            f"qos=false ! queue ! "
            f"appsink name=sink emit-signals=true sync=false "
            f"max-buffers={APPSINK_MAX_BUFFERS} drop={str(APPSINK_DROP_MODE).lower()}"
        ),
    ]
    pipeline = " ".join(parts)
    return pipeline


def on_bus_message(bus: Any, message: Any, loop: Any) -> None:
    """Handle GStreamer bus messages for error and end-of-stream events.

    Monitors the GStreamer pipeline bus for critical messages (ERROR, EOS)
    and terminates the main event loop gracefully on error or stream end.

    Args:
        bus: GStreamer Bus object from the pipeline
        message: GStreamer Message object from the bus
        loop: GLib.MainLoop instance to control application lifecycle

    Returns:
        None
    """
    msg_type: Gst.MessageType = message.type

    if msg_type == Gst.MessageType.ERROR:
        err, debug = message.parse_error()
        logger.error(f"GStreamer error: {err}")
        if debug:
            logger.debug(f"Debug info: {debug}")
        loop.quit()

    elif msg_type == Gst.MessageType.EOS:
        logger.info("End of stream reached")
        loop.quit()


# ============================================================================
# Main Application
# ============================================================================


def main() -> int:
    """Start driveway counter with Hailo inference and GStreamer pipeline.

    This function:
        1. Validates configuration and model file existence
        2. Initializes GStreamer and constructs the inference pipeline
        3. Connects detection callbacks to process Hailo inference results
        4. Runs the main event loop for continuous frame processing
        5. Handles graceful shutdown with report saving

    The application will:
        - Connect to RTSP camera stream using credentials from environment
        - Process frames through YOLOv8 inference on Hailo accelerator
        - Track objects across frames and detect zone boundary crossings
        - Log entry/exit events with detection confidence
        - Report statistics every STATS_LOG_INTERVAL_SEC seconds
        - Save daily JSON report and roll over statistics at midnight

    Returns:
        int: Exit code
            0 = Successful completion or keyboard interrupt
            1 = Configuration error or pipeline initialization failure

    Exit Codes:
        0: Normal exit (CTRL+C) or successful run
        1: Model file not found or pipeline creation failed
    """
    logger.info("🚗 Driveway Counter (Hailo 26 TOPS)")
    logger.info("📍 Tracking Zone: %s", TRACKING_ZONE.tolist())
    logger.info(
        "📋 Original resolution: %dx%d | Processing resolution: %dx%d",
        ORIG_W,
        ORIG_H,
        IMG_W,
        IMG_H,
    )

    # Initialize GStreamer library
    try:
        Gst.init(None)
    except Exception as e:
        logger.error("Failed to initialize GStreamer: %s", e)
        return 1

    # Build RTSP URL with URL-encoded credentials (security: no plaintext in logs)
    username_enc: str = quote(USERNAME, safe="")
    password_enc: str = quote(PASSWORD, safe="")
    rtsp_url: str = (
        f"rtsp://{username_enc}:{password_enc}@{IPADDRESS}:554"
        f"/cam/realmonitor?channel={CHANNEL}&subtype={SUBTYPE}"
    )

    # Log configuration
    logger.info("✅ Model: %s", HEF_MODEL_PATH)
    logger.info("📊 Confidence threshold: %.2f", CONF_THRESH)
    logger.info(
        "📈 Tracking: timeout=%ds, velocity_thr=%d px",
        TRACK_TIMEOUT_SEC,
        VELOCITY_THRESHOLD,
    )

    # Validate model file exists before pipeline creation
    if not os.path.exists(HEF_MODEL_PATH):
        logger.error("❌ HEF model not found: %s", HEF_MODEL_PATH)
        return 1

    # Build GStreamer pipeline from configuration
    pipeline: Gst.Pipeline | None = None
    try:
        pipeline_str: str = build_pipeline_string(
            rtsp_url,
            HEF_MODEL_PATH,
            IMG_W,
            IMG_H,
            SUBTYPE,
        )
        pipeline: Gst.Pipeline = Gst.parse_launch(pipeline_str)
        logger.debug("Pipeline created successfully")
    except Exception as e:
        logger.error("❌ Pipeline creation failed: %s", e)
        if IS_DEBUG:
            logger.exception("Full traceback:")
        return 1

    # Connect Python callback to GStreamer appsink element
    try:
        sink: Any = pipeline.get_by_name("sink")
        if sink is None:
            logger.error("❌ Could not find 'sink' element in pipeline")
            return 1
        sink.connect("new-sample", process_frame_detections)
        logger.debug("Connected sample callback to appsink")
    except Exception as e:
        logger.error("❌ Failed to connect callback: %s", e)
        return 1

    # Set up GStreamer bus for error/EOS handling
    bus: Gst.Bus = pipeline.get_bus()
    bus.add_signal_watch()

    # Create main event loop
    loop: GLib.MainLoop = GLib.MainLoop()

    # Define bus message handler with loop reference
    def bus_message_handler(bus: Any, message: Any) -> None:
        """Wrapper to pass loop instance to on_bus_message."""
        on_bus_message(bus, message, loop)

    bus.connect("message", bus_message_handler)

    # Start pipeline (transition to PLAYING state)
    logger.info("🚀 Starting GStreamer pipeline...")
    try:
        ret = pipeline.set_state(Gst.State.PLAYING)
        if ret == Gst.StateChangeReturn.FAILURE:
            logger.error("❌ Failed to start pipeline")
            return 1
        logger.info("✅ Pipeline running, processing frames...")
    except Exception as e:
        logger.error("❌ Pipeline start failed: %s", e)
        return 1

    # Run main event loop (blocks until quit signal or error)
    try:
        loop.run()
    except KeyboardInterrupt:
        logger.info("⊛ Keyboard interrupt (CTRL+C) received")
        return 0
    except Exception as e:
        logger.error("❌ Main loop error: %s", e)
        if IS_DEBUG:
            logger.exception("Full traceback:")
        return 1
    finally:
        # Guaranteed cleanup on any exit path
        if pipeline is not None:
            pipeline.set_state(Gst.State.NULL)

        # Save final statistics report
        save_report()
        logger.info("✅ Application shutdown complete")

    return 0


# ============================================================================
# Application Entry Point
# ============================================================================


if __name__ == "__main__":
    exit(main())
