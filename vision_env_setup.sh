#!/usr/bin/env bash
# vision_env_setup.sh — sets up Python venv for visual perception pipeline
# RTX 3060 Ti / CUDA 13.0 driver → PyTorch cu124 wheels

set -e

VENV=~/vision_env

echo "Creating virtual environment at $VENV ..."
python3 -m venv "$VENV"
source "$VENV/bin/activate"

echo "Upgrading pip..."
pip install --upgrade pip

echo "Installing PyTorch (CUDA 12.4 wheels)..."
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu124

echo "Installing YOLOv8 (ultralytics)..."
pip install ultralytics

echo "Installing vision utilities..."
pip install pillow opencv-python tqdm requests

echo "Installing RelTR dependencies..."
pip install cython scipy pycocotools

echo ""
echo "Verifying..."
python3 -c "
import torch
from ultralytics import YOLO
print(f'torch:       {torch.__version__}')
print(f'cuda avail:  {torch.cuda.is_available()}')
print(f'device:      {torch.cuda.get_device_name(0) if torch.cuda.is_available() else \"cpu\"}')
print(f'ultralytics: ok')
"

echo ""
echo "Done. Activate with: source ~/vision_env/bin/activate"
echo "Then test: python3 ~/projectbrain_dev/vision/test_yolo.py"
