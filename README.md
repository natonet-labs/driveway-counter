[![Hailo](https://img.shields.io/badge/Hailo-26%20TOPS-orange?logo=ai&logoColor=white)] [![RPi5](https://img.shields.io/badge/Raspberry%20Pi-5-E30B5D?logo=raspberrypi&logoColor=white)] [![Python](https://img.shields.io/badge/Python-3.13-blue?logo=python&logoColor=white)]

# Hailo Driveway Counter

**15 FPS AI driveway counter on RPi 5 + Hailo 26 TOPS**  
*Single script, 7% CPU, zone tracking, JSON reports, live Cloudflare dashboard*

## Overview

Turn your Raspberry Pi 5 into a high-performance AI security camera with this single-script solution. It delivers reliable driveway monitoring at 15 FPS using just 7% CPU and ~190MB RAM, with optional live metrics synced to a Cloudflare Workers dashboard viewable from anywhere.

### What makes it stand out

- Hailo AI HAT acceleration (90+ inferences/sec) eliminates GPU dependency.
- Unique ID tracking prevents double-counting across frame boundaries.
- Configurable polygon zones via live web interface — no manual coordinate math.
- Midnight JSON reports: `{"date": "2026-02-21", "entries": 333, "exits": 317}` with support for any of 80 COCO object classes.
- **Hourly Cloudflare sync** via background thread — never blocks inference.
- **Live dashboard** showing today's totals, intraday hourly chart, and 30-day history.
- Designed for home labs wanting production-grade computer vision without cloud dependency or complex multi-container setups. Deployable as a systemd service for 24/7 operation.

## Features

- Real-time object detection: any of 80 COCO classes (people, vehicles, animals, etc.).
- H.264 / H.265 RTSP camera support.
- Low-res at ~7% CPU, ~50°C, ~190MB RAM, ~15 FPS performance on RPi 5 + Hailo-8.
- Single Python script deployment (`driveway_counter_hailo.py`).
- Daily JSON reports with midnight rollover.
- Hourly metrics upload to Cloudflare Workers KV (non-blocking background thread).
- Web dashboard: live today summary, intraday bar chart, 30-day line chart.

## Demo

https://github.com/user-attachments/assets/208b9c0f-3f4d-4b04-af89-2d2af14d28bb

Terminal output showing live detection, tracking, and zone counting. Watch real-time updates: any COCO class objects entering and exiting zones, unique tracking IDs, FPS performance, and cumulative counts.

## Hardware Requirements

- Raspberry Pi 5 (4GB+ recommended).
- Hailo-8 AI HAT (26 TOPS, PCIe variant used by Raspberry Pi AI Kit).
- RTSP-compatible IP camera (H.264 or H.265).
- SSD storage (recommended for performance).

## Software Requirements

- Raspberry Pi OS (Debian Trixie, 64-bit).
- Hailo software stack:
  - `hailo-all` installed from Hailo’s Debian repo (provides HailoRT, GStreamer plugins, Python bindings).
  - `hailo-rpi5-examples` cloned locally and `./install.sh` run once (installs YOLOv8m HEF model and YOLO postprocess `.so`).[file:361][web:341]
- Python 3.13 (system `python3`).
- GStreamer 1.0 with Hailo plugins (`hailonet`, `hailofilter`, `hailocropper`, `hailotracker`, `hailoaggregator`).[file:335][web:351]

## Quick Start

```bash
# 1. Install Hailo RPi5 examples (one-time)
cd /mnt/ssd/projects
git clone https://github.com/hailo-ai/hailo-rpi5-examples.git
cd hailo-rpi5-examples && ./install.sh

# 2. Clone this repository
cd /mnt/ssd/projects
git clone https://github.com/n-vo/driveway-counter.git
cd driveway-counter

# 3. Setup Python environment (Python 3.13 with system Hailo bindings)
python3 -m venv .venv --system-site-packages
# Make sure venv can see /usr/lib/python3/dist-packages (where hailo.cpython-313-*.so lives)
echo "/usr/lib/python3/dist-packages" > .venv/lib/python3.13/site-packages/system_packages.pth
source .venv/bin/activate
pip install -r requirements.txt

# 4. Configure camera and optional Cloudflare metrics
cp .env.example .env
nano .env  # Add your RTSP credentials and Cloudflare tokens

# 5. Setup Hailo resources (model symlink + YOLO postprocess library check)
./setup_hailo.sh

# 6. Run
python driveway_counter_hailo.py
```

If anything fails related to Hailo drivers, Hailo Python module, or GStreamer pipeline, see:

- [`docs/TROUBLESHOOTING.md`](docs/TROUBLESHOOTING.md) – full driver + stack recovery guide.

## Cloudflare Dashboard (Optional)

The counter can sync metrics hourly to a Cloudflare Workers KV store and serve a live dashboard at your Worker URL. This requires a free Cloudflare account.

**Dashboard features:**

- Today's entry/exit totals updated every hour.
- Intraday bar chart showing activity by hour.
- 30-day history line chart.

See the [Cloudflare Setup section in SETUP_GUIDE.md](docs/SETUP_GUIDE.md#-cloudflare-metrics-dashboard-optional) for full deployment instructions.

## Notes on Hailo / TAPPAS

- This application **does not require** a full TAPPAS source build.
- It only depends on:
  - Hailo’s Debian packages (`hailo-all`, `hailo-tappas-core`).
  - The YOLOv8m HEF model and `libyolo_hailortpp_postprocess.so` installed by `hailo-rpi5-examples` / `hailo-all`.
  - `hailocropper` running without an external `so-path` (no `libwhole_buffer.so` needed).[file:335][web:344]

If you later modify the pipeline to use custom croppers or postprocess libraries under `post_processes/`, you may need a full TAPPAS source install. For the stock driveway-counter pipeline, the Debian packages and examples are sufficient.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for details.

## License

MIT – see [LICENSE.md](LICENSE.md).

## Acknowledgments

- [Hailo AI Documentation](https://hailo.ai/developer-zone/)
- [Hailo RPi5 Examples](https://github.com/hailo-ai/hailo-rpi5-examples)
- [GStreamer Hailo Plugin / TAPPAS](https://github.com/hailo-ai/tappas)
- [Ultralytics YOLOv8](https://github.com/ultralytics/ultralytics)
- [YOLOv8 Model Zoo](https://github.com/hailo-ai/hailo_model_zoo)
- [Cloudflare Workers](https://workers.cloudflare.com/)
