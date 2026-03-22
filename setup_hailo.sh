#!/bin/bash
# setup_hailo.sh - Hailo Driveway Counter Setup

set -e  # Exit on any error

echo "🔧 Setting up Hailo Driveway Counter..."

# Create models directory
mkdir -p models

# Create symlink to system yolov8m.hef (installed by hailo-rpi5-examples)
if [ -f "/usr/local/hailo/resources/models/hailo8/yolov8m.hef" ]; then
  echo "📦 Creating symlink to system yolov8m.hef..."
  ln -sf /usr/local/hailo/resources/models/hailo8/yolov8m.hef ./models/yolov8m.hef
  echo "✅ Model linked: $(readlink -f models/yolov8m.hef)"
else
  echo "❌ ERROR: System yolov8m.hef not found!"
  echo "   Expected: /usr/local/hailo/resources/models/hailo8/yolov8m.hef"
  echo ""
  echo "   This file is installed by the Hailo RPi5 examples bundle."
  echo "   Please run:"
  echo "     cd /mnt/ssd/projects/hailo-rpi5-examples && ./install.sh"
  exit 1
fi

# Create reports directory
mkdir -p reports

# Verify YOLO postprocess library exists (installed by hailo-rpi5-examples / hailo-all)
YOLO_POST_SO="/usr/local/hailo/resources/so/libyolo_hailortpp_postprocess.so"
if [ ! -f "${YOLO_POST_SO}" ]; then
  echo "⚠️  WARNING: YOLO postprocess library not found!"
  echo "   Expected: ${YOLO_POST_SO}"
  echo ""
  echo "   This should be installed by hailo-rpi5-examples (and hailo-all)."
  echo "   If you get GStreamer pipeline errors related to hailofilter, run:"
  echo "     cd /mnt/ssd/projects/hailo-rpi5-examples && ./install.sh"
else
  echo "✅ YOLO postprocess library found: ${YOLO_POST_SO}"
fi

echo ""
echo "✅ Setup complete!"
echo ""
echo "📋 Next steps:"
echo "  1. Copy .env.example to .env and configure your camera"
echo "  2. Run: python3 -m venv .venv --system-site-packages"
echo "  3. Run: echo \"/usr/lib/python3/dist-packages\" > .venv/lib/python3.13/site-packages/system_packages.pth"
echo "  4. Run: source .venv/bin/activate"
echo "  5. Run: pip install -r requirements.txt"
echo "  6. Run: python driveway_counter_hailo.py"
echo ""
echo "ℹ️  If Hailo driver or GStreamer errors persist, see docs/TROUBLESHOOTING.md"
