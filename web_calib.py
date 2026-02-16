#!/usr/bin/env python3
"""Web Calibration Tool - Single Zone Visualization.

This Flask web application displays a live video stream from an RTSP camera
with an overlaid single tracking zone polygon for calibration.

Environment variables (.env):
    USERNAME: Camera username
    PASSWORD: Camera password
    IPADDRESS: Camera IP address
    CHANNEL: Camera channel (default: 1)
    SUBTYPE: RTSP stream subtype (0=main, 1=substream)
    ORIG_W: Original camera width (e.g. 704 or 1920)
    ORIG_H: Original camera height (e.g. 480 or 1080)
    TRACKING_ZONE: Tracking zone polygon in ORIGINAL resolution coordinates
                   e.g. [[100,50],[600,50],[600,430],[100,430]]
"""

import os
import time
from typing import Generator, Optional, Tuple

import cv2
import numpy as np
from dotenv import load_dotenv
from flask import Flask, Response

# Configure OpenCV logging
os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "loglevel;error"

# Load environment variables
load_dotenv()


# ============================================================================
# Configuration
# ============================================================================

# RTSP Camera Connection Settings
USERNAME: str = os.getenv("USERNAME", "")
PASSWORD: str = os.getenv("PASSWORD", "")
IPADDRESS: str = os.getenv("IPADDRESS", "")
CHANNEL: str = os.getenv("CHANNEL", "1")
SUBTYPE: str = os.getenv("SUBTYPE", "0")

RTSP_URL: str = (
    f"rtsp://{USERNAME}:{PASSWORD}@{IPADDRESS}:554"
    f"/cam/realmonitor?channel={CHANNEL}&subtype={SUBTYPE}"
)

# Original resolution from .env (used for zone coordinates)
ORIG_W: int = int(os.getenv("ORIG_W", "3840"))
ORIG_H: int = int(os.getenv("ORIG_H", "2160"))

# Single Zone Definition (in ORIGINAL resolution)
TRACKING_ZONE_STR: str = os.getenv(
    "TRACKING_ZONE",
    # Default: wide rectangle for 704x480, will scale for other resolutions
    "[[100,50],[600,50],[600,430],[100,430]]"
)
TRACKING_ZONE_ORIG: np.ndarray = np.array(eval(TRACKING_ZONE_STR), dtype=np.int32)

# Visualization Settings (BGR format)
ZONE_COLOR: Tuple[int, int, int] = (0, 255, 255)  # Yellow
TEXT_COLOR: Tuple[int, int, int] = (255, 255, 255)  # White
BORDER_THICKNESS: int = 3
FONT_SIZE: float = 1.0

# Flask Application Settings
FLASK_HOST: str = "0.0.0.0"
FLASK_PORT: int = 8081

# ============================================================================
# Global State
# ============================================================================

cap: Optional[cv2.VideoCapture] = None

# ============================================================================
# Helper Functions
# ============================================================================


def initialize_video_capture() -> cv2.VideoCapture:
    """Initialize video capture from RTSP stream.

    Returns:
        cv2.VideoCapture: Video capture object

    Raises:
        RuntimeError: If RTSP connection fails
    """
    global cap
    print(f"🔗 Connecting RTSP: rtsp://{USERNAME}:****@{IPADDRESS}:{CHANNEL}/{SUBTYPE}...")
    
    cap = cv2.VideoCapture(RTSP_URL, cv2.CAP_FFMPEG)
    
    # RTSP optimizations
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)           # Minimal buffering
    cap.set(cv2.CAP_PROP_FPS, 15)                 # Target FPS
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 704)        # Match substream
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
    
    # FFmpeg RTSP params (robust)
    cap.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 5000)
    cap.set(cv2.CAP_PROP_READ_TIMEOUT_MSEC, 1000)
    
    if not cap.isOpened():
        raise RuntimeError(f"❌ RTSP failed: {RTSP_URL}")
    
    print("✅ RTSP connected (buff=1, timeout=1s)")
    return cap


def log_zone_config() -> None:
    """Log the current zone configuration."""
    print(f"Original resolution (ORIG_WxORIG_H): {ORIG_W}x{ORIG_H}")
    print(f"Tracking Zone (ORIGINAL coords): {TRACKING_ZONE_STR}")


def clip_zone_to_frame(
    zone: np.ndarray, frame_width: int, frame_height: int
) -> np.ndarray:
    """Clip zone coordinates to frame boundaries.

    Args:
        zone: Zone polygon as numpy array
        frame_width: Frame width in pixels
        frame_height: Frame height in pixels

    Returns:
        np.ndarray: Clipped zone coordinates
    """
    clipped = zone.copy()
    clipped[:, 0] = np.clip(clipped[:, 0], 0, frame_width - 1)
    clipped[:, 1] = np.clip(clipped[:, 1], 0, frame_height - 1)
    return clipped


def scale_zone_to_frame(
    zone_orig: np.ndarray, frame_width: int, frame_height: int
) -> np.ndarray:
    """Scale zone from ORIGINAL resolution to current frame resolution.

    Args:
        zone_orig: Zone polygon in original resolution
        frame_width: Target frame width in pixels
        frame_height: Target frame height in pixels

    Returns:
        np.ndarray: Scaled zone coordinates
    """
    if ORIG_W <= 0 or ORIG_H <= 0:
        return zone_orig.copy()
    scale_x = frame_width / ORIG_W
    scale_y = frame_height / ORIG_H
    scaled = zone_orig * np.array([scale_x, scale_y], dtype=np.float32)
    return scaled.astype(np.int32)


def draw_zone_on_frame(frame: np.ndarray, zone_orig: np.ndarray) -> np.ndarray:
    """Draw single tracking zone on frame.

    Args:
        frame: Input video frame
        zone_orig: Zone polygon in original resolution

    Returns:
        np.ndarray: Frame with zone drawn
    """
    h, w = frame.shape[:2]

    # Scale and clip to frame
    zone_scaled = scale_zone_to_frame(zone_orig, w, h)
    zone_clipped = clip_zone_to_frame(zone_scaled, w, h)

    # Draw polygon
    cv2.polylines(frame, [zone_clipped], True, ZONE_COLOR, BORDER_THICKNESS)

    # Label + resolution
    cv2.putText(
        frame,
        f"{w}x{h}",
        (10, 30),
        cv2.FONT_HERSHEY_SIMPLEX,
        FONT_SIZE,
        TEXT_COLOR,
        2,
    )
    cv2.putText(
        frame,
        "Tracking Zone",
        (10, 60),
        cv2.FONT_HERSHEY_SIMPLEX,
        FONT_SIZE,
        ZONE_COLOR,
        2,
    )

    return frame


def gen_frames() -> Generator[bytes, None, None]:
    """Generate video frames for streaming.

    Yields:
        bytes: MJPEG frame data with boundary markers
    """
    global cap
    
    consecutive_failures = 0
    max_failures = 50
    
    while True:
        ret, frame = cap.read()
        if not ret:
            consecutive_failures += 1
            if consecutive_failures % 10 == 0:
                print(f"⚠️ Frame drop #{consecutive_failures}")
            if consecutive_failures > max_failures:
                print("🔄 Reconnecting RTSP...")
                cap.release()
                time.sleep(1)
                cap = initialize_video_capture()  # Now safe
                consecutive_failures = 0
            continue
        
        consecutive_failures = 0
        
        # Draw single zone
        frame = draw_zone_on_frame(frame, TRACKING_ZONE_ORIG)
        
        # JPEG (quality 85 for speed)
        ret, buffer = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
        if not ret:
            continue
            
        yield (b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" +  buffer.tobytes() + b"\r\n")


# ============================================================================
# Flask Application
# ============================================================================

app = Flask(__name__)

HTML_TEMPLATE: str = """
<html>
  <head>
    <title>Driveway Single Zone Calibration</title>
    <style>
      body { background-color: #222; color: #eee; font-family: sans-serif; text-align: center; }
      h1 { margin-top: 20px; }
      img { border: 2px solid #555; margin-top: 20px; }
      .note { margin-top: 10px; font-size: 0.9em; color: #ccc; }
    </style>
  </head>
  <body>
    <h1>Driveway Single Zone Calibration</h1>
    <p class="note">
      Yellow polygon = TRACKING_ZONE (from .env, ORIGINAL resolution, scaled to stream).
    </p>
    <img src="/video" />
  </body>
</html>
"""


@app.route("/")
def index() -> str:
    """Serve the calibration page."""
    return HTML_TEMPLATE


@app.route("/video")
def video() -> Response:
    """Stream video frames."""
    return Response(
        gen_frames(),
        mimetype="multipart/x-mixed-replace; boundary=frame",
    )


# ============================================================================
# Application Entry Point
# ============================================================================


def main() -> int:
    """Main application entry point.

    Returns:
        int: Exit code (0 for success, 1 for error)
    """
    global cap
    
    try:
        initialize_video_capture()
        log_zone_config()
        print(f"Starting Flask server on http://{FLASK_HOST}:{FLASK_PORT}")
        app.run(host=FLASK_HOST, port=FLASK_PORT, debug=False, threaded=True)
    except RuntimeError as e:
        print(f"❌ Error: {e}")
        return 1
    except KeyboardInterrupt:
        print("\n🛑 Shutting down...")
        if cap is not None:
            cap.release()
            cap = None
        return 0
    finally:
        if cap is not None:
            cap.release()


if __name__ == "__main__":
    exit(main())
