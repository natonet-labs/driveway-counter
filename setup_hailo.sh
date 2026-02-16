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
    echo "   Please run hailo-rpi5-examples installation first:"
    echo "   cd /mnt/ssd/projects/hailo-rpi5-examples && ./install.sh"
    exit 1
fi

# Create reports directory
mkdir -p reports

# Verify postprocess library exists (installed by hailo-rpi5-examples)
if [ ! -f "/usr/local/hailo/resources/so/libyolo_hailortpp_postprocess.so" ]; then
    echo "⚠️  WARNING: YOLO postprocess library not found!"
    echo "   Expected: /usr/local/hailo/resources/so/libyolo_hailortpp_postprocess.so"
    echo "   This should be installed by hailo-rpi5-examples/install.sh"
    echo ""
    echo "   If you get pipeline errors, run:"
    echo "   cd /mnt/ssd/projects/hailo-rpi5-examples && ./install.sh"
else
    echo "✅ YOLO postprocess library found"
fi

echo ""
echo "✅ Setup complete!"
echo ""
echo "📋 Next steps:"
echo "   1. Copy .env.example to .env and configure your camera"
echo "   2. Run: source .venv/bin/activate"
echo "   3. Run: python driveway_counter_hailo.py"
echo ""
