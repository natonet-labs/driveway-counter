# Hailo Driveway Counter

AI-powered vehicle detection and counting system using Hailo 26 TOPS AI HAT+ on Raspberry Pi 5.

## Overview

Real-time driveway monitoring that counts objects entering and exiting using YOLOv8 object detection accelerated by Hailo AI hardware.

## Features

- Real-time detection: people, cars, cats, dogs
- Tracking zone counting with configurable polygons
- Object tracking with unique IDs (Hailo tracker)
- Daily JSON reports with midnight rollover
- Hailo hardware acceleration (90%+ inference)
- H.264 low-res stream decoding
- 7% CPU, 50°C, 190MB RAM, 15 FPS
- Single Python script
- Live web zone calibration

## Hardware Requirements

- Raspberry Pi 5 (4GB+ recommended)
- Hailo AI HAT+ (26 TOPS)
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
git clone https://github.com/YOUR_USERNAME/driveway-counter.git
cd driveway-counter

# 3. Setup Python environment
python3 -m venv .venv --system-site-packages
source .venv/bin/activate
pip install -r requirements.txt

# 4. Configure camera
cp .env.example .env
nano .env  # Add your RTSP credentials

# 5. Setup Hailo resources
./setup_hailo.sh

# 6. Run
python driveway_counter_hailo.py
```

For detailed installation, troubleshooting, and systemd setup, see [SETUP_GUIDE.md](SETUP_GUIDE.md)

## Resources

- [Hailo AI Documentation](https://hailo.ai/developer-zone/)
- [Hailo RPi5 Examples](https://github.com/hailo-ai/hailo-rpi5-examples)
- [YOLOv8 Model Zoo](https://github.com/hailo-ai/hailo_model_zoo)
- [GStreamer Hailo Plugin](https://github.com/hailo-ai/tappas)

---