# Hailo-8 on Raspberry Pi 5 — Troubleshooting & Restoration Guide

Use this guide when:

- The app crashes with `HAILO_OUT_OF_PHYSICAL_DEVICES(74)` or segfaults on launch
- `hailortcli scan` stops finding the device after a system update
- The GStreamer pipeline fails to start or produces no detections
- The kernel driver is lost after a kernel update

**Hardware:** Raspberry Pi 5 + Hailo-8 AI HAT (26 TOPS)  
**Not applicable to:** Hailo-10 (`hailo1x_pci` driver)

---

## Known-Good Stack

| Component | Version |
|---|---|
| Kernel driver | `hailo_pci.ko` from `hailort-drivers` **v4.19.0** |
| User-space | `hailo-all` (HailoRT 4.23.0) |
| Python module | `hailo.cpython-313-aarch64-linux-gnu.so` (Python 3.13) |
| GStreamer plugins | `hailonet`, `hailofilter`, `hailocropper`, `hailoaggregator`, `hailotracker` |

---

## Quick Diagnosis Checklist

Run these before diving into individual sections:

```bash
# 1. Is the device node present?
ls -l /dev/hailo*

# 2. Is the correct driver loaded? (only hailo_pci should appear)
lsmod | grep hailo

# 3. Does HailoRT see the device?
hailortcli scan

# 4. Can HailoRT talk to the firmware?
hailortcli fw-control identify

# 5. Is anything already holding the device open?
sudo fuser -v /dev/hailo0
```

---

## Issue 1: `HAILO_OUT_OF_PHYSICAL_DEVICES(74)` + Segfault

This is the most common error. There are three distinct causes — check them in order.

### Cause A: systemd service already running (most common)

The service is configured with `Restart=always`. After any crash it respawns within 10 seconds and reclaims `/dev/hailo0`. Any manual launch while the service is active will immediately fail with this error.

```bash
# Check
sudo systemctl status driveway-counter.service

# Fix — stop the service before any manual run
sudo systemctl stop driveway-counter.service
python3 driveway_counter_hailo.py

# When done debugging, restart the service
sudo systemctl start driveway-counter.service
```

**Standard debug workflow:**
```bash
sudo systemctl stop driveway-counter.service
source .venv/bin/activate
python3 driveway_counter_hailo.py
# ... debug ...
sudo systemctl start driveway-counter.service
```

### Cause B: Stale process holding the device

A crashed GStreamer pipeline can leave a Python process alive that keeps `/dev/hailo0` open even after the app appears to have exited.

```bash
# Find the process
sudo fuser -v /dev/hailo0

# Kill it
sudo fuser -k /dev/hailo0
sleep 2

# Verify device is free (use_count should be 0)
lsmod | grep hailo
# Expected: hailo_pci   147456  0
```

### Cause C: Two Hailo drivers loaded simultaneously

A system update may load `hailo1x_pci` (the Hailo-10 driver) alongside `hailo_pci` (the Hailo-8 driver). They fight over the device.

```bash
# Check
lsmod | grep hailo
# If you see BOTH hailo_pci AND hailo1x_pci, that is the problem

# Immediate fix
sudo rmmod hailo1x_pci

# Permanent fix — blacklist the Hailo-10 driver
echo "blacklist hailo1x_pci" | sudo tee /etc/modprobe.d/hailo-blacklist.conf
sudo update-initramfs -u
```

After a reboot, `lsmod | grep hailo` should show only `hailo_pci`.

---

## Issue 2: Driver Lost After Kernel Update

Symptoms:
- `ls -l /dev/hailo*` → `No such file or directory`
- `sudo modprobe hailo_pci` → `Module hailo_pci not found`
- `hailortcli scan` → `No devices found`

The kernel update built a new kernel image and the old DKMS module no longer matches. Rebuild the driver from source.

### Step 1 — Clone and checkout the stable tag

```bash
cd /tmp
git clone https://github.com/hailo-ai/hailort-drivers.git
cd hailort-drivers
git checkout v4.19.0
```

> If you see `hailo1x_pci.ko` during the build you are on the wrong branch. `v4.19.0` builds `hailo_pci.ko` for Hailo-8 only.

### Step 2 — Build

```bash
cd linux/pcie
make clean && make all
find . -name "hailo_pci.ko"
# Expected: ./build/release/aarch64/hailo_pci.ko
```

### Step 3 — Install

```bash
sudo mkdir -p /lib/modules/$(uname -r)/kernel/drivers/misc/
sudo cp build/release/aarch64/hailo_pci.ko \
  /lib/modules/$(uname -r)/kernel/drivers/misc/
sudo depmod -a
sudo modprobe hailo_pci
hailortcli scan
# Expected: Device: 0001:01:00.0
```

---

## Issue 3: "AvgDets: 0.0" — No Detections

### Check 1: Confidence threshold too high

```bash
grep CONF_THRESH .env
# Try lowering to 0.25 for testing
```

### Check 2: Hailo Python module not loading

```bash
source .venv/bin/activate
python3 -c "import hailo; print(hailo.__file__)"
# Expected: /usr/lib/python3/dist-packages/hailo.cpython-313-aarch64-linux-gnu.so
```

If this fails, see **Issue 5** below.

### Check 3: hailofilter not in pipeline

```bash
grep "hailofilter" driveway_counter_hailo.py
# Must be present — it runs YOLO NMS postprocessing
```

### Check 4: Test with gst-launch

```bash
# URL-encode any special characters in the password
gst-launch-1.0 \
  rtspsrc location="rtsp://user:pass@192.168.1.100:554/cam/realmonitor?channel=1&subtype=1" \
  latency=300 protocols=tcp ! \
  rtph264depay ! h264parse ! avdec_h264 ! \
  videoscale ! video/x-raw,width=704,height=480 ! \
  videoconvert ! video/x-raw,format=RGB ! \
  hailocropper use-letterbox=true resize-method=inter-area internal-offset=true ! \
  hailonet hef-path=./models/yolov8m.hef batch-size=1 ! \
  fakesink
```

---

## Issue 4: `hailocropper` Pipeline Error / `libwhole_buffer.so` Not Found

The original hailo-rpi5-examples documentation references:
```
so-path=/usr/lib/aarch64-linux-gnu/hailo/tappas/post_processes/cropping_algorithms/libwhole_buffer.so
```

**Do not use this path.** `libwhole_buffer.so` is only present after a full TAPPAS source build, which is not required for this application. Running `hailocropper` without `so-path` uses the built-in whole-buffer passthrough mode, which works correctly with `hailo-all` / `hailo-tappas-core`.

The current `driveway_counter_hailo.py` does not set `so-path` on `hailocropper`. If you see this path in any version of the script, remove it.

---

## Issue 5: "Hailo Python Module Not Found" in venv

```bash
# Reinstall user-space packages
sudo apt update && sudo apt install hailo-all -y

# Re-create the dist-packages pointer
echo "/usr/lib/python3/dist-packages" \
  > .venv/lib/python3.13/site-packages/system_packages.pth

# Verify
source .venv/bin/activate
python3 -c "import hailo; print(hailo.__file__)"
```

If the file at `/usr/lib/python3/dist-packages/hailo.cpython-313-*.so` does not exist after reinstalling `hailo-all`, your Python version may have changed. Check:

```bash
python3 --version
ls /usr/lib/python3/dist-packages/hailo*.so
```

The `.so` filename must match your Python version.

---

## Issue 6: "ImportError: No module named 'gi'"

The venv was created without `--system-site-packages`. GStreamer Python bindings are system packages and cannot be installed via pip.

```bash
rm -rf .venv
python3 -m venv .venv --system-site-packages
echo "/usr/lib/python3/dist-packages" \
  > .venv/lib/python3.13/site-packages/system_packages.pth
source .venv/bin/activate
pip install -r requirements.txt
```

---

## Issue 7: "Track ID Is None"

`hailotracker` is missing from the pipeline or misconfigured.

Verify the pipeline string in `driveway_counter_hailo.py` contains:
```
hailotracker name=tracker class-id=-1 kalman-dist-thr=0.9 iou-thr=0.65
  init-iou-thr=0.7 keep-tracked-frames=15 keep-lost-frames=5 qos=false
```

---

## Issue 8: RTSP Connection Failed

```bash
# 1. Test network
ping 192.168.1.100

# 2. Test RTSP directly (URL-encode special characters in password)
ffmpeg -rtsp_transport tcp \
  -i "rtsp://user:pass@192.168.1.100:554/cam/realmonitor?channel=1&subtype=1" \
  -frames:v 1 -y /tmp/test.jpg

# 3. Verify camera web interface
curl -u username:password http://192.168.1.100
```

If the password contains special characters (`@`, `#`, `!`, etc.), they must be URL-encoded in the RTSP URL. The app handles this automatically via `urllib.parse.quote`.

---

## Issue 9: FPS Below 10

```bash
# Check CPU temperature (throttling starts around 80°C)
vcgencmd measure_temp

# Check CPU clock speed
vcgencmd measure_clock arm

# Check overall load
htop
```

Expected at steady state: ~15 FPS, ~7% CPU, ~50°C, ~190 MB RAM with the H.264 substream (SUBTYPE=1). The H.265 main stream (SUBTYPE=0) pushes CPU to 100% — use the substream.

---

## Issue 10: Cloudflare Sync Failed

```bash
# 1. Verify both values are set
grep -E "WORKER_URL|CLOUDFLARE_TOKEN" .env

# 2. Test the endpoint manually
curl -X POST https://YOUR_WORKER.workers.dev/api/metrics \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"key":"driveway:2026-01-01","value":{"date":"2026-01-01","entries":1,"exits":1}}'
# Expected response: Metrics stored OK

# 3. Test read endpoints
curl https://YOUR_WORKER.workers.dev/today
curl https://YOUR_WORKER.workers.dev/hourly
```

Cloudflare failures are logged but never crash the counter. The inference pipeline continues regardless.

---

## Issue 11: Intraday Chart Empty on Dashboard

The hourly chart only populates after the first upload, which happens one hour after the counter starts. Check the logs for:

```
📤 Hourly metrics queued for Cloudflare upload
Metrics synced to Cloudflare: YYYY-MM-DD
```

If neither line appears, verify `UPLOAD_INTERVAL_SEC = 3600.0` in the script and that the Cloudflare upload thread started (`☁️  Cloudflare upload thread started` in startup logs).

---

## Full Sanity Checklist

When everything seems broken after an update, walk this list top to bottom:

```bash
# 1. Driver
ls -l /dev/hailo*
lsmod | grep hailo          # only hailo_pci, no hailo1x_pci
hailortcli scan
hailortcli fw-control identify

# 2. User-space packages
dpkg -l | grep hailo
gst-inspect-1.0 | grep hailo

# 3. Service / process conflicts
sudo systemctl stop driveway-counter.service
sudo fuser -k /dev/hailo0 2>/dev/null

# 4. Python environment
cd /mnt/ssd/projects/driveway-counter
source .venv/bin/activate
python3 -c "import hailo; import gi; print('OK')"

# 5. Models and libraries
ls -lh models/yolov8m.hef
ls -l /usr/local/hailo/resources/so/libyolo_hailortpp_postprocess.so
ls -l /usr/local/hailo/resources/barcode_labels/coco_80.json

# 6. Run
python3 driveway_counter_hailo.py
```
