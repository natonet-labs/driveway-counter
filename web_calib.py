#!/usr/bin/env python3
"""Web Calibration Tool - Single Zone Visualization.

This Flask web application displays a live video stream from an RTSP camera
with an overlaid single tracking zone polygon for calibration purposes.

The application connects to an RTSP camera using environment variables from .env,
streams live video frames with zone overlay visualization, and provides a web
interface on https://localhost:8081 for real-time calibration and configuration.

Note: Browsers auto-upgrade local network connections to HTTPS, so SSL support
is enabled with self-signed certificates. Accept the certificate warning when
first accessing the interface.

Environment Variables (.env):
    USERNAME: Camera username
    PASSWORD: Camera password
    IPADDRESS: Camera IP address
    CHANNEL: Camera channel number (default: 1)
    SUBTYPE: RTSP stream subtype (0=main, 1=substream)
    ORIG_W: Original camera width in pixels (e.g., 704 or 1920)
    ORIG_H: Original camera height in pixels (e.g., 480 or 1080)
    TRACKING_ZONE: Tracking zone polygon in ORIGINAL resolution coordinates
                   Format: [[x1,y1],[x2,y2],[x3,y3],[x4,y4]]

Example .env:
    USERNAME=user
    PASSWORD=pass
    IPADDRESS=192.168.1.100
    CHANNEL=1
    SUBTYPE=0
    ORIG_W=704
    ORIG_H=480
    TRACKING_ZONE=[[380,3],[480,3],[480,460],[380,460]]
"""

import os
import time
from typing import Generator, Optional, Tuple

import cv2
import numpy as np
from dotenv import load_dotenv
from flask import Flask, Response

# Load environment variables early
load_dotenv()

# Suppress OpenCV FFmpeg logging
os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "loglevel;error"

# =============================================================================
# Configuration - RTSP Camera Connection
# =============================================================================

USERNAME: str = os.getenv("USERNAME", "")
PASSWORD: str = os.getenv("PASSWORD", "")
IPADDRESS: str = os.getenv("IPADDRESS", "")
CHANNEL: str = os.getenv("CHANNEL", "1")
SUBTYPE: str = os.getenv("SUBTYPE", "0")

RTSP_URL: str = (
    f"rtsp://{USERNAME}:{PASSWORD}@{IPADDRESS}:554"
    f"/cam/realmonitor?channel={CHANNEL}&subtype={SUBTYPE}"
)


# =============================================================================
# Configuration - Original Camera Resolution and Zone
# =============================================================================

ORIG_W: int = int(os.getenv("ORIG_W", "3840"))
ORIG_H: int = int(os.getenv("ORIG_H", "2160"))

TRACKING_ZONE_STR: str = os.getenv(
    "TRACKING_ZONE",
    "[[100,50],[600,50],[600,430],[100,430]]",
)
TRACKING_ZONE_ORIG: np.ndarray = np.array(eval(TRACKING_ZONE_STR), dtype=np.int32)


# =============================================================================
# Configuration - Video Stream Parameters
# =============================================================================

STREAM_WIDTH: int = 704
STREAM_HEIGHT: int = 480
RTSP_BUFFER_SIZE: int = 1
RTSP_TARGET_FPS: int = 15
RTSP_OPEN_TIMEOUT_MS: int = 5000
RTSP_READ_TIMEOUT_MS: int = 1000


# =============================================================================
# Configuration - Visualization
# =============================================================================

ZONE_COLOR: Tuple[int, int, int] = (0, 255, 255)  # Yellow (BGR)
TEXT_COLOR: Tuple[int, int, int] = (255, 255, 255)  # White (BGR)
BORDER_THICKNESS: int = 3
FONT_SIZE: float = 1.0
FONT_THICKNESS: int = 2
JPEG_QUALITY: int = 85


# =============================================================================
# Configuration - Error Handling & Reconnection
# =============================================================================

MAX_CONSECUTIVE_FRAME_FAILURES: int = 50
FRAME_FAILURE_REPORT_INTERVAL: int = 10
RECONNECT_DELAY_SEC: float = 1.0


# =============================================================================
# Configuration - Flask Application
# =============================================================================

FLASK_HOST: str = "0.0.0.0"
FLASK_PORT: int = 8081


# =============================================================================
# Global State
# =============================================================================

cap: Optional[cv2.VideoCapture] = None

# =============================================================================
# Video Capture & Initialization
# =============================================================================


def initialize_video_capture() -> cv2.VideoCapture:
    """Initialize and configure RTSP video capture.

    Opens an RTSP connection using OpenCV's FFMPEG backend with optimized
    settings for low-latency, reliable streaming from IP cameras.

    Returns:
        cv2.VideoCapture: Configured video capture object connected to stream.

    Raises:
        RuntimeError: If RTSP connection fails or stream is not accessible.

    Note:
        Sets minimal buffer size (1) to reduce latency and configures
        timeouts for robust reconnection handling on network issues.
    """
    global cap

    print(
        f"🔗 Connecting RTSP: rtsp://{USERNAME}:****@{IPADDRESS}:{CHANNEL}/{SUBTYPE}..."
    )

    cap = cv2.VideoCapture(RTSP_URL, cv2.CAP_FFMPEG)

    # Configure capture properties for low-latency, reliable RTSP streaming
    cap.set(cv2.CAP_PROP_BUFFERSIZE, RTSP_BUFFER_SIZE)
    cap.set(cv2.CAP_PROP_FPS, RTSP_TARGET_FPS)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, STREAM_WIDTH)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, STREAM_HEIGHT)

    # FFmpeg RTSP timeout parameters for robust reconnection handling
    cap.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, RTSP_OPEN_TIMEOUT_MS)
    cap.set(cv2.CAP_PROP_READ_TIMEOUT_MSEC, RTSP_READ_TIMEOUT_MS)

    if not cap.isOpened():
        error_msg = f"RTSP connection failed: {RTSP_URL}"
        print(f"❌ {error_msg}")
        raise RuntimeError(error_msg)

    print(
        f"✅ RTSP connected (buffer={RTSP_BUFFER_SIZE}, timeout={RTSP_READ_TIMEOUT_MS}ms)"
    )
    return cap


def log_zone_config() -> None:
    """Log current zone configuration for verification and debugging.

    Prints the original camera resolution and tracking zone coordinates
    to help verify that environment variables are loaded correctly.
    """
    print(f"Original resolution: {ORIG_W}x{ORIG_H}")
    print(f"Tracking Zone (original coords): {TRACKING_ZONE_STR}")


# =============================================================================
# Coordinate Scaling & Clipping
# =============================================================================


def clip_zone_to_frame(
    zone: np.ndarray,
    frame_width: int,
    frame_height: int,
) -> np.ndarray:
    """Clip zone polygon coordinates to frame boundaries.

    Ensures all polygon vertices stay within valid frame dimensions by
    clipping to [0, width-1] and [0, height-1] ranges to prevent
    out-of-bounds drawing operations.

    Args:
        zone: Zone polygon vertices as Nx2 numpy array of int32.
        frame_width: Frame width in pixels.
        frame_height: Frame height in pixels.

    Returns:
        np.ndarray: Clipped zone coordinates with same shape as input.
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

    Applies linear scaling based on aspect ratio differences between the
    original camera resolution and the current video frame resolution.

    Args:
        zone_orig: Zone polygon in original camera resolution.
        frame_width: Target frame width in pixels.
        frame_height: Target frame height in pixels.

    Returns:
        np.ndarray: Scaled zone coordinates as int32 array.

    Note:
        If original resolution is invalid (≤0), returns a copy of the
        original zone unscaled.
    """
    if ORIG_W <= 0 or ORIG_H <= 0:
        print(f"⚠️ Invalid original resolution ({ORIG_W}x{ORIG_H}), using unscaled zone")
        return zone_orig.copy()

    scale_x = frame_width / ORIG_W
    scale_y = frame_height / ORIG_H
    scaled = zone_orig * np.array([scale_x, scale_y], dtype=np.float32)
    return scaled.astype(np.int32)


# =============================================================================
# Frame Rendering
# =============================================================================


def draw_zone_on_frame(
    frame: np.ndarray,
    zone_orig: np.ndarray,
) -> np.ndarray:
    """Draw tracking zone polygon and labels on video frame.

    Scales the zone from original resolution to match the current frame size,
    clips to frame boundaries, then draws the polygon outline and text labels
    for calibration visualization.

    Args:
        frame: Input video frame in BGR format.
        zone_orig: Zone polygon in original resolution coordinates.

    Returns:
        np.ndarray: Frame with zone polygon and labels overlaid.
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
        (10, 30),
        cv2.FONT_HERSHEY_SIMPLEX,
        FONT_SIZE,
        TEXT_COLOR,
        FONT_THICKNESS,
    )

    # Draw zone label
    cv2.putText(
        frame,
        "Tracking Zone",
        (10, 60),
        cv2.FONT_HERSHEY_SIMPLEX,
        FONT_SIZE,
        ZONE_COLOR,
        FONT_THICKNESS,
    )

    return frame


# =============================================================================
# Video Frame Generation for Streaming
# =============================================================================


def gen_frames() -> Generator[bytes, None, None]:
    """Generate video frames for MJPEG streaming to web client.

    Continuously reads frames from the video capture object, applies zone
    visualization, encodes as JPEG, and yields MJPEG-formatted data for
    streaming. Handles frame read failures by tracking consecutive failures
    and attempting automatic reconnection when threshold is exceeded.

    Yields:
        bytes: MJPEG frame data with multipart boundary markers.

    Note:
        After MAX_CONSECUTIVE_FRAME_FAILURES consecutive failures, the
        function attempts to reconnect to the RTSP stream.
    """
    global cap

    consecutive_failures = 0
    mjpeg_boundary = b"--frame\r\nContent-Type: image/jpeg\r\n\r\n"

    while True:
        # Validate capture object exists
        if cap is None:
            print("❌ Video capture object is None")
            time.sleep(RECONNECT_DELAY_SEC)
            continue

        # Read frame from RTSP stream
        ret, frame = cap.read()

        # Handle frame read failures
        if not ret:
            consecutive_failures += 1

            # Log periodic failures to avoid spam
            if consecutive_failures % FRAME_FAILURE_REPORT_INTERVAL == 0:
                print(f"⚠️ Frame drop #{consecutive_failures}")

            # Attempt reconnection after threshold exceeded
            if consecutive_failures > MAX_CONSECUTIVE_FRAME_FAILURES:
                print("🔄 Reconnecting RTSP... (failure threshold exceeded)")
                try:
                    cap.release()
                    time.sleep(RECONNECT_DELAY_SEC)
                    cap = initialize_video_capture()
                    consecutive_failures = 0
                except RuntimeError as e:
                    print(f"❌ Reconnection failed: {e}")
                    consecutive_failures = 0

            continue

        # Reset failure counter on successful read
        consecutive_failures = 0

        # Draw zone overlay on frame
        frame = draw_zone_on_frame(frame, TRACKING_ZONE_ORIG)

        # Encode frame as JPEG for streaming
        ret, buffer = cv2.imencode(
            ".jpg",
            frame,
            [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY],
        )

        if not ret:
            print("❌ Failed to encode frame as JPEG")
            continue

        # Yield MJPEG frame with proper boundary formatting
        yield mjpeg_boundary + buffer.tobytes() + b"\r\n"


# =============================================================================
# Flask Web Application
# =============================================================================

app = Flask(__name__)

# HTML template for web interface
HTML_TEMPLATE: str = """
<!DOCTYPE html>
<html>
  <head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Driveway Single Zone Calibration</title>
    <style>
      body {
        background-color: #222;
        color: #eee;
        font-family: sans-serif;
        text-align: center;
        margin: 0;
        padding: 20px;
      }
      h1 {
        margin-top: 20px;
        margin-bottom: 10px;
      }
      img {
        border: 2px solid #555;
        margin-top: 20px;
        max-width: 100%;
        height: auto;
      }
      .note {
        margin-top: 10px;
        font-size: 0.9em;
        color: #ccc;
      }
      .container {
        max-width: 1200px;
        margin: 0 auto;
      }
    </style>
  </head>
  <body>
    <div class="container">
      <h1>Driveway Single Zone Calibration</h1>
      <p class="note">
        Yellow polygon = TRACKING_ZONE (from .env, ORIGINAL resolution, scaled to stream).
      </p>
      <img src="/video" alt="Live video stream with zone overlay" />
    </div>
  </body>
</html>
"""


@app.route("/")
def index() -> str:
    """Serve the calibration web page.

    Returns:
        str: HTML page with embedded video stream.

    Route:
        GET /
    """
    return HTML_TEMPLATE


@app.route("/video")
def video() -> Response:
    """Stream live MJPEG video with zone overlay.

    Streams MJPEG video with zone overlay from gen_frames() to the web client.
    The client continuously requests this endpoint to display live video.

    Returns:
        Response: MJPEG multipart stream with proper content-type header.

    Route:
        GET /video

    Content-Type:
        multipart/x-mixed-replace; boundary=frame
    """
    return Response(
        gen_frames(),
        mimetype="multipart/x-mixed-replace; boundary=frame",
    )


# =============================================================================
# Application Entry Point
# =============================================================================


def main() -> int:
    """Main application entry point.

    Initializes video capture, logs configuration, starts Flask web server,
    and handles graceful shutdown with proper resource cleanup.

    Returns:
        int: Exit code (0 for success, 1 for error).

    Exit Codes:
        0: Successful execution and shutdown.
        1: Runtime error (e.g., RTSP connection failed).
    """
    global cap

    try:
        # Initialize video capture and log configuration
        initialize_video_capture()
        log_zone_config()

        # Start Flask web server with SSL support
        # Browser auto-upgrades local connections to HTTPS, so SSL is required
        print(f"Starting Flask server on https://{FLASK_HOST}:{FLASK_PORT}")
        print(
            "📝 Note: Self-signed certificate. Accept the security warning in your browser."
        )
        app.run(
            host=FLASK_HOST,
            port=FLASK_PORT,
            debug=False,
            threaded=True,
            ssl_context="adhoc",
        )

        return 0

    except RuntimeError as e:
        print(f"❌ Runtime error: {e}")
        return 1

    except KeyboardInterrupt:
        print("\n🛑 Shutdown signal received (Ctrl+C)")
        return 0

    finally:
        # Cleanup: release video capture resources
        if cap is not None:
            try:
                cap.release()
                print("✅ Video capture resources released")
            except Exception as e:
                print(f"❌ Error releasing video capture: {e}")


if __name__ == "__main__":
    exit(main())
