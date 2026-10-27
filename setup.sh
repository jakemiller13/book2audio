#!/bin/sh
# One-time setup: Python environment, Kokoro voice model (~340 MB), ffmpeg, OCR helper.
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q kokoro-onnx soundfile numpy anthropic
mkdir -p models
for f in kokoro-v1.0.onnx voices-v1.0.bin; do
  [ -f "models/$f" ] || curl -fL -o "models/$f" \
    "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/$f"
done
command -v ffmpeg >/dev/null || brew install ffmpeg
swiftc -O ocr.swift -o ocr 2>/dev/null
echo "Ready. Try: ./book2audio --help"
