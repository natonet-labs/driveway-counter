[![Hailo](https://img.shields.io/badge/Hailo-26%20TOPS-orange?logo=ai&logoColor=white)](https://hailo.ai)
[![RPi5](https://img.shields.io/badge/Raspberry%20Pi-5-E30B5D?logo=raspberrypi&logoColor=white)](https://www.raspberrypi.com)
[![Python](https://img.shields.io/badge/Python-3.13-blue?logo=python&logoColor=white)](https://python.org)

# Hailo Driveway Counter

**15 FPS AI driveway counter — RPi 5 + Hailo-8 26 TOPS — 7% CPU**

Single Python script. Zone-based entry/exit counting. JSON daily reports. Optional live Cloudflare dashboard.

---

## Overview

Turns a Raspberry Pi 5 with the Hailo-8 AI HAT into a vehicle counter that runs 24/7 as a systemd service. YOLOv8m inference runs entirely on the Hailo accelerator — the CPU handles only H.264 decoding and the Python zone logic.

Objects crossing into the tracking zone are counted as entries; objects crossing out are counted as exits. The direction comes from the zone transition itself, so a vehicle reversing out of the driveway is counted as an exit regardless of which side it started on. A horizontal-velocity threshold gates the count so stationary jitter — a parked car's bounding box drifting a pixel or two — never registers.

Counts persist across restarts — if the service restarts mid-day it resumes from the existing report file rather than resetting to zero.

---

## Features

- Any of 80 COCO object classes (vehicles, people, animals, etc.)
- H.264 / H.265 RTSP camera support
- 2-D polygon tracking zone with accurate point-in-polygon containment
- Bidirectional counting with velocity-based direction detection
- Stale track cleanup — parked vehicles don't accumulate indefinitely
- Daily JSON reports with automatic midnight rollover
- Hourly Cloudflare Workers KV sync via non-blocking background thread
- Live dashboard: today's totals, intraday bar chart, 30-day history
- Live zone calibration tool (`web_calib.py`) — overlay polygon on stream before deployment
- `Restart=always` systemd service for unattended 24/7 operation

---

## Hardware

| Component | Spec |
|---|---|
| Raspberry Pi 5 | 4 GB minimum, 8 GB recommended |
| Hailo AI HAT | Hailo-8, 26 TOPS (PCIe) |
| Camera | Any RTSP camera with H.264 or H.265 output |
| Storage | SSD recommended (`/mnt/ssd`) |

---

## Software Requirements

- Raspberry Pi OS 64-bit (Debian Trixie)
- `hailo-all` Debian package (HailoRT + GStreamer plugins + Python bindings)
- `hailo-rpi5-examples` — run `./install.sh` once to install the YOLOv8m model and YOLO postprocess library
- Python 3.13 system `python3`

A full TAPPAS source build is **not required**.

---

## Quick Start

```bash
# 1. Install Hailo system packages (one-time)
cd /mnt/ssd/projects
git clone https://github.com/hailo-ai/hailo-rpi5-examples.git
cd hailo-rpi5-examples && ./install.sh

# 2. Clone this repo
cd /mnt/ssd/projects
git clone https://github.com/natonet-labs/driveway-counter.git
cd driveway-counter

# 3. Python environment
python3 -m venv .venv --system-site-packages
echo "/usr/lib/python3/dist-packages" \
  > .venv/lib/python3.13/site-packages/system_packages.pth
source .venv/bin/activate
pip install -r requirements.txt

# 4. Configure camera
cp .env.example .env
nano .env

# 5. Set up model symlink and verify libraries
./setup_hailo.sh

# 6. Blacklist the Hailo-10 driver to prevent conflicts after kernel updates
echo "blacklist hailo1x_pci" | sudo tee /etc/modprobe.d/hailo-blacklist.conf
sudo update-initramfs -u

# 7. Calibrate zone against live stream
python web_calib.py   # open http://rpi.local:8081

# 8. Run
python3 driveway_counter_hailo.py
```

For full setup instructions including systemd service configuration and Cloudflare dashboard deployment, see [`docs/setup-guide.md`](docs/setup-guide.md).

---

## Configuration

All configuration lives in `.env`. Copy `.env.example` to get started.

```bash
# Camera
USERNAME=your_username
PASSWORD=your_password
IPADDRESS=192.168.1.100
CHANNEL=1
SUBTYPE=1          # 1 = H.264 substream (recommended), 0 = H.265 main stream

# Resolution — must match the selected subtype's native output
ORIG_W=704
ORIG_H=480
IMG_W=704
IMG_H=480

# Detection
CONF_THRESH=0.65   # Lower = more detections, more false positives

# Zone — 4-point polygon in original resolution (top-left, top-right, bottom-right, bottom-left)
TRACKING_ZONE=[[380,3],[480,3],[480,460],[380,460]]

# Reports
REPORT_DIR=./reports

# Cloudflare (optional — leave blank to disable)
WORKER_URL=
CLOUDFLARE_TOKEN=
```

**Use `SUBTYPE=1`.** The H.264 substream at 704×480 runs at 15 FPS using 7% CPU. The H.265 main stream at 4K drives the Pi 5 to 100% CPU and is not viable for continuous operation.

---

## Zone Calibration

Run `web_calib.py` to adjust the tracking zone visually against the live stream before deploying the counter:

```bash
source .venv/bin/activate
python web_calib.py
# Open http://rpi.local:8081
```

The yellow polygon shows the current `TRACKING_ZONE` from `.env` scaled to the stream resolution. Edit `.env` and refresh to update. The zone scales automatically to any processing resolution.

---

## Daily Reports

```bash
cat reports/driveway_$(date +%Y-%m-%d).json
```

```json
{
  "date": "2026-03-21",
  "entries": 15,
  "exits": 12
}
```

Counts resume from disk on restart — a mid-day service restart does not reset the day's totals.

---

## Cloudflare Dashboard (Optional)

Metrics sync hourly to Cloudflare Workers KV. The dashboard shows today's totals, an intraday bar chart, and 30-day history — accessible from anywhere without opening the Pi to the internet.

See the [Cloudflare setup section in setup-guide.md](docs/setup-guide.md#cloudflare-metrics-dashboard-optional) for deployment instructions.

---

## Performance

| Metric | Value |
|---|---|
| FPS | 15 |
| CPU | ~7% |
| RAM | ~190 MB |
| Temperature | ~50°C |
| Hailo utilisation | 30–40% |

**On Hailo-8:** YOLOv8m inference, YOLO NMS, letterbox preprocessing, Kalman tracking  
**On CPU:** H.264 decode, format conversion, Python zone logic, background upload thread

---

## Troubleshooting

See [`docs/troubleshooting.md`](docs/troubleshooting.md) for the full guide covering:

- `HAILO_OUT_OF_PHYSICAL_DEVICES(74)` — three distinct causes including the most common one: the systemd service is already running and holding the device
- Driver conflicts after kernel updates (`hailo1x_pci` vs `hailo_pci`)
- Kernel driver rebuild procedure after a kernel version change
- No detections / zero FPS
- Python venv / Hailo module import failures
- RTSP connection failures

---

## Demo

https://github.com/user-attachments/assets/208b9c0f-3f4d-4b04-af89-2d2af14d28bb

---

## Notes on Hailo / TAPPAS

This application does not require a full TAPPAS source build. It depends only on:

- Hailo Debian packages (`hailo-all`, `hailo-tappas-core`)
- The YOLOv8m HEF model and `libyolo_hailortpp_postprocess.so` installed by `hailo-rpi5-examples`
- `libwhole_buffer.so`, passed to `hailocropper` via `so-path` (ships with `hailo-tappas-core`)

**GStreamer 1.26+ requires `so-path` on `hailocropper`.** Earlier versions handled the
no-`so-path` passthrough mode correctly; 1.26.2 segfaults during `pipeline.set_state(PLAYING)`
without it. The library is present on a standard `hailo-all` install via `hailo-tappas-core` — a
full TAPPAS source build is still not needed. See
[`docs/troubleshooting.md`](docs/troubleshooting.md#issue-4-hailocropper-segfault-on-gstreamer-126)
for the full diagnosis.

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

---

## License

MIT — see [LICENSE.md](LICENSE.md).

---

## Acknowledgments

- [Hailo AI](https://hailo.ai/developer-zone/)
- [hailo-rpi5-examples](https://github.com/hailo-ai/hailo-rpi5-examples)
- [TAPPAS / GStreamer Hailo plugins](https://github.com/hailo-ai/tappas)
- [Ultralytics YOLOv8](https://github.com/ultralytics/ultralytics)
- [Cloudflare Workers](https://workers.cloudflare.com/)
