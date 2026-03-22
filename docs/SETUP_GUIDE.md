# Hailo Driveway Counter Complete Setup Guide
**Self-contained application using Hailo 26 TOPS AI HAT+ on Raspberry Pi 5**

## Prerequisites
- Raspberry Pi 5 with Hailo AI HAT+ (26 TOPS) installed
- Hailo system packages installed (run hailo-rpi5-examples install.sh first)
- RTSP camera accessible on network
- SSD mounted at `/mnt/ssd` (recommended for performance)

---

## 🚀 Quick Start (Fresh Install)

### Step 1: Install Hailo System Packages (One-time, System-wide)
```bash
# Clone official Hailo examples (for system setup only)
cd /mnt/ssd/projects
git clone https://github.com/hailo-ai/hailo-rpi5-examples.git
cd hailo-rpi5-examples

# Run official installer (installs GStreamer plugins, libraries, models)
./install.sh

# Verify installation
gst-inspect-1.0 hailonet | head -5
ls -l /usr/local/hailo/resources/
```

**This installs:**
- `hailort` (Hailo runtime)
- GStreamer Hailo plugins (`hailonet`, `hailofilter`, `hailotracker`, `hailocropper`, `hailoaggregator`)
- YOLO postprocessing libraries
- Pre-compiled HEF models (including yolov8m.hef)

---

### Step 2: Clone Driveway Counter Repository
```bash
cd /mnt/ssd/projects
git clone https://github.com/YOUR_USERNAME/driveway-counter.git
cd driveway-counter
```

---

### Step 3: Create Python Virtual Environment
```bash
# IMPORTANT: Use --system-site-packages to access system Hailo packages
python3 -m venv .venv --system-site-packages

# Activate
source .venv/bin/activate

# Install Python dependencies
pip install --upgrade pip
pip install python-dotenv numpy==1.26.4 opencv-python==4.11.0.86 requests

# Verify critical imports
python3 -c "import gi; gi.require_version('Gst', '1.0'); from gi.repository import Gst; print('✅ GStreamer OK')"
python3 -c "import hailo; print('✅ Hailo Python OK:', hailo.__file__)"
```

**Why `--system-site-packages`?**
- The `hailo` Python module is installed system-wide (not via pip)
- GStreamer Python bindings (PyGObject/gi) are also system packages
- Your venv needs access to these while keeping other deps isolated

---

### Step 4: Create Environment Configuration
```bash
# Create .env file with your camera settings
cat > .env << 'EOF'
# Camera credentials
USERNAME=rtsp_username
PASSWORD=rtsp_password
IPADDRESS=192.168.1.100
CHANNEL=1
SUBTYPE=1

# Original camera resolution (depends on subtype=1 or 0)
ORIG_W=704
ORIG_H=480

# Processing resolution
IMG_W=704
IMG_H=480

# Detection confidence threshold (lower = more detections, more false positives)
CONF_THRESH=0.30

# SINGLE TRACKING ZONE (top-left, top-right, bottom-right, bottom-left)
TRACKING_ZONE=[[x,y],[x,y],[x,y],[x,y]]

# Reports output directory
REPORT_DIR=./reports

ISDEBUG=False

# Cloudflare metrics (optional — leave blank to disable)
WORKER_URL=
CLOUDFLARE_TOKEN=
EOF

# Edit with your actual camera credentials
nano .env
```

**Important:** If your password contains special characters, they'll be automatically URL-encoded by the script.

---

### Step 5: Create Symlinks to Hailo Resources

The Hailo system installation (from Step 1) includes pre-compiled YOLOv8 models at `/usr/local/hailo/resources/models/hailo8/`. Instead of duplicating these large files, we'll create symlinks.

```bash
# Run the setup script
./setup_hailo.sh
```

**Expected output:**
```
🔧 Setting up Hailo Driveway Counter...
📦 Creating symlink to system yolov8m.hef...
✅ Model linked: /usr/local/hailo/resources/models/hailo8/yolov8m.hef
✅ YOLO postprocess library found
✅ Setup complete!

📋 Next steps:
   1. Copy .env.example to .env and configure your camera
   2. Run: source .venv/bin/activate
   3. Run: python driveway_counter_hailo.py
```

**This creates:**
- `models/yolov8m.hef` → symlink to `/usr/local/hailo/resources/models/hailo8/yolov8m.hef` (30MB)
- `reports/` directory for daily JSON reports

**Verify symlink:**
```bash
ls -lh models/yolov8m.hef
# Should show: models/yolov8m.hef -> /usr/local/hailo/resources/models/hailo8/yolov8m.hef
```

**Why symlinks?**
- ✅ Saves 30MB disk space per project
- ✅ Always uses the latest system model
- ✅ No model duplication across projects
- ✅ Models persist even if you delete/recreate project

**Note:** The system models are installed by `hailo-rpi5-examples/install.sh` and remain on your Pi until you explicitly uninstall Hailo packages.

---

### Step 6: Verify GStreamer Pipeline Elements
```bash
# Check all required Hailo GStreamer elements
gst-inspect-1.0 hailonet      # Neural network inference
gst-inspect-1.0 hailofilter   # YOLO postprocessing (NMS, class filtering)
gst-inspect-1.0 hailotracker  # Object tracking with IDs
gst-inspect-1.0 hailocropper  # Input preprocessing/cropping
gst-inspect-1.0 hailoaggregator  # Multi-branch pipeline aggregation

# Check YOLO postprocess library
ls -l /usr/local/hailo/resources/so/libyolo_hailortpp_postprocess.so

# Check COCO labels config
ls -l /usr/local/hailo/resources/barcode_labels/coco_80.json
```

**If any are missing:** Re-run hailo-rpi5-examples `./install.sh`

---

### Prevent driver conflicts on future kernel updates

The system may load `hailo1x_pci` (Hailo-10 driver) alongside the correct
`hailo_pci` (Hailo-8 driver) after a kernel update. Blacklist it now to
prevent `HAILO_OUT_OF_PHYSICAL_DEVICES(74)` errors in the future:
```bash
echo "blacklist hailo1x_pci" | sudo tee /etc/modprobe.d/hailo-blacklist.conf
sudo update-initramfs -u
```

Verify only the correct driver is present:
```bash
lsmod | grep hailo
# Expected: hailo_pci   (hailo1x_pci should NOT appear)
```

### Step 7: Test RTSP Camera Connection
```bash
# Test with ffmpeg (optional but recommended)
source .env
ffmpeg -rtsp_transport tcp -i "rtsp://${USERNAME}:${PASSWORD}@${IPADDRESS}:554/cam/realmonitor?channel=${CHANNEL}&subtype=${SUBTYPE}" -frames:v 1 -y test.jpg
```

**Note:** If password has special characters, url-encode them.

---

### Step 8: Run Driveway Counter
```bash
# Activate venv (if not already active)
source .venv/bin/activate

# Run the application
python driveway_counter_hailo.py
```

**Expected output:**
```
[INFO] 🚗 Driveway Counter (Hailo 26 TOPS)
[INFO] 📍 Tracking Zone: [[380, 3], [480, 3], [480, 460], [380, 460]]
[INFO] ☁️  Cloudflare upload thread started (interval=3600s)
[INFO] ✅ Model: ./models/yolov8m.hef
[INFO] 📊 Confidence threshold: 0.30
[INFO] 🚀 Starting GStreamer pipeline...
[INFO] ✅ Pipeline running, processing frames...
[INFO] ➡️ ENTRY (vx=16.0 cx=388): car ID:7362 conf=0.81
[INFO] ➡️ EXIT (vx=70.0 cx=530):  car ID:7362 conf=0.60
[INFO] 📊 Entries=1 Exits=1 | FPS=15 | Tracks=2
[INFO] 📤 Hourly metrics queued for Cloudflare upload
[INFO] Metrics synced to Cloudflare: 2026-03-15
```

**Stop with:** `Ctrl+C`

---

## ☁️ Cloudflare Metrics Dashboard (Optional)

The counter can sync metrics hourly to Cloudflare Workers KV and serve a live dashboard accessible from anywhere. This is entirely optional — the counter runs fine without it.

### How it works

A background thread in the Python script wakes up every hour, takes a snapshot of the current daily totals, and POSTs it to your Cloudflare Worker. This never blocks the GStreamer inference pipeline. At midnight the final day's totals are also uploaded automatically.

The Worker stores two KV entries per upload:
- `driveway:YYYY-MM-DD` — the running daily total (overwritten each hour)
- `hourly:YYYY-MM-DD:HH` — an intraday snapshot for the hour (expires after 48h)

### Step A: Create a Cloudflare Worker

1. Log in to [dash.cloudflare.com](https://dash.cloudflare.com) and go to **Workers & Pages**
2. Click **Create** → **Create Worker**
3. Name it `driveway-metrics` and click **Deploy**
4. Click **Edit code** and replace the default script with the contents of `cloudflare/index.ts` from this repository
5. Click **Deploy**

### Step B: Create a KV Namespace

1. In the Cloudflare dashboard go to **Workers & Pages** → **KV**
2. Click **Create a namespace**, name it `DRIVEWAY_METRICS`, and click **Add**
3. Go back to your `driveway-metrics` worker → **Settings** → **Bindings**
4. Click **Add** → **KV Namespace**
5. Set variable name to `DRIVEWAY_METRICS` and select the namespace you just created
6. Click **Save and deploy**

### Step C: Create an API Token

1. Go to [dash.cloudflare.com/profile/api-tokens](https://dash.cloudflare.com/profile/api-tokens)
2. Click **Create Token** → **Create Custom Token**
3. Give it a name like `driveway-pi`
4. Under **Permissions** add: `Account` → `Workers KV Storage` → `Edit`
5. Click **Continue to summary** → **Create Token**
6. Copy the token — it is only shown once

### Step D: Configure .env on the Pi

Add your Worker URL and token to `.env`:

```bash
WORKER_URL=https://driveway-metrics.YOUR_SUBDOMAIN.workers.dev/api/metrics
CLOUDFLARE_TOKEN=your_api_token_here
```

### Step E: Deploy the Dashboard

Host `cloudflare/index.html` anywhere static — Cloudflare Pages is the simplest option:

1. Go to **Workers & Pages** → **Create** → **Pages** → **Upload assets**
2. Upload `index.html` and deploy
3. Or simply open the file locally in a browser — it fetches data directly from the Worker API

### Dashboard endpoints

| Endpoint | Description |
|---|---|
| `GET /today` | Today's latest entry/exit totals |
| `GET /hourly` | Today's intraday snapshots sorted by hour |
| `GET /dashboard` | Last 30 days of daily totals |
| `POST /api/metrics` | Upload a metrics snapshot (used by the Pi) |

### Verifying the upload

In the Pi logs you should see:
```
[INFO] 📤 Hourly metrics queued for Cloudflare upload
[INFO] Metrics synced to Cloudflare: 2026-03-15
```

If you see `Cloudflare sync failed`, check that `WORKER_URL` and `CLOUDFLARE_TOKEN` are set correctly in `.env` and that the Worker is deployed.

**Note:** The intraday hourly chart will be empty until the first upload arrives, which is one hour after the counter starts. The `UPLOAD_INTERVAL_SEC` constant in `driveway_counter_hailo.py` controls the interval (default 3600 seconds).

---

## 🔧 Advanced: Run as Systemd Service

### Create Service File
```bash
sudo nano /etc/systemd/system/driveway-counter.service
```

**Service configuration:**
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

### Enable and Start
```bash
# Reload systemd
sudo systemctl daemon-reload

# Enable on boot
sudo systemctl enable driveway-counter.service

# Start now
sudo systemctl start driveway-counter.service

# Check status
sudo systemctl status driveway-counter.service

# View live logs
sudo journalctl -u driveway-counter.service -f
```

### Service Management
```bash
# Stop
sudo systemctl stop driveway-counter.service

# Restart
sudo systemctl restart driveway-counter.service

# Disable auto-start
sudo systemctl disable driveway-counter.service

# View recent logs
sudo journalctl -u driveway-counter.service -n 100
```

---

## 📊 Daily Reports

Reports are saved to `./reports/` directory:

```bash
# View today's report
cat reports/driveway_$(date +%Y-%m-%d).json

# Example output:
{
  "date": "2026-02-14",
  "entries": 15,
  "exits": 12
}
```

**Report rotation:** New file created automatically at midnight. The final totals are also uploaded to Cloudflare at rollover if configured.

---

## 🐛 Troubleshooting

### Issue: "Pipeline error: could not link videoconvert0 to hailonet0"
**Cause:** Missing `hailocropper` or wrong pipeline structure.

**Fix:**
1. Verify your `driveway_counter_hailo.py` includes the correct pipeline with `hailocropper` → `hailoaggregator`
2. Check the pipeline section matches the working version
3. The pipeline MUST use multi-branch structure (not direct RGB → hailonet)

---

### Issue: "AvgDets: 0.0" (no detections)
**Causes:**
1. Camera view has no objects
2. Detection threshold too high
3. `hailofilter` not in pipeline
4. Python `hailo` module not accessible

**Fixes:**
```bash
# 1. Lower confidence threshold
nano .env  # Set CONF_THRESH=0.30

# 2. Verify hailo module
python -c "import hailo; print(hailo.__file__)"

# 3. Check pipeline includes hailofilter
grep "hailofilter" driveway_counter_hailo.py

# 4. Test with gst-launch-1.0 (replace credentials and URL-encode special characters in password)
gst-launch-1.0 rtspsrc location="rtsp://user:password@192.168.1.100:554/cam/realmonitor?channel=1&subtype=1" latency=300 protocols=tcp ! rtph264depay ! h264parse ! avdec_h264 ! videoscale ! video/x-raw,width=704,height=480 ! videoconvert ! video/x-raw,format=RGB ! hailocropper so-path=/usr/lib/aarch64-linux-gnu/hailo/tappas/post_processes/cropping_algorithms/libwhole_buffer.so function-name=create_crops use-letterbox=true resize-method=inter-area internal-offset=true ! hailonet hef-path=./models/yolov8m.hef batch-size=1 ! fakesink
```

---

### Issue: "Track ID is None"
**Cause:** `hailotracker` not in pipeline or not configured correctly.

**Fix:** Verify pipeline includes:
```python
hailotracker name=tracker class-id=-1 kalman-dist-thr=0.9 iou-thr=0.65 init-iou-thr=0.7 keep-tracked-frames=15 keep-lost-frames=5 !
```

---

### Issue: "RTSP connection failed"
**Causes:**
1. Wrong credentials
2. Special characters in password not URL-encoded
3. Camera offline
4. Network issue

**Fixes:**
```bash
# 1. Test network connectivity
ping 192.168.1.100

# 2. Verify RTSP URL manually (URL-encode special characters for password)
ffmpeg -rtsp_transport tcp -i "rtsp://user:password@192.168.1.100:554/cam/realmonitor?channel=1&subtype=1" -frames:v 1 test.jpg

# 3. Check camera web interface
curl -u username:password http://192.168.1.100
```

---

### Issue: "ImportError: No module named 'gi'"
**Cause:** Created venv without `--system-site-packages`.

**Fix:**
```bash
rm -rf .venv
python3 -m venv .venv --system-site-packages
source .venv/bin/activate
pip install python-dotenv numpy==1.26.4 opencv-python==4.11.0.86 requests
```

---

### Issue: FPS drops below 10
**Causes:**
1. CPU throttling (overheating)
2. Network bandwidth issues
3. RTSP latency too high

**Fixes:**
```bash
# Check CPU temperature
vcgencmd measure_temp

# Check CPU frequency
vcgencmd measure_clock arm

# Monitor system resources
htop
```

---

### Issue: "Cloudflare sync failed"
**Causes:**
1. `WORKER_URL` or `CLOUDFLARE_TOKEN` missing or incorrect in `.env`
2. Worker not deployed or binding not configured
3. Network unreachable from the Pi

**Fixes:**
```bash
# 1. Verify .env has both values set
grep -E "WORKER_URL|CLOUDFLARE_TOKEN" .env

# 2. Test the Worker endpoint manually
curl -X POST https://driveway-metrics.YOUR_SUBDOMAIN.workers.dev/api/metrics \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"key":"driveway:2026-01-01","value":{"date":"2026-01-01","entries":1,"exits":1}}'
# Expected response: Metrics stored OK

# 3. Test the /today endpoint
curl https://driveway-metrics.YOUR_SUBDOMAIN.workers.dev/today
```

**Note:** Cloudflare sync failures are logged but never crash the counter. The inference pipeline continues regardless.

---

### Issue: Hourly chart shows no data on dashboard
**Cause:** The intraday chart is populated by hourly snapshot keys which only exist after the first upload, one hour after the counter starts.

**Fix:** Wait for the first `📤 Hourly metrics queued` log entry. If it never appears, check that `UPLOAD_INTERVAL_SEC` is set to `3600.0` in the script and that the Cloudflare upload thread started (look for `☁️  Cloudflare upload thread started` in logs).

---

## 📁 Project File Structure

```
/mnt/ssd/projects/driveway-counter/
├── .venv/                                    # Python virtual environment
├── .env                                      # Your configuration (not in git)
├── .gitignore                                # Ignore .env, .venv, reports/
├── driveway_counter_hailo.py                 # Main application script
├── SETUP_GUIDE.md                            # This file
├── README.md                                 # Project documentation
├── models/
│   └── yolov8m.hef -> /usr/local/hailo/...  # Symlink to system model
├── reports/                                  # Daily JSON reports (not in git)
│   └── driveway_2026-03-15.json
└── requirements.txt                          # Python dependencies
```

---

## 📦 What Goes in Git vs Local

### ✅ Commit to Git:
- `driveway_counter_hailo.py`
- `SETUP_GUIDE.md`
- `README.md`
- `.gitignore`
- `requirements.txt`
- `.env.example` (template without credentials)

### ❌ Don't Commit (add to .gitignore):
- `.venv/`
- `.env` (contains passwords and API tokens!)
- `reports/`
- `models/` (symlinks, system-dependent)
- `*.pyc`, `__pycache__/`

---

## ⚡ Performance Metrics

**Expected Performance:**
- **FPS:** 15 FPS (RTSP H.264 decode + Hailo inference)
- **Latency:** ~300ms end-to-end
- **CPU Usage:** 7%
- **Memory:** 190MB RAM
- **Hailo Utilization:** 30-40% (yolov8m + tracking + postprocessing)
- **Storage:** ~2-5KB per day (JSON reports)
- **Cloudflare upload:** background thread, zero inference impact

**What's Running on Hailo 26 TOPS:**
✅ YOLOv8m neural network inference (`hailonet`)  
✅ YOLO NMS postprocessing (`hailofilter`)  
✅ Kalman filter object tracking (`hailotracker`)  
✅ Letterbox preprocessing (`hailocropper`)  

**What's Running on CPU:**
- RTSP stream decoding (H.264 → raw frames)
- Video scaling/format conversion
- Python logic (zone checking, counting)
- Cloudflare upload thread (background, non-blocking)

---

## 🎯 Zone Configuration Guide

Zones are defined in `.env` using **original camera resolution** coordinates.

**Example for low-res stream (704x480):**

```bash
TRACKING_ZONE=[[380,3],[480,3],[480,460],[380,460]]
```

**Visual representation:**

```
Frame: 704×480 pixels (substream)
┌──────────────────────────┐ y=0 ← Top
│     ┌──────────────┐     │
│     │              │     │
│     │              │     │
│     │ ← TRACKING → │     │
│     │   ← ZONE →   │     │
│     │              │     │
│     │              │     │
│     └──────────────┘     │
└──────────────────────────┘ y=480 ← Bottom
x=0                           x=704
```

**To adjust zones:**
1. Edit `.env`
2. Modify coordinate arrays (must be 4 corner points, clockwise from top-left)
3. Save and restart application
4. Monitor logs for zone crossing events to verify placement

**Tip:** Use `web_calib.py` to calibrate coordinates visually with a live stream overlay.

---

## 🔄 Update Instructions

```bash
cd /mnt/ssd/projects/driveway-counter

# Stop service (if running)
sudo systemctl stop driveway-counter.service

# Pull latest code
git pull origin main

# Reinstall dependencies (if requirements.txt changed)
source .venv/bin/activate
pip install -r requirements.txt

# Restart service
sudo systemctl start driveway-counter.service

# Or run manually
python driveway_counter_hailo.py
```

---

## 🧪 Testing

### Test 1: GStreamer Pipeline Only
```bash
# If password has special characters, url-encode them
gst-launch-1.0 rtspsrc location="rtsp://user:password@192.168.1.100:554/cam/realmonitor?channel=1&subtype=1" latency=300 protocols=tcp ! rtph264depay ! h264parse ! avdec_h264 ! videoscale ! video/x-raw,width=704,height=480 ! videoconvert ! video/x-raw,format=RGB ! hailocropper so-path=/usr/lib/aarch64-linux-gnu/hailo/tappas/post_processes/cropping_algorithms/libwhole_buffer.so function-name=create_crops use-letterbox=true ! hailonet hef-path=./models/yolov8m.hef batch-size=1 ! fakesink -v
```

### Test 2: Python Hailo Module
```bash
python3 << 'EOF'
import hailo
import gi
gi.require_version('Gst', '1.0')
from gi.repository import Gst
print("✅ All imports successful")
EOF
```

### Test 3: Cloudflare Worker Endpoint
```bash
# Replace with your actual Worker URL and token
curl https://driveway-metrics.YOUR_SUBDOMAIN.workers.dev/today
curl https://driveway-metrics.YOUR_SUBDOMAIN.workers.dev/hourly
curl https://driveway-metrics.YOUR_SUBDOMAIN.workers.dev/dashboard
```

### Test 4: Full Application (Dry Run)
```bash
# Edit .env and set CONF_THRESH=0.0 to detect everything
python driveway_counter_hailo.py
# You should see many detections and the Cloudflare thread start message
```

---

## 📚 Additional Resources

- [Hailo AI Documentation](https://hailo.ai/developer-zone/)
- [Hailo RPi5 Examples GitHub](https://github.com/hailo-ai/hailo-rpi5-examples)
- [GStreamer Hailo Plugin Reference](https://github.com/hailo-ai/tappas)
- [YOLOv8 Model Zoo](https://github.com/hailo-ai/hailo_model_zoo)
- [Cloudflare Workers Documentation](https://developers.cloudflare.com/workers/)
- [Cloudflare KV Documentation](https://developers.cloudflare.com/kv/)

---

**🎉 You're all set! The Hailo 26 TOPS AI HAT is now counting vehicles in your driveway.**

---

**Tested On:** Raspberry Pi 5 + Hailo AI HAT+ (26 TOPS) + Lorex 4K RTSP Camera

**Key Improvements:**

1. ✅ **System-wide Hailo setup** (hailo-rpi5-examples install.sh)
2. ✅ **`--system-site-packages`** (critical for Hailo Python access)
3. ✅ **Model symlinks** (30MB saved, always system-latest)
4. ✅ **GStreamer pipeline debug** (hailocropper/aggregator fix)
5. ✅ **RTSP password encoding** (url-encoded special characters)
6. ✅ **Production systemd service** (restart/monitoring ready)
7. ✅ **Git hygiene** (`.env` + `.venv` + `reports/` ignored)
8. ✅ **Live zone calibration** (web_calib.py visualizer)
9. ✅ **Single-zone logic** (no line-crossing complexity)
10. ✅ **Low-res optimization** (704×480 substream viable)
11. ✅ **Jitter-resistant tracking** (parked vehicles ignored, tuned Kalman parameters)
12. ✅ **7% CPU efficiency** (Hailo-8L fully utilized)
13. ✅ **Cloudflare hourly metrics** (background thread, non-blocking)
14. ✅ **Live dashboard** (today summary, intraday chart, 30-day history)

**`git clone` and follow this guide to recreate the exact working setup!** 🎯