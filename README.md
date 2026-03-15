[![Hailo](https://img.shields.io/badge/Hailo-26%20TOPS-orange?logo=ai&logoColor=white)] [![RPi5](https://img.shields.io/badge/Raspberry%20Pi-5-E30B5D?logo=raspberrypi&logoColor=white)] [![Python](https://img.shields.io/badge/Python-3.11+-blue?logo=python&logoColor=white)]

# Hailo Driveway Counter

**15 FPS AI driveway counter on RPi 5 + Hailo 26 TOPS**  
*Single script, 7% CPU, zone tracking, JSON reports, live Cloudflare dashboard*

## Overview

Turn your Raspberry Pi 5 into a high-performance AI security camera with this single-script solution. It delivers reliable driveway monitoring at 15 FPS using just 7% CPU and 190MB RAM, with optional live metrics synced to a Cloudflare Workers dashboard viewable from anywhere.

### What makes it stand out:

- Hailo AI HAT acceleration (90+ inferences/sec) eliminates GPU dependency
- Unique ID tracking prevents double-counting across frame boundaries
- Configurable polygon zones via live web interface — no manual coordinate math
- Midnight JSON reports: `{"date": "2026-02-21", "entries": 333, "exits": 317}` with support for any of 80 COCO object classes
- **Hourly Cloudflare sync** via background thread — never blocks inference
- **Live dashboard** showing today's totals, intraday hourly chart, and 30-day history
- Perfect for home labs wanting production-grade computer vision without cloud dependency or complex multi-container setups. Deployable as systemd service for 24/7 operation.

## Features

- Real-time object detection: any of 80 COCO classes (people, vehicles, animals, etc.)
- H.264 low-res stream decoding (RTSP-compatible cameras)
- 7% CPU, 50°C, 190MB RAM, 15 FPS performance
- Single Python script deployment
- Daily JSON reports with midnight rollover
- Hourly metrics upload to Cloudflare Workers KV (non-blocking background thread)
- Web dashboard: live today summary, intraday bar chart, 30-day line chart

## Demo

https://github.com/user-attachments/assets/208b9c0f-3f4d-4b04-af89-2d2af14d28bb

Terminal output showing live detection, tracking, and zone counting. Watch real-time updates: any COCO class objects entering and exiting zones, unique tracking IDs, FPS performance, and cumulative counts.

## Hardware Requirements

- Raspberry Pi 5 (4GB+ recommended)
- Hailo AI HAT (26 TOPS)
- RTSP-compatible IP camera (H.264 or H.265)
- SSD storage (recommended for performance)

## Quick Start

```bash
# 1. Install system dependencies (one-time)
cd /mnt/ssd/projects
git clone https://github.com/hailo-ai/hailo-rpi5-examples.git
cd hailo-rpi5-examples && ./install.sh

# 2. Clone this repository
cd /mnt/ssd/projects
git clone https://github.com/n-vo/driveway-counter.git
cd driveway-counter

# 3. Setup Python environment
python3 -m venv .venv --system-site-packages
source .venv/bin/activate
pip install -r requirements.txt

# 4. Configure camera and optional Cloudflare metrics
cp .env.example .env
nano .env  # Add your RTSP credentials and Cloudflare tokens

# 5. Setup Hailo resources
./setup_hailo.sh

# 6. Run
python driveway_counter_hailo.py
```

For detailed installation, troubleshooting, Cloudflare setup, and systemd configuration, see [SETUP_GUIDE.md](SETUP_GUIDE.md).

## Cloudflare Dashboard (Optional)

The counter can sync metrics hourly to a Cloudflare Workers KV store and serve a live dashboard at your Worker URL. This requires a free Cloudflare account.

**Dashboard features:**
- Today's entry/exit totals updated every hour
- Intraday bar chart showing activity by hour
- 30-day history line chart

See the [Cloudflare Setup section in SETUP_GUIDE.md](SETUP_GUIDE.md#-cloudflare-metrics-dashboard-optional) for full deployment instructions.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for details.

## License

MIT – see [LICENSE.md](LICENSE.md) file.

## Acknowledgments

- [Hailo AI Documentation](https://hailo.ai/developer-zone/)
- [Hailo RPi5 Examples](https://github.com/hailo-ai/hailo-rpi5-examples)
- [GStreamer Hailo Plugin](https://github.com/hailo-ai/tappas)
- [Ultralytics YOLOv8](https://github.com/ultralytics/ultralytics)
- [YOLOv8 Model Zoo](https://github.com/hailo-ai/hailo_model_zoo)
- [Cloudflare Workers](https://workers.cloudflare.com/)

---