#!/bin/sh
# One-time setup: checks requirements, then builds the Python environment, downloads the
# Kokoro voice model (~340 MB), installs ffmpeg if needed, and compiles the OCR helper.
# Safe to run again at any time (for example after moving the folder).
#
# Plain ./setup.sh never installs the expressive voice. It is a separate, opt-in step:
#
#   ./setup.sh --emotion          also installs the optional expressive voice for --emotion
#                                 (about 4.5 GB more: PyTorch and the Chatterbox models).
#                                 Asks before downloading anything.
#   ./setup.sh --emotion --yes    the same, without asking (for scripts)
#   ./setup.sh --remove-emotion   deletes the expressive voice again (frees about 4.5 GB)
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"
die() { echo "Setup stopped: $*" >&2; exit 1; }
EMOTION= YES= REMOVE=
for a in "$@"; do
  case "$a" in
    --emotion) EMOTION=1 ;;
    --yes) YES=1 ;;
    --remove-emotion) REMOVE=1 ;;
    *) die "unknown option $a (the options are --emotion, --yes and --remove-emotion)" ;;
  esac
done
[ -z "$YES" ] || [ -n "$EMOTION" ] || die "--yes only goes with --emotion."
[ -z "$REMOVE" ] || [ -z "$EMOTION" ] || die "use --emotion or --remove-emotion, not both."

# --- Remove the optional expressive voice
if [ -n "$REMOVE" ]; then
  if [ -e .venv-emotion ] || [ -e models/emotion ]; then
    echo "Removing the expressive voice (.venv-emotion and models/emotion)..."
    rm -rf .venv-emotion models/emotion
    echo "Done. --emotion is off until you run  ./setup.sh --emotion  again."
  else
    echo "The expressive voice isn't installed; nothing to remove."
  fi
  exit 0
fi

# --- Opt-in for the expressive voice: say what it costs, check space, and ask first
if [ -n "$EMOTION" ] && [ ! -f models/emotion/.ready ]; then
  cat <<'MSG'

The optional expressive voice (--emotion) is a large download:
  - about 1.3 GB  PyTorch and Chatterbox, in .venv-emotion/
  - about 3.2 GB  the Chatterbox and Whisper models, in models/emotion/
  = about 4.5 GB of disk space, all inside this folder.
It's only needed for --emotion; everything else in book2audio works without it.
Remove it any time with:  ./setup.sh --remove-emotion

MSG
  FREE_GB=$(df -Pk . | awk 'NR==2 {print int($4 / 1048576)}')
  [ "$FREE_GB" -ge 6 ] || die "the expressive voice needs about 4.5 GB and there are only ${FREE_GB} GB free here (keep 6 GB free to be safe)."
  if [ -z "$YES" ]; then
    [ -t 0 ] || die "not asking without a terminal. Run  ./setup.sh --emotion --yes  to install it anyway."
    printf "Download and install it now? [y/N] "
    read -r answer
    case "$answer" in
      [yY]|[yY][eE][sS]) ;;
      *) die "nothing was downloaded. Run  ./setup.sh  for book2audio without the expressive voice." ;;
    esac
  fi
fi

# --- requirements
[ "$(uname)" = Darwin ] || die "book2audio needs macOS (it uses Apple's Vision framework for OCR)."
[ ${#DIR} -le 90 ] || die "this folder's path is too long for the voice's pronunciation library:
  $DIR
Move the folder somewhere shorter, such as ~/book2audio, and run ./setup.sh again."
xcode-select -p >/dev/null 2>&1 && command -v swiftc >/dev/null ||
  die "the Xcode Command Line Tools are missing. Run:  xcode-select --install  then run ./setup.sh again."
# The voice packages need Python 3.12 or 3.13 (not yet 3.14).
PYTHON=
for c in python3.13 python3.12 python3; do
  if command -v "$c" >/dev/null && "$c" -c 'import sys; sys.exit(not (3, 12) <= sys.version_info < (3, 14))' 2>/dev/null; then
    PYTHON=$(command -v "$c"); break
  fi
done
[ -n "$PYTHON" ] || die "Python 3.12 or 3.13 is needed (python3 here is $(python3 -V 2>&1 || echo missing)).
Install it with:  brew install python@3.13  then run ./setup.sh again."
if ! command -v ffmpeg >/dev/null; then
  command -v brew >/dev/null || die "ffmpeg is missing. Install Homebrew (https://brew.sh), then run ./setup.sh again."
  echo "Installing ffmpeg with Homebrew..."
  brew install ffmpeg
fi

# --- Python environment (rebuilt if missing, broken, or the folder has moved)
if [ ! -x .venv/bin/python ] || [ "$(cat .venv/.location 2>/dev/null)" != "$DIR" ] ||
   ! .venv/bin/python -c 'import sys; sys.exit(not (3, 12) <= sys.version_info < (3, 14))' 2>/dev/null; then
  echo "Creating Python environment..."
  rm -rf .venv
  "$PYTHON" -m venv .venv
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

# --- Optional: the expressive voice (--emotion), in its own environment
if [ -n "$EMOTION" ]; then
  if [ ! -x .venv-emotion/bin/python ] || [ "$(cat .venv-emotion/.location 2>/dev/null)" != "$DIR" ]; then
    echo "Creating the expressive voice's Python environment..."
    rm -rf .venv-emotion
    "$PYTHON" -m venv .venv-emotion
    echo "$DIR" > .venv-emotion/.location
  fi
  .venv-emotion/bin/python -m pip install -q --upgrade pip
  echo "Installing the expressive voice (PyTorch and Chatterbox, about 1.3 GB)..."
  .venv-emotion/bin/python -m pip install -q -r requirements-emotion.txt
  echo "Downloading its models (about 3.2 GB, once)..."
  PYTHONWARNINGS=ignore HF_HUB_DISABLE_PROGRESS_BARS=1 HF_HUB_VERBOSITY=error \
    .venv-emotion/bin/python src/book2audio/emotion_worker.py --download
fi
command -v claude >/dev/null ||
  echo "Note: Claude Code (the \`claude\` command) isn't installed, so runs will skip proofreading and summaries."
echo "Ready. Hear the voices with:  ./book2audio --voice-samples"
[ -n "$EMOTION" ] || [ -x .venv-emotion/bin/python ] ||
  echo "(The optional expressive voice for --emotion is not installed. It's about 4.5 GB; see the README.)"
