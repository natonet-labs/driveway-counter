#!/usr/bin/env python3
"""
Web Calibration Tool - Single Zone Visualization.

A Flask web application that displays a live RTSP video stream
with an overlaid tracking zone polygon for coordinate calibration.

Usage:
    python web_calib.py
    Open http://rpi.local:8081       — calibration UI
    Open http://rpi.local:8081/video — raw MJPEG stream

Environment variables (via .env):
    USERNAME      Camera username
    PASSWORD      Camera password
    IP_ADDRESS    Camera IP address
    CHANNEL       Camera channel (default: 1)
    SUBTYPE       RTSP subtype (0=main, 1=substream)
    ORIG_W        Original camera width (e.g. 704)
    ORIG_H        Original camera height (e.g. 480)
    TRACKING_ZONE Zone polygon in original resolution coords
                  e.g. [[380,3],[480,3],[480,460],[380,460]]
"""

from __future__ import annotations

import ast
import logging
import os
import time
from typing import Generator, Optional, Tuple

import cv2
import numpy as np
from dotenv import load_dotenv
from flask import Flask, Response

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# Suppress noisy OpenCV/FFmpeg output
os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "loglevel;error"

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
load_dotenv()

USERNAME: str = os.getenv("USERNAME", "")
PASSWORD: str = os.getenv("PASSWORD", "")
IP_ADDRESS: str = os.getenv("IP_ADDRESS", "")
CHANNEL: str = os.getenv("CHANNEL", "1")
SUBTYPE: str = os.getenv("SUBTYPE", "0")

ORIG_W: int = int(os.getenv("ORIG_W", 704))
ORIG_H: int = int(os.getenv("ORIG_H", 480))

FLASK_HOST: str = "0.0.0.0"
FLASK_PORT: int = int(os.getenv("FLASK_PORT", 8081))

RTSP_URL: str = (
    f"rtsp://{USERNAME}:{PASSWORD}@{IP_ADDRESS}:554"
    f"/cam/realmonitor?channel={CHANNEL}&subtype={SUBTYPE}"
)

# ---------------------------------------------------------------------------
# Zone Configuration
# ---------------------------------------------------------------------------
_DEFAULT_ZONE = "[[100,50],[600,50],[600,430],[100,430]]"
_zone_raw: str = os.getenv("TRACKING_ZONE", _DEFAULT_ZONE)

try:
    TRACKING_ZONE_ORIG: np.ndarray = np.array(
        ast.literal_eval(_zone_raw), dtype=np.int32
    )
except (ValueError, SyntaxError):
    logger.warning("Invalid TRACKING_ZONE, using default: %s", _DEFAULT_ZONE)
    TRACKING_ZONE_ORIG = np.array(ast.literal_eval(_DEFAULT_ZONE), dtype=np.int32)

# ---------------------------------------------------------------------------
# Visualization Constants
# ---------------------------------------------------------------------------
ZONE_COLOR: Tuple[int, int, int] = (0, 255, 255)  # Yellow (BGR)
TEXT_COLOR: Tuple[int, int, int] = (255, 255, 255)  # White (BGR)
BORDER_THICKNESS: int = 3
FONT_SCALE: float = 1.0
FONT_FACE: int = cv2.FONT_HERSHEY_SIMPLEX
JPEG_QUALITY: int = 85
MAX_CONSECUTIVE_FAILURES: int = 50

# ---------------------------------------------------------------------------
# Video Capture
# ---------------------------------------------------------------------------
cap: Optional[cv2.VideoCapture] = None


def initialize_video_capture() -> cv2.VideoCapture:
    """Initialize RTSP video capture with optimized settings.

    Returns:
        cv2.VideoCapture: Opened capture object.

    Raises:
        RuntimeError: If RTSP connection cannot be established.
    """
    global cap
    logger.info(
        "Connecting to RTSP rtsp://%s@%s channel=%s subtype=%s ...",
        USERNAME,
        IP_ADDRESS,
        CHANNEL,
        SUBTYPE,
    )
    cap = cv2.VideoCapture(RTSP_URL, cv2.CAP_FFMPEG)
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    cap.set(cv2.CAP_PROP_FPS, 15)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, ORIG_W)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, ORIG_H)
    cap.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 5000)
    cap.set(cv2.CAP_PROP_READ_TIMEOUT_MSEC, 1000)

    if not cap.isOpened():
        raise RuntimeError(f"RTSP connection failed: {RTSP_URL}")

    logger.info("RTSP connected (buffer=1, timeout=1s)")
    return cap


# ---------------------------------------------------------------------------
# Zone Utilities
# ---------------------------------------------------------------------------
def scale_zone_to_frame(
    zone_orig: np.ndarray, frame_w: int, frame_h: int
) -> np.ndarray:
    """Scale zone from original resolution to current frame resolution.

    Args:
        zone_orig: Zone polygon in original camera resolution.
        frame_w:   Target frame width in pixels.
        frame_h:   Target frame height in pixels.

    Returns:
        np.ndarray: Scaled zone coordinates.
    """
    if ORIG_W == 0 or ORIG_H == 0:
        return zone_orig.copy()
    scale = np.array([frame_w / ORIG_W, frame_h / ORIG_H], dtype=np.float32)
    return (zone_orig * scale).astype(np.int32)


def clip_zone_to_frame(zone: np.ndarray, frame_w: int, frame_h: int) -> np.ndarray:
    """Clip zone coordinates to frame boundaries.

    Args:
        zone:    Zone polygon as numpy array.
        frame_w: Frame width in pixels.
        frame_h: Frame height in pixels.

    Returns:
        np.ndarray: Clipped zone coordinates.
    """
    clipped = zone.copy()
    clipped[:, 0] = np.clip(clipped[:, 0], 0, frame_w - 1)
    clipped[:, 1] = np.clip(clipped[:, 1], 0, frame_h - 1)
    return clipped


def draw_zone_on_frame(frame: np.ndarray) -> np.ndarray:
    """Draw tracking zone polygon and resolution label on frame.

    Args:
        frame: Input BGR video frame.

    Returns:
        np.ndarray: Frame with zone overlay.
    """
    h, w = frame.shape[:2]
    zone_scaled = scale_zone_to_frame(TRACKING_ZONE_ORIG, w, h)
    zone_clipped = clip_zone_to_frame(zone_scaled, w, h)

    cv2.polylines(frame, [zone_clipped], True, ZONE_COLOR, BORDER_THICKNESS)
    cv2.putText(frame, f"{w}x{h}", (10, 30), FONT_FACE, FONT_SCALE, TEXT_COLOR, 2)
    cv2.putText(frame, "Tracking Zone", (10, 60), FONT_FACE, FONT_SCALE, ZONE_COLOR, 2)
    return frame


# ---------------------------------------------------------------------------
# MJPEG Stream Generator
# ---------------------------------------------------------------------------
def gen_frames() -> Generator[bytes, None, None]:
    """Generate MJPEG frames for Flask streaming route.

    Yields:
        bytes: MJPEG frame with multipart boundary headers.
    """
    global cap
    consecutive_failures = 0

    while True:
        ret, frame = cap.read()

        if not ret:
            consecutive_failures += 1
            if consecutive_failures % 10 == 0:
                logger.warning("Frame drop #%d", consecutive_failures)
            if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                logger.warning("Reconnecting RTSP...")
                cap.release()
                time.sleep(1)
                cap = initialize_video_capture()
                consecutive_failures = 0
            continue

        consecutive_failures = 0
        frame = draw_zone_on_frame(frame)

        ret, buffer = cv2.imencode(
            ".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY]
        )
        if not ret:
            continue

        yield (
            b"--frame\r\n"
            b"Content-Type: image/jpeg\r\n\r\n" + buffer.tobytes() + b"\r\n"
        )


# ---------------------------------------------------------------------------
# Flask Application
# ---------------------------------------------------------------------------
app = Flask(__name__)

_HTML_TEMPLATE: str = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>Driveway Zone Calibration</title>
  <style>
    body {{ background:#222; color:#eee; font-family:sans-serif; text-align:center; }}
    h1   {{ margin-top:20px; }}
    img  {{ border:2px solid #555; margin-top:20px; max-width:100%; }}
    .note {{ margin-top:10px; font-size:0.9em; color:#ccc; }}
  </style>
</head>
<body>
  <h1>Driveway Zone Calibration</h1>
  <p class="note">
    Yellow polygon = <strong>TRACKING_ZONE</strong> from <code>.env</code>
    (original {ORIG_W}x{ORIG_H}, scaled to stream)
  </p>
  <img src="/video" alt="Live stream with tracking zone">
  <p class="note">Edit <code>TRACKING_ZONE</code> in <code>.env</code> and refresh to update.</p>
</body>
</html>"""


@app.route("/")
def index() -> str:
    """Serve the calibration web page."""
    return _HTML_TEMPLATE


@app.route("/video")
def video() -> Response:
    """Stream live MJPEG video with zone overlay."""
    return Response(
        gen_frames(),
        mimetype="multipart/x-mixed-replace; boundary=frame",
    )


# ---------------------------------------------------------------------------
# Entry Point
# ---------------------------------------------------------------------------
def main() -> int:
    """Start RTSP capture and Flask web server.

    Returns:
        int: Exit code (0 = success, 1 = error).
    """
    global cap
    try:
        initialize_video_capture()
        logger.info(
            "Zone (original %dx%d): %s", ORIG_W, ORIG_H, TRACKING_ZONE_ORIG.tolist()
        )
        logger.info("Starting server → http://%s:%d", FLASK_HOST, FLASK_PORT)
        app.run(host=FLASK_HOST, port=FLASK_PORT, debug=False, threaded=True)
    except RuntimeError as e:
        logger.error("Startup error: %s", e)
        return 1
    except KeyboardInterrupt:
        logger.info("Shutting down...")
    finally:
        if cap is not None:
            cap.release()
            cap = None
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
