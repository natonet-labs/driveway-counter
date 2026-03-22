# Hailo-8 AI Accelerator on Raspberry Pi 5 - Troubleshooting & Restoration Guide

This document provides a step-by-step restoration guide for the Hailo-8 AI HAT on the Raspberry Pi 5. Use this guide when:

- The kernel driver is lost after a system/kernel update.
- `hailortcli scan` stops seeing the device.
- The driveway-counter GStreamer pipeline crashes with Hailo errors or segfaults.

This guide is specific to **Hailo-8 on Raspberry Pi 5** using the **driveway-counter** application.

---

## Specs & Known-Good Stack

- **Hardware:** Raspberry Pi 5 with Hailo-8 AI HAT (26 TOPS).  
  *Note: This is NOT the Hailo-10 (which uses `hailo1x_pci.ko`).*
- **Kernel driver:** `hailo_pci.ko` built from `hailort-drivers` **v4.19.0**.
- **User-space stack (APT):**
  - `hailo-all` (includes `hailort`, GStreamer plugins, examples, etc.).
  - `hailo-tappas-core` (core TAPPAS libs; full TAPPAS source build is *not* required for this app).
- **Python module:**  
  `/usr/lib/python3/dist-packages/hailo.cpython-313-aarch64-linux-gnu.so` (Python 3.13).
- **GStreamer plugins required:**
  - `hailonet`, `hailofilter`, `hailocropper`, `hailoaggregator`, `hailotracker`.[web:344][web:351]

### Version mismatch notes

- If you see:

  ```text
  [HailoRT] [error] Failed to query driver info with HAILO_DRIVER_INVALID_IOCTL(86)
  HAILO_INVALID_DRIVER_VERSION(76)
  ```

  when running `hailortcli` tools, that indicates a mismatch between the `hailo_pci` kernel module and the installed user-space `hailort` library.[file:360]

- For this setup, the following has been validated to work:

  - **Kernel driver:** `v4.19.0` from `hailort-drivers`  
  - **User space:** `hailo-all` (HailoRT `4.23.0`)

  Minor IOCTL/version warnings are acceptable **as long as**:
  - `hailortcli fw-control identify` succeeds, and
  - The GStreamer pipeline runs without segfaults.[file:360]

---

## 1. Symptoms of Driver / Device Failure

Use this section when the hardware itself disappears or the kernel module is broken.

- `hailortcli scan` returns:

  ```text
  Hailo Devices:
  [-]  No devices found.
  ```

- `ls -l /dev/hailo*` returns `No such file or directory`.
- `sudo modprobe hailo_pci` returns:

  ```text
  modprobe: FATAL: Module hailo_pci not found
  ```

- `dmesg` shows Hailo-related errors on boot or after plugging the HAT.
- The driveway-counter app fails very early, before any GStreamer output, or segfaults immediately.

---

## 2. Kernel Driver Restoration (Hailo-8 Only)

If the driver is lost or corrupted, **do not** rely on DKMS or the `hailo-rpi5-examples` install script if they repeatedly fail. Instead, manually compile the `v4.19.0` driver from the exact repository.

### Step 2.1: (Optional but Dangerous) Purge Broken Kernel Modules

Only use this if the system is in a badly broken state and you know what you’re doing. It will also remove user-space Hailo packages, which must then be reinstalled.

```bash
sudo apt purge 'hailo*' 'hailort*' 'h10-*' 'h8-*' dkms -y
sudo apt autoremove -y
sudo rm -rf /lib/modules/$(uname -r)/kernel/drivers/misc/hailo*
sudo depmod -a
```

> **Note:** After this purge you *must* reinstall `hailo-all` later (see section 4). Avoid this step unless absolutely necessary.[file:360]

### Step 2.2: Clone the Hailo Drivers Repository

The kernel drivers are in `hailort-drivers`, **not** the main `hailort` repo.

```bash
cd /tmp
git clone https://github.com/hailo-ai/hailort-drivers.git
cd hailort-drivers
```

### Step 2.3: Checkout Stable Version (v4.19.0)

```bash
git checkout v4.19.0
```

> If you see `hailo1x_pci.ko` during build, you are on a Hailo-10 branch/tag — switch back to `v4.19.0`.[file:360]

### Step 2.4: Compile the Driver for Hailo-8

```bash
cd linux/pcie
make clean
make all
```

Verify the resulting module:

```bash
find . -name "hailo_pci.ko"
# Expected: ./build/release/aarch64/hailo_pci.ko
```

### Step 2.5: Install and Load the Kernel Module

```bash
sudo mkdir -p /lib/modules/$(uname -r)/kernel/drivers/misc/
sudo cp build/release/aarch64/hailo_pci.ko /lib/modules/$(uname -r)/kernel/drivers/misc/
sudo depmod -a
sudo modprobe hailo_pci
```

---

## 3. Basic Device Verification

Run these as the normal `pi` user (no venv needed):

```bash
ls -l /dev/hailo*
# Expected: crw-rw-rw- 1 root root 508, 0 ... /dev/hailo0 (permissions may vary)

hailortcli scan
# Expected:
# Hailo Devices:
# [-] Device: 0001:01:00.0

hailortcli fw-control identify
# Expected: Hailo-8, firmware 4.23.0, etc.
```

If `scan` and `fw-control identify` both succeed, the **kernel driver and firmware are OK**.

If `hailortcli fw-control identify` fails with version/IOCTL errors but `scan` works, treat it as a **driver/library mismatch**; confirm you’re on `hailo-all` that is compatible with your driver build or recompile driver from the matching `hailort-drivers` tag.

---

## 4. User-Space Hailo Stack & Python venv

The driveway-counter app runs under **Python 3.13** in a virtual environment and relies on the system-installed Hailo Python extension.[file:335]

### Step 4.1: Ensure Hailo User-Space Packages Are Installed

After any purge or OS upgrade, reinstall:

```bash
sudo apt update
sudo apt install hailo-all -y
```

This provides:

- `/usr/lib/python3/dist-packages/hailo.cpython-313-aarch64-linux-gnu.so`
- GStreamer elements: `hailonet`, `hailofilter`, `hailocropper`, `hailoaggregator`, `hailotracker`, etc.[web:341][web:351]

Verify the Python module:

```bash
find /usr/lib/python3/dist-packages -maxdepth 1 -name "hailo*"
# Expect:
# /usr/lib/python3/dist-packages/hailo.cpython-313-aarch64-linux-gnu.so
# plus hailo_platform, hailort-*.egg-info, etc.
```

### Step 4.2: Virtualenv Creation (with system-site-packages)

In `/mnt/ssd/projects/driveway-counter`:

```bash
python3 -m venv .venv --system-site-packages
source .venv/bin/activate
pip install -r requirements.txt
```

Check `pyvenv.cfg`:

```text
include-system-site-packages = true
version = 3.13.5
```

### Step 4.3: Fix “Hailo Python module not found” in venv

If you still see:

```text
Hailo Python module not found. Ensure venv uses --system-site-packages. Error: No module named 'hailo'
```

even though `hailo-all` is installed, explicitly add `dist-packages` into the venv’s site-packages:

```bash
echo "/usr/lib/python3/dist-packages" > .venv/lib/python3.13/site-packages/system_packages.pth
```

Then:

```bash
source .venv/bin/activate
python3 -c "import hailo; print(hailo.__file__)"
# Should print: /usr/lib/python3/dist-packages/hailo.cpython-313-aarch64-linux-gnu.so
```

---

## 5. GStreamer / Hailo Pipeline Checks

The driveway-counter app uses a GStreamer pipeline that depends on Hailo plugins and a YOLO postprocess `.so`.[file:335]

### Step 5.1: Verify Hailo GStreamer plugins

```bash
gst-inspect-1.0 | grep hailo
```

You should see entries including:

- `hailo: hailonet`
- `hailotools: hailofilter`
- `hailotools: hailocropper`
- `hailotools: hailoaggregator`
- `hailotools: hailotracker`[web:351]

If `hailonet` / `hailofilter` / `hailocropper` are missing, reinstall:

```bash
sudo apt install hailo-tappas-core -y
```

*(Full TAPPAS source install is **not required** for this app — only the core libs and plugins.)*[web:356]

### Step 5.2: Model & Postprocess Library Paths

The application expects:

- Model (symlinked by `setup_hailo.sh`):

  ```bash
  ./models/yolov8m.hef  ->  /usr/local/hailo/resources/models/hailo8/yolov8m.hef
  ```

- YOLO postprocess `.so`:

  ```bash
  /usr/local/hailo/resources/so/libyolo_hailortpp_postprocess.so
  ```

Check:

```bash
ls -l ./models/yolov8m.hef
ls -l /usr/local/hailo/resources/so/libyolo_hailortpp_postprocess.so
```

If the model symlink is missing, rerun:

```bash
./setup_hailo.sh
```

### Step 5.3: Cropper Library

Original code referenced:

```text
/usr/lib/aarch64-linux-gnu/hailo/tappas/post_processes/cropping_algorithms/libwhole_buffer.so
```

This library is part of a **full TAPPAS build** and is *not* present when only `hailo-all` / `hailo-tappas-core` are installed. The app has been updated to use `hailocropper` **without** an external `so-path`, which is supported and works as a passthrough/general cropper mode.[web:344][web:347][file:335]

Do **not** reintroduce the `so-path=...libwhole_buffer.so` line unless you also install and maintain a full TAPPAS source build.

---

## 6. Common Runtime Errors & Fixes

### 6.1 `HAILO_OUT_OF_PHYSICAL_DEVICES(74)` during pipeline start

Symptoms:

```text
[HailoRT] [error] CHECK failed - Failed to create vdevice. there are not enough free devices. requested: 1, found: 0
HAILO_OUT_OF_PHYSICAL_DEVICES(74)
Segmentation fault
```

But:

- `hailortcli scan` shows `Device: 0001:01:00.0`.
- `hailortcli fw-control identify` works.

This typically indicates:

- The GStreamer pipeline is misconfigured (e.g., pointing `hailofilter` / `hailocropper` at missing or incompatible `.so` files), **not** that the hardware is actually gone.[web:340][web:355][file:335]

Actions:

1. Ensure no stale processes hold `/dev/hailo0`:

   ```bash
   sudo fuser -v /dev/hailo0
   ps aux | grep hailo
   ```

   Kill if needed:

   ```bash
   sudo killall -9 python3 python
   sudo fuser -k /dev/hailo0
   ```

2. Relax permissions (for testing):

   ```bash
   sudo chmod 666 /dev/hailo*
   hailortcli scan
   ```

3. Verify all `so-path=` targets in the pipeline actually exist (see section 5.2) and that the cropper is **not** pointing at a missing `libwhole_buffer.so`.

### 6.2 `Hailo Python module not found`

Already covered in **4.3**: ensure `hailo-all` is installed and that `.venv/lib/python3.13/site-packages/system_packages.pth` contains:

```text
/usr/lib/python3/dist-packages
```

---

## 7. Driveway-Counter App: Sanity Checklist

If the Hailo-8 hardware is fine but the driveway-counter app fails, walk this list:

1. **Driver / Device:**

   ```bash
   ls -l /dev/hailo*
   hailortcli scan
   hailortcli fw-control identify
   ```

2. **User-space Hailo:**

   ```bash
   dpkg -l | grep hailo
   find /usr/lib/python3/dist-packages -maxdepth 1 -name "hailo*"
   gst-inspect-1.0 | grep hailo
   ```

3. **Python env:**

   ```bash
   cd /mnt/ssd/projects/driveway-counter
   python3 -m venv .venv --system-site-packages
   echo "/usr/lib/python3/dist-packages" > .venv/lib/python3.13/site-packages/system_packages.pth
   source .venv/bin/activate
   pip install -r requirements.txt
   python3 -c "import hailo"
   ```

4. **Models & libs:**

   ```bash
   ./setup_hailo.sh
   ls -l ./models/yolov8m.hef
   ls -l /usr/local/hailo/resources/so/libyolo_hailortpp_postprocess.so
   ```

5. **Run the app:**

   ```bash
   source .venv/bin/activate
   python3 driveway_counter_hailo.py
   ```

If all checks pass, the driveway counter should run without needing a full TAPPAS source install.
