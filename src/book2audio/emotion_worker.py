"""The expressive voice for --emotion: Chatterbox (MIT license), which imitates a narrator from
a short clip of them, and a small speech-to-text model that checks each line came out as
written. It runs in its own environment (.venv-emotion, made by ./setup.sh --emotion), since
it needs PyTorch, and emotion.py talks to it one request at a time:

  stdin   {"cmd": "voice", "path": "clip.wav"}             imitate this narrator
          {"cmd": "say", "text": ..., "exaggeration": 0.5, "cfg": 0.4, "seed": 1, "out": "x.npy"}
  stdout  {"ready": true, "rate": 24000}  once loaded, then one reply per request:
          {"ok": true, "heard": "what the checker heard"}  or  {"ok": false, "error": ...}

  python emotion_worker.py --download   fetches the models into models/emotion (setup only)
"""
import gc
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
os.environ.setdefault("HF_HOME", str(ROOT / "models" / "emotion"))
os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")   # the odd operation the Mac GPU lacks


def cap_gpu_memory(gb=8.0):
    """Keep the voice's GPU memory to about `gb`. It needs about 3.3 GB, but left alone
    PyTorch holds on to far more (18 GB on an 18 GB Mac, growing each line), which makes the
    Mac swap, slows everything down and ends in 'out of memory' partway through a chapter.
    The cap is a ratio of the GPU's recommended size, which is about 3/4 of the Mac's memory."""
    if "PYTORCH_MPS_HIGH_WATERMARK_RATIO" in os.environ:
        return
    try:
        ram = int(subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True,
                                 text=True).stdout) / 2**30
    except (OSError, ValueError):
        return
    high = min(1.7, max(0.3, gb / (0.75 * ram)))
    os.environ["PYTORCH_MPS_HIGH_WATERMARK_RATIO"] = f"{high:.2f}"
    os.environ["PYTORCH_MPS_LOW_WATERMARK_RATIO"] = f"{high * 0.8:.2f}"


cap_gpu_memory()
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
CHECKER = "openai/whisper-base.en"   # speech-to-text, about 290 MB
CHATTERBOX_FILES = ["ve.safetensors", "t3_cfg.safetensors", "s3gen.safetensors", "tokenizer.json", "conds.pt"]
READY = ROOT / "models" / "emotion" / ".ready"


def download():
    from huggingface_hub import hf_hub_download, snapshot_download
    from chatterbox.tts import REPO_ID
    for f in CHATTERBOX_FILES:
        hf_hub_download(repo_id=REPO_ID, filename=f)
    snapshot_download(CHECKER, allow_patterns=["*.json", "*.safetensors", "*.txt"])
    READY.write_text("ok\n")


def serve():
    reply_to = os.fdopen(os.dup(1), "w", buffering=1)
    os.dup2(2, 1)   # anything the libraries print goes to the log, not into the replies
    reply = lambda d: reply_to.write(json.dumps(d) + "\n")

    import numpy as np
    import torch
    from chatterbox.tts import ChatterboxTTS
    from transformers import pipeline
    device = "mps" if torch.backends.mps.is_available() else "cpu"

    def tidy_memory():
        """Give back the GPU memory PyTorch keeps in reserve after a line. Without this the
        reserve grows by about 60 MB a line until a long chapter runs out of memory. Returns
        how full the GPU memory is (0-1), so a worker close to the limit can be restarted."""
        if device != "mps":
            return 0.0
        gc.collect()
        torch.mps.empty_cache()
        # PyTorch's hard limit is the recommended size times this ratio (1.7 unless set)
        ratio = float(os.environ.get("PYTORCH_MPS_HIGH_WATERMARK_RATIO") or 1.7) or 1.7
        return torch.mps.driver_allocated_memory() / (torch.mps.recommended_max_memory() * ratio)

    tts = ChatterboxTTS.from_pretrained(device=device)
    hear = pipeline("automatic-speech-recognition", model=CHECKER, device="cpu")
    reply({"ready": True, "rate": tts.sr})

    for line in sys.stdin:
        try:
            req = json.loads(line)
            if req["cmd"] == "voice":
                tts.prepare_conditionals(req["path"])
                reply({"ok": True})
            elif req["cmd"] == "say":
                torch.manual_seed(req["seed"])
                wav = tts.generate(req["text"], exaggeration=req["exaggeration"], cfg_weight=req["cfg"])
                wav = wav.squeeze(0).cpu().numpy().astype(np.float32)
                np.save(req["out"], wav)
                heard = hear({"raw": wav, "sampling_rate": tts.sr})["text"]
                reply({"ok": True, "heard": heard.strip(), "memory": tidy_memory()})
            else:
                reply({"ok": False, "error": f"unknown request {req.get('cmd')!r}"})
        except Exception as e:   # one bad line shouldn't end the whole book
            reply({"ok": False, "error": f"{type(e).__name__}: {e}"[:400], "memory": tidy_memory()})


if __name__ == "__main__":
    if sys.argv[1:] == ["--download"]:
        download()
    else:
        os.environ["HF_HUB_OFFLINE"] = "1"   # never download while reading: only ./setup.sh --emotion does
        serve()
