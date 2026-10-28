#!/bin/sh
# One-time setup: checks requirements, then builds the Python environment, downloads the
# Kokoro voice model (~340 MB), installs ffmpeg if needed, and compiles the OCR helper.
# Safe to run again at any time (for example after moving the folder).
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"
die() { echo "Setup stopped: $*" >&2; exit 1; }

# --- requirements
[ "$(uname)" = Darwin ] || die "book2audio needs macOS (it uses Apple's Vision framework for OCR)."
[ ${#DIR} -le 90 ] || die "this folder's path is too long for the voice's pronunciation library:
  $DIR
Move the folder somewhere shorter, such as ~/book2audio, and run ./setup.sh again."
xcode-select -p >/dev/null 2>&1 && command -v swiftc >/dev/null ||
  die "the Xcode Command Line Tools are missing. Run:  xcode-select --install  then run ./setup.sh again."
command -v python3 >/dev/null || die "python3 is missing. Install Python 3.10 or newer (python.org or Homebrew)."
python3 -c 'import sys; sys.exit(sys.version_info < (3, 10))' ||
  die "Python 3.10 or newer is needed; python3 is $(python3 -V 2>&1)."
if ! command -v ffmpeg >/dev/null; then
  command -v brew >/dev/null || die "ffmpeg is missing. Install Homebrew (https://brew.sh), then run ./setup.sh again."
  echo "Installing ffmpeg with Homebrew..."
  brew install ffmpeg
fi

# --- Python environment (rebuilt if missing, broken, or the folder has moved)
if [ ! -x .venv/bin/python ] || [ "$(cat .venv/.location 2>/dev/null)" != "$DIR" ] ||
   ! .venv/bin/python -c 'import sys' 2>/dev/null; then
  echo "Creating Python environment..."
  rm -rf .venv
  python3 -m venv .venv
  echo "$DIR" > .venv/.location
fi
.venv/bin/python -m pip install -q --upgrade pip
.venv/bin/python -m pip install -q -r requirements.txt

# --- Kokoro voice model (Apache-2.0), checked against known checksums
mkdir -p models
URL=https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0
while read -r sum f; do
  if [ ! -f "models/$f" ] || ! echo "$sum  models/$f" | shasum -a 256 -c -s; then
    echo "Downloading $f..."
    curl -fL --progress-bar -o "models/$f.part" "$URL/$f"
    echo "$sum  models/$f.part" | shasum -a 256 -c -s || { rm -f "models/$f.part"; die "$f failed its checksum."; }
    mv "models/$f.part" "models/$f"
  fi
done <<EOF
7d5df8ecf7d4b1878015a32686053fd0eebe2bc377234608764cc0ef3636a6c5 kokoro-v1.0.onnx
bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d voices-v1.0.bin
EOF

# --- OCR helper
echo "Compiling OCR helper..."
swiftc -O -suppress-warnings src/book2audio/ocr.swift -o ocr

./book2audio --help >/dev/null
command -v claude >/dev/null ||
  echo "Note: Claude Code (the \`claude\` command) isn't installed, so runs will skip proofreading and summaries."
echo "Ready. Hear the voices with:  ./book2audio --voice-samples"
