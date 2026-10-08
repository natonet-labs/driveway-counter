# Hailo Driveway Counter — Complete Setup Guide

**Raspberry Pi 5 + Hailo-8 AI HAT (26 TOPS)**

---

## Prerequisites

- Raspberry Pi 5 (4 GB+ RAM; 8 GB recommended)
- Hailo-8 AI HAT (26 TOPS) installed and seated on the PCIe connector
- RTSP IP camera accessible on the local network
- SSD mounted at `/mnt/ssd` (recommended — reduces SD card wear)
- Raspberry Pi OS 64-bit (Debian Trixie or later)

---

## Step 1 — Install Hailo System Packages (one-time)

```bash
cd /mnt/ssd/projects
git clone https://github.com/hailo-ai/hailo-rpi5-examples.git
cd hailo-rpi5-examples
./install.sh

# Verify
gst-inspect-1.0 hailonet | head -5
ls /usr/local/hailo/resources/models/hailo8/yolov8m.hef
```

This installs:
- `hailort` runtime and Python bindings
- GStreamer plugins: `hailonet`, `hailofilter`, `hailotracker`, `hailocropper`, `hailoaggregator`
- Pre-compiled `yolov8m.hef` model
- YOLO postprocessing library (`libyolo_hailortpp_postprocess.so`)

---

## Step 2 — Clone This Repository

```bash
cd /mnt/ssd/projects
git clone https://github.com/natonet-labs/driveway-counter.git
cd driveway-counter
```

---

## Step 3 — Create the Python Virtual Environment

```bash
# --system-site-packages gives the venv access to the system-installed
# Hailo Python module and GStreamer bindings, which cannot be pip-installed.
python3 -m venv .venv --system-site-packages

# Explicitly add dist-packages to the venv's search path
echo "/usr/lib/python3/dist-packages" \
  > .venv/lib/python3.13/site-packages/system_packages.pth

source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

# Verify critical imports
python3 -c "import hailo; print('✅ Hailo:', hailo.__file__)"
python3 -c "import gi; gi.require_version('Gst','1.0'); from gi.repository import Gst; print('✅ GStreamer OK')"
```

---

## Step 4 — Configure the Environment

```bash
cp .env.example .env
nano .env
```

Key settings:

| Variable | Description |
|---|---|
| `USERNAME` / `PASSWORD` | RTSP camera credentials |
| `IPADDRESS` | Camera IP |
| `SUBTYPE` | `1` = H.264 substream (~7% CPU), `0` = H.265 main stream (~100% CPU — avoid) |
| `ORIG_W` / `ORIG_H` | Native resolution of the selected stream |
| `IMG_W` / `IMG_H` | Processing resolution (match `ORIG_W`/`ORIG_H` for substream) |
| `CONF_THRESH` | Detection confidence threshold — `0.65` tuned default, see [tuning guide](tuning-guide.md) |
| `TRACKING_ZONE` | 4-point polygon in original resolution coordinates |

**Use `SUBTYPE=1` (H.264 substream).** The H.265 main stream at 4K drives CPU to 100% on the Pi 5. The substream at 704×480 runs at 15 FPS using 7% CPU.

---

## Step 5 — Set Up Hailo Resources

```bash
./setup_hailo.sh
```

This creates a symlink from `models/yolov8m.hef` to the system-installed model and verifies the YOLO postprocess library is present.

```bash
# Verify
ls -lh models/yolov8m.hef
# Expected: models/yolov8m.hef -> /usr/local/hailo/resources/models/hailo8/yolov8m.hef
```

---

## Step 6 — Prevent Driver Conflicts on Future Kernel Updates

A system update may load `hailo1x_pci` (the Hailo-10 driver) alongside `hailo_pci` (the Hailo-8 driver). When both are loaded simultaneously the app fails immediately with `HAILO_OUT_OF_PHYSICAL_DEVICES(74)`. Blacklist the Hailo-10 driver now so it never loads on your system.

```bash
echo "blacklist hailo1x_pci" | sudo tee /etc/modprobe.d/hailo-blacklist.conf
sudo update-initramfs -u
```

Verify after the next reboot:
```bash
lsmod | grep hailo
# Expected: hailo_pci only — hailo1x_pci must not appear
```

---

## Step 7 — Verify GStreamer Pipeline Elements

```bash
gst-inspect-1.0 hailonet
gst-inspect-1.0 hailofilter
gst-inspect-1.0 hailotracker
gst-inspect-1.0 hailocropper
gst-inspect-1.0 hailoaggregator

ls -l /usr/local/hailo/resources/so/libyolo_hailortpp_postprocess.so
ls -l /usr/local/hailo/resources/barcode_labels/coco_80.json
```

If any GStreamer elements are missing, re-run `./install.sh` from the `hailo-rpi5-examples` directory.

---

## Step 8 — Calibrate the Tracking Zone

The tracking zone is a 4-point polygon defined in **original camera resolution** coordinates. Use the included web calibration tool to dial in the coordinates visually against the live stream before running the counter.

```bash
source .venv/bin/activate
python web_calib.py
```

Open `http://rpi.local:8081` in a browser. The yellow polygon shows the current `TRACKING_ZONE` from `.env` overlaid on the live stream. Edit the coordinates in `.env` and refresh the page to update.

The zone should cover the driveway entrance — objects crossing the left or right boundary of the polygon are counted as entries or exits depending on direction of travel.

```
Frame: 704×480 (substream)
┌──────────────────────────┐
│        driveway          │
│     ┌──────────┐         │
│     │  ZONE    │         │
│     │ entry/   │         │
│     │ exit     │         │
│     └──────────┘         │
└──────────────────────────┘
```

---

## Step 9 — Test the RTSP Connection

```bash
# URL-encode any special characters in the password manually if needed
ffmpeg -rtsp_transport tcp \
  -i "rtsp://USERNAME:PASSWORD@IPADDRESS:554/cam/realmonitor?channel=1&subtype=1" \
  -frames:v 1 -y /tmp/test.jpg

# Check the captured frame
ls -lh /tmp/test.jpg
```

---

## Step 10 — Run the Counter

```bash
source .venv/bin/activate
python3 driveway_counter_hailo.py
```

Expected startup output:
```
✅ Hailo: /usr/lib/python3/dist-packages/hailo.cpython-313-aarch64-linux-gnu.so
📂 Resumed from existing report: entries=0 exits=0
🚗 Driveway Counter (Hailo-8 26 TOPS)
📍 Tracking Zone (processing res): [[380, 3], [480, 3], [480, 460], [380, 460]]
☁️  Cloudflare upload thread started (interval=3600s)
🚀 Pipeline running — press Ctrl+C to stop
✅ Pipeline running, processing frames...
📊 Entries=0 Exits=0 | FPS=15 | Tracks=2
```

Stop with `Ctrl+C`.

---

## Step 11 — Install as a Systemd Service

### Create the service file

```bash
sudo nano /etc/systemd/system/driveway-counter.service
```

```ini
[Unit]
Description=Hailo Driveway Counter
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=pi
WorkingDirectory=/mnt/ssd/projects/driveway-counter
Environment="PATH=/mnt/ssd/projects/driveway-counter/.venv/bin:/usr/local/bin:/usr/bin:/bin"
Environment="GST_PLUGIN_PATH=/usr/lib/aarch64-linux-gnu/gstreamer-1.0"
ExecStart=/mnt/ssd/projects/driveway-counter/.venv/bin/python3 driveway_counter_hailo.py
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

### Enable and start

```bash
sudo systemctl daemon-reload
sudo systemctl enable driveway-counter.service
sudo systemctl start driveway-counter.service
sudo systemctl status driveway-counter.service
```

### View live logs

```bash
sudo journalctl -u driveway-counter.service -f
```

### Service management

```bash
sudo systemctl stop driveway-counter.service
sudo systemctl restart driveway-counter.service
sudo systemctl disable driveway-counter.service
sudo journalctl -u driveway-counter.service -n 100
```

> **Important:** Always stop the service before running the app manually.
> The service uses `Restart=always` and will respawn within 10 seconds after
> any crash, immediately reclaiming `/dev/hailo0`. Any manual launch while
> the service is active will fail with `HAILO_OUT_OF_PHYSICAL_DEVICES(74)`.
>
> ```bash
> sudo systemctl stop driveway-counter.service
> python3 driveway_counter_hailo.py
> # When done:
> sudo systemctl start driveway-counter.service
> ```

---

## Cloudflare Metrics Dashboard (Optional)

The counter syncs entry/exit totals to Cloudflare Workers KV every hour via a background thread. This never blocks the inference pipeline.

### How it works

A background thread wakes up every hour, snapshots the current daily totals, and POSTs to your Cloudflare Worker. Two KV entries are written per upload:
- `driveway:YYYY-MM-DD` — running daily total (overwritten each hour)
- `hourly:YYYY-MM-DD:HH` — intraday snapshot (expires after 48 h)

The intraday chart will be empty until the first upload, which occurs one hour after the counter starts.

### Step A — Create a Cloudflare Worker

1. Log in to [dash.cloudflare.com](https://dash.cloudflare.com) → **Workers & Pages**
2. **Create** → **Create Worker** → name it `driveway-metrics` → **Deploy**
3. **Edit code** → replace the default with `cloudflare/index.ts` from this repo → **Deploy**

### Step B — Create a KV Namespace

1. **Workers & Pages** → **KV** → **Create a namespace** → name it `DRIVEWAY_METRICS`
2. Go to your worker → **Settings** → **Bindings** → **Add** → **KV Namespace**
3. Variable name: `DRIVEWAY_METRICS` → select the namespace → **Save and deploy**

### Step C — Create an API Token

1. [dash.cloudflare.com/profile/api-tokens](https://dash.cloudflare.com/profile/api-tokens)
2. **Create Token** → **Create Custom Token**
3. Permissions: `Account` → `Workers KV Storage` → `Edit`
4. Copy the token — shown only once

### Step D — Add to .env

```bash
WORKER_URL=https://driveway-metrics.YOUR_SUBDOMAIN.workers.dev/api/metrics
CLOUDFLARE_TOKEN=your_token_here
```

### Dashboard endpoints

| Endpoint | Description |
|---|---|
| `GET /today` | Today's latest entry/exit totals |
| `GET /hourly` | Today's intraday snapshots by hour |
| `GET /dashboard` | Last 30 days of daily totals |
| `POST /api/metrics` | Upload a snapshot (used by the Pi) |

---

## Daily Reports

Reports are saved to `./reports/` as JSON:

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

A new file is created at midnight. The final day's totals are also uploaded to Cloudflare at rollover if configured. If the service restarts mid-day, counts resume from the existing report file — nothing is lost.

---

## Performance Reference

| Metric | Value |
|---|---|
| FPS | 15 (SUBTYPE=1, H.264 substream) |
| CPU | ~7% |
| RAM | ~190 MB |
| Temperature | ~50°C |
| Hailo utilisation | 30–40% |
| Storage (reports) | ~2–5 KB/day |

**On Hailo:** YOLOv8m inference, YOLO NMS postprocessing, letterbox preprocessing, Kalman filter tracking  
**On CPU:** H.264 decode, format conversion, Python zone logic, Cloudflare upload thread

---

## Update Instructions

```bash
sudo systemctl stop driveway-counter.service
cd /mnt/ssd/projects/driveway-counter
git pull origin main
source .venv/bin/activate
pip install -r requirements.txt
sudo systemctl start driveway-counter.service
sudo journalctl -u driveway-counter.service -f
```

---

## Project Structure

```
driveway-counter/
├── driveway_counter_hailo.py   # Main application
├── web_calib.py                # Live zone calibration tool
├── setup_hailo.sh              # Model symlink + library check
├── requirements.txt
├── .env                        # Your config (not in git)
├── .env.example                # Template
├── .gitignore
├── README.md
├── CONTRIBUTING.md
├── models/
│   └── yolov8m.hef            # Symlink → system model (30 MB)
├── reports/                    # Daily JSON reports (not in git)
└── docs/
    ├── setup-guide.md          # This file
    └── troubleshooting.md
```

---

## What Goes in Git

**Commit:**
- `driveway_counter_hailo.py`, `web_calib.py`, `setup_hailo.sh`
- `docs/`, `README.md`, `CONTRIBUTING.md`
- `requirements.txt`, `.env.example`, `.gitignore`

**Do not commit:**
- `.env` — contains passwords and API tokens
- `.venv/` — environment is system-dependent
- `reports/` — runtime data
- `models/*.hef` — large binary, symlink only
