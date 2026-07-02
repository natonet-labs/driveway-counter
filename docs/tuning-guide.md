# Driveway Counter: Tuning Guide to Reduce False Positives

## Problem: 506 Entries / 470 Exits Yesterday

This is **3–5× normal** for a suburban driveway. You're detecting street traffic, leaves, birds, shadows, and wind-blown debris.

---

## Root Causes & Fixes

### 1. **CONF_THRESH = 0.45 is too low** ❌

YOLOv8m at 704×480 (low-res substream) from a high-mounted camera has many false positives. At 0.45 confidence:
- A shadow cast by a tree leaf might score 0.50 and pass
- A distant bird might score 0.48 and pass
- Street traffic noise scores 0.52 and gets counted

**Fix:** Raise to **0.65–0.75**

```bash
# .env
CONF_THRESH=0.65
```

Test strategy:
- Start at 0.65 (filters ~80% of junk)
- Run 1 day, check if real vehicles are missed
- If no missed entries/exits → raise to 0.70
- If missing real events → drop back to 0.60

---

### 2. **VELOCITY_THRESHOLD = 2 px/frame is tiny** ❌

At 15 FPS on 704×480:
- A leaf drifting: 1–3 px/frame
- A shadow jittering: 1–2 px/frame
- A real vehicle moving at 10 mph: 5–10 px/frame
- A person walking at 3 mph: 3–5 px/frame

Your threshold of 2 px is **below** ambient noise. Stationary shadows that compress artifacts cause to wiggle by 1–2 pixels per frame get counted as "motion."

**Fix:** Raise to **5–8 px/frame**

```bash
# .env
VELOCITY_THRESHOLD=5
```

This rejects leaves and jitter, accepts real motion.

---

### 3. **No size filtering** ❌

YOLOv8 doesn't understand "too small to be relevant." A bounding box 10×10 pixels (a gnat) is the same as 200×200 (a car) to the counting logic.

**Fix:** Add a minimum bounding box area filter (pixels²)

```bash
# .env
MIN_BBOX_AREA=2500
```

**Size reference at 704×480:**
| Area (px²) | Box Size | What it is |
|---|---|---|
| 500 | 22×22 | Leaf, bird |
| 1000 | 32×32 | Small bird, large insect |
| 2500 | 50×50 | Cat/dog |
| 5000 | 71×71 | Person/small vehicle |
| 10000 | 100×100 | Person/car |

Start with **2500** (filters insects/leaves) and adjust up if you still see false positives.

---

### 4. **Your tracking zone is too wide** ❌

```
TRACKING_ZONE=[[380,3],[480,3],[480,460],[380,460]]
```

On a 704×480 frame, this is:
- **X range:** 380–480 = **100 pixels wide**
- **Y range:** 3–460 = **covers almost full height**

At a high-mounted Lorex camera, this likely includes:
- Street apron / curb
- Sidewalk
- Neighbor's driveway edge
- Street pass-by traffic

**Fix:** Tighten to the actual driveway entrance

Use `web_calib.py` to visualize. A proper driveway zone should be:
- **Narrower** (maybe 60–80 px wide, not 100)
- **Lower** (bottom boundary at y=400, not y=460, to exclude street)

Example tightened zone:
```bash
TRACKING_ZONE=[[380,50],[480,50],[480,400],[380,400]]
```

**Important:** Don't shrink so much that family members walking past miss being counted. Use the calibration tool to verify.

---

## Recommended Starting Point

Update your `.env`:

```bash
# Detection filtering
CONF_THRESH=0.65
MIN_BBOX_AREA=2500
VELOCITY_THRESHOLD=5

# Zone (tighten from your current one)
# Use web_calib.py first to visualize!
TRACKING_ZONE=[[380,50],[480,50],[480,400],[380,400]]
```

---

## Deployment & Testing

### Step 1: Update `.env`

```bash
nano /mnt/ssd/projects/driveway-counter/.env
# Change the 4 parameters above
```

### Step 2: Deploy the enhanced script

```bash
sudo systemctl stop driveway-counter.service
cp driveway_counter_hailo_enhanced.py \
  /mnt/ssd/projects/driveway-counter/driveway_counter_hailo.py
sudo systemctl start driveway-counter.service
```

### Step 3: Monitor logs

```bash
sudo journalctl -u driveway-counter.service -f
```

Expected output (should show filtered detections):
```
16:32:15 [INFO] 📊 Entries=2 Exits=2 | FPS=15 | Tracks=0
16:32:25 [INFO] 📊 Entries=2 Exits=2 | FPS=15 | Tracks=1
16:32:35 [INFO] ➡️ ENTRY vx=5.2 cx=415 person ID:42 conf=0.78 area=3850
16:32:38 [INFO] ⬅️ EXIT  vx=-6.1 cx=420 person ID:42 conf=0.81 area=4120
```

Notice:
- `area=3850` and `area=4120` — bigger detections (real objects)
- `conf=0.78–0.81` — high confidence
- `vx=5–6` — meaningful velocity (not 1–2 px jitter)

### Step 4: Iterate

Run for **1 full day**. Check Cloudflare dashboard. Expected:
- **80–120 entries** on a suburban driveway
- Entries ≈ exits (minus family members coming/going without matching)

If still high (200+):
- Tighten the zone more (use calibration tool)
- Raise `CONF_THRESH` to 0.70
- Raise `VELOCITY_THRESHOLD` to 8

If missing real traffic:
- Lower `CONF_THRESH` to 0.60
- Lower `VELOCITY_THRESHOLD` to 4
- Widen the zone slightly

---

## Enable Debug Logging (Optional)

To see what's being filtered:

```bash
# .env
ISDEBUG=True
```

Logs will show:
```
[DEBUG] Filtered: car area=800 px² (below 2500)
[DEBUG] Filtered: person area=1200 px² (below 2500)
[DEBUG] Track 42 person@0.68 area=3500 in_zone=True cx=410 vx=2.1
```

This helps you dial in the thresholds. Turn off after tuning to reduce log spam.

---

## What Changed in the Enhanced Script

1. **Added `MIN_BBOX_AREA`** — skips tiny detections
2. **Raised default `CONF_THRESH`** to 0.65 (from 0.30)
3. **Raised default `VELOCITY_THRESHOLD`** to 5 px/frame (from 2)
4. **Added `VELOCITY_THRESHOLD` to `.env`** — now configurable without editing code
5. **Logged bbox area** in ENTRY/EXIT logs — helps debug
6. **Added area filter logging** in DEBUG mode

---

## When to Stop Tuning

You should see:
- **≤150 entries/exits on a quiet day** (1–2 family cars)
- **≥50 entries/exits on a busy day** (deliveries, visitors, etc.)
- **Entries ≈ exits** (balanced, not 506/470 asymmetry)
- **No logged garbage in the entry logs** — only real vehicles/people

If you're still seeing 400+ counts after these changes, the zone itself is the problem. Use `web_calib.py` to visually narrow it.

---

## Advanced: Understanding the Thresholds

```python
# What gets counted (pseudocode):
for detection in frame:
    if detection.confidence < CONF_THRESH:
        skip()  # Filter low-confidence garbage
    
    bbox_area = detection.bbox.width * detection.bbox.height
    if bbox_area < MIN_BBOX_AREA:
        skip()  # Filter tiny noise
    
    velocity = current_position - previous_position
    if abs(velocity) < VELOCITY_THRESHOLD:
        skip()  # Filter jitter
    
    if is_zone_crossing(current, previous):
        count()  # Real entry/exit
```

Each filter is independent. All must pass for a detection to count.

**Rule of thumb:**
- Too strict → miss real events
- Too loose → catch garbage

Balance by running 1 day per tuning iteration.