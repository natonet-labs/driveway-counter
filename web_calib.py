#!/usr/bin/env python3
"""Web Calibration Tool - Single Zone Visualization.

This Flask web application displays a live video stream from an RTSP camera
with an overlaid single tracking zone polygon for calibration. It serves a
web interface for zone visualization and calibration purposes.

The application:
    - Connects to an RTSP camera using connection parameters from .env
    - Streams live video frames with overlay visualization
    - Displays a configurable tracking zone polygon for calibration
    - Uses Flask to serve web interface on http://localhost:8081

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

import ast
import logging
import os
import time
from typing import Generator, Optional, Tuple

import cv2
import numpy as np
from dotenv import load_dotenv
from flask import Flask, Response

__all__ = [
    "initialize_video_capture",
    "log_zone_config",
    "clip_zone_to_frame",
    "scale_zone_to_frame",
    "draw_zone_on_frame",
    "gen_frames",
    "index",
    "video",
    "main",
]

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

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
    "[[100,50],[600,50],[600,430],[100,430]]",
)

# Parse tracking zone using safe literal_eval instead of eval()
try:
    TRACKING_ZONE_ORIG: np.ndarray = np.array(
        ast.literal_eval(TRACKING_ZONE_STR),
        dtype=np.int32,
    )
except (ValueError, SyntaxError) as e:
    logger.error(f"Invalid TRACKING_ZONE format: {e}")
    TRACKING_ZONE_ORIG = np.array(
        [[100, 50], [600, 50], [600, 430], [100, 430]],
        dtype=np.int32,
    )

# Visualization Settings (BGR format)
ZONE_COLOR: Tuple[int, int, int] = (0, 255, 255)  # Yellow
TEXT_COLOR: Tuple[int, int, int] = (255, 255, 255)  # White
BORDER_THICKNESS: int = 3
FONT_SIZE: float = 1.0
FONT_THICKNESS: int = 2

# Video capture parameters
STREAM_WIDTH: int = 704
STREAM_HEIGHT: int = 480
TARGET_FPS: int = 15
BUFFER_SIZE: int = 1
RTSP_OPEN_TIMEOUT_MS: int = 5000
RTSP_READ_TIMEOUT_MS: int = 1000

# JPEG encoding settings
JPEG_QUALITY: int = 85

# Frame streaming parameters
FRAME_FAILURE_REPORT_INTERVAL: int = 10
MAX_CONSECUTIVE_FRAME_FAILURES: int = 50
RECONNECT_DELAY_SEC: float = 1.0

# Text overlay positions (x, y) in pixels
TEXT_POSITION_RESOLUTION: Tuple[int, int] = (10, 30)
TEXT_POSITION_LABEL: Tuple[int, int] = (10, 60)

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

    Opens an RTSP connection using OpenCV's FFMPEG backend with optimized
    settings for minimal latency and robust error handling.

    Returns:
        cv2.VideoCapture: Configured video capture object connected to stream

    Raises:
        RuntimeError: If RTSP connection fails or stream is not accessible
    """
    global cap
    logger.info(
        f"🔗 Connecting RTSP: rtsp://{USERNAME}:****@{IPADDRESS}:{CHANNEL}/{SUBTYPE}..."
    )

    cap = cv2.VideoCapture(RTSP_URL, cv2.CAP_FFMPEG)

    # Configure capture properties for low-latency, reliable RTSP streaming
    cap.set(cv2.CAP_PROP_BUFFERSIZE, BUFFER_SIZE)  # Minimal buffering
    cap.set(cv2.CAP_PROP_FPS, TARGET_FPS)  # Target frames per second
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, STREAM_WIDTH)  # Match substream resolution
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, STREAM_HEIGHT)

    # FFmpeg RTSP timeout parameters for robust reconnection handling
    cap.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, RTSP_OPEN_TIMEOUT_MS)
    cap.set(cv2.CAP_PROP_READ_TIMEOUT_MSEC, RTSP_READ_TIMEOUT_MS)

    if not cap.isOpened():
        error_msg = f"RTSP connection failed: {RTSP_URL}"
        logger.error(f"❌ {error_msg}")
        raise RuntimeError(error_msg)

    logger.info(
        f"✅ RTSP connected (buffer={BUFFER_SIZE}, timeout={RTSP_READ_TIMEOUT_MS}ms)"
    )
    return cap


def log_zone_config() -> None:
    """Log the current zone configuration for debugging and verification.

    Outputs the original camera resolution and tracking zone coordinates
    to help validate configuration settings.
    """
    logger.info(f"Original resolution: {ORIG_W}x{ORIG_H}")
    logger.info(f"Tracking Zone (original coords): {TRACKING_ZONE_STR}")


def clip_zone_to_frame(
    zone: np.ndarray,
    frame_width: int,
    frame_height: int,
) -> np.ndarray:
    """Clip zone coordinates to frame boundaries.

    Ensures polygon coordinates stay within valid frame dimensions by clipping
    to [0, width-1] and [0, height-1] ranges.

    Args:
        zone: Zone polygon vertices as Nx2 numpy array
        frame_width: Frame width in pixels
        frame_height: Frame height in pixels

    Returns:
        np.ndarray: Clipped zone coordinates with same shape as input
    """
    clipped = zone.copy()
    clipped[:, 0] = np.clip(clipped[:, 0], 0, frame_width - 1)
    clipped[:, 1] = np.clip(clipped[:, 1], 0, frame_height - 1)
    return clipped


def scale_zone_to_frame(
    zone_orig: np.ndarray,
    frame_width: int,
    frame_height: int,
) -> np.ndarray:
    """Scale zone polygon from original resolution to current frame resolution.

    Applies linear scaling based on aspect ratio differences between original
    and current frame dimensions. Handles edge case where original dimensions
    are invalid by returning a copy of the original zone.

    Args:
        zone_orig: Zone polygon in original camera resolution
        frame_width: Target frame width in pixels
        frame_height: Target frame height in pixels

    Returns:
        np.ndarray: Scaled zone coordinates as int32 array

    Examples:
        >>> zone = np.array([[100, 50], [600, 50], [600, 430], [100, 430]])
        >>> scaled = scale_zone_to_frame(zone, 352, 240)  # Half resolution
        # Returns coordinates scaled accordingly
    """
    # Handle invalid original resolution
    if ORIG_W <= 0 or ORIG_H <= 0:
        logger.warning(
            f"Invalid original resolution ({ORIG_W}x{ORIG_H}), returning unscaled zone"
        )
        return zone_orig.copy()

    scale_x = frame_width / ORIG_W
    scale_y = frame_height / ORIG_H
    scaled = zone_orig * np.array([scale_x, scale_y], dtype=np.float32)
    return scaled.astype(np.int32)


def draw_zone_on_frame(
    frame: np.ndarray,
    zone_orig: np.ndarray,
) -> np.ndarray:
    """Draw tracking zone polygon and labels on video frame.

    Scales the zone from original resolution to match current frame size,
    then draws the polygon outline and text labels for calibration visualization.

    Args:
        frame: Input video frame (BGR format)
        zone_orig: Zone polygon in original resolution coordinates

    Returns:
        np.ndarray: Frame with zone polygon and labels overlaid
    """
    height, width = frame.shape[:2]

    # Scale zone to current frame resolution and clip to boundaries
    zone_scaled = scale_zone_to_frame(zone_orig, width, height)
    zone_clipped = clip_zone_to_frame(zone_scaled, width, height)

    # Draw zone polygon
    cv2.polylines(
        frame,
        [zone_clipped],
        True,  # Closed polygon
        ZONE_COLOR,
        BORDER_THICKNESS,
    )

    # Draw frame resolution label
    cv2.putText(
        frame,
        f"{width}x{height}",
        TEXT_POSITION_RESOLUTION,
        cv2.FONT_HERSHEY_SIMPLEX,
        FONT_SIZE,
        TEXT_COLOR,
        FONT_THICKNESS,
    )

    # Draw zone label
    cv2.putText(
        frame,
        "Tracking Zone",
        TEXT_POSITION_LABEL,
        cv2.FONT_HERSHEY_SIMPLEX,
        FONT_SIZE,
        ZONE_COLOR,
        FONT_THICKNESS,
    )

    return frame


def gen_frames() -> Generator[bytes, None, None]:
    """Generate video frames for streaming to web client.

    Continuously reads frames from the video capture object, applies zone
    visualization, encodes as JPEG, and yields MJPEG-formatted data for
    streaming. Handles frame read failures with automatic reconnection.

    Yields:
        bytes: MJPEG frame data with multipart boundary markers

    Note:
        Responds to frame read failures by incrementing a failure counter.
        After exceeding MAX_CONSECUTIVE_FRAME_FAILURES, automatically
        reconnects to the RTSP stream.
    """
    global cap

    consecutive_failures = 0
    mjpeg_boundary = b"--frame\r\nContent-Type: image/jpeg\r\n\r\n"

    while True:
        if cap is None:
            logger.error("Video capture object is None")
            time.sleep(RECONNECT_DELAY_SEC)
            continue

        ret, frame = cap.read()

        # Handle frame read failures
        if not ret:
            consecutive_failures += 1

            # Log periodic failures to avoid spam
            if consecutive_failures % FRAME_FAILURE_REPORT_INTERVAL == 0:
                logger.warning(f"⚠️ Frame drop #{consecutive_failures}")

            # Attempt reconnection after threshold
            if consecutive_failures > MAX_CONSECUTIVE_FRAME_FAILURES:
                logger.info("🔄 Reconnecting RTSP... (failure threshold exceeded)")
                try:
                    cap.release()
                    time.sleep(RECONNECT_DELAY_SEC)
                    cap = initialize_video_capture()
                except RuntimeError as e:
                    logger.error(f"Reconnection failed: {e}")
                    consecutive_failures = 0

            continue

        # Reset failure counter on successful read
        consecutive_failures = 0

        # Draw zone overlay
        frame = draw_zone_on_frame(frame, TRACKING_ZONE_ORIG)

        # Encode frame as JPEG with quality setting
        ret, buffer = cv2.imencode(
            ".jpg",
            frame,
            [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY],
        )

        if not ret:
            logger.debug("Failed to encode frame as JPEG")
            continue

        # Yield MJPEG frame with proper boundary formatting
        yield mjpeg_boundary + buffer.tobytes() + b"\r\n"


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
    """Serve the web calibration interface.

    Returns:
        str: HTML page with embedded video stream

    Route:
        GET /
    """
    return HTML_TEMPLATE


@app.route("/video")
def video() -> Response:
    """Stream live video frames to web client.

    Streams MJPEG video with zone overlay from gen_frames() to the web client.
    Client continuously requests this endpoint to display live video.

    Returns:
        Response: MJPEG multipart stream with proper content-type header

    Route:
        GET /video

    MIME type:
        multipart/x-mixed-replace; boundary=frame
    """
    return Response(
        gen_frames(),
        mimetype="multipart/x-mixed-replace; boundary=frame",
    )


# ============================================================================
# Application Entry Point
# ============================================================================


def main() -> int:
    """Main application entry point.

    Initializes video capture, logs configuration, starts Flask web server,
    and handles graceful shutdown with proper resource cleanup.

    Returns:
        int: Exit code (0 for success, 1 for error)

    Exit codes:
        0: Successful execution and shutdown
        1: Runtime error (e.g., RTSP connection failed)
    """
    global cap

    try:
        # Initialize video capture and log configuration
        initialize_video_capture()
        log_zone_config()

        # Start Flask web server
        logger.info(f"Starting Flask server on http://{FLASK_HOST}:{FLASK_PORT}")
        app.run(
            host=FLASK_HOST,
            port=FLASK_PORT,
            debug=False,
            threaded=True,
        )

        return 0

    except RuntimeError as e:
        logger.error(f"❌ Runtime error: {e}")
        return 1

    except KeyboardInterrupt:
        logger.info("🛑 Shutdown signal received (Ctrl+C)")
        return 0

    finally:
        # Cleanup: release video capture resources
        if cap is not None:
            try:
                cap.release()
                logger.info("✅ Video capture resources released")
            except Exception as e:
                logger.error(f"Error releasing video capture: {e}")
            finally:
                cap = None


if __name__ == "__main__":
    exit(main())
