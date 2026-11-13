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
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
os.environ.setdefault("HF_HOME", str(ROOT / "models" / "emotion"))
os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")   # the odd operation the Mac GPU lacks
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
                reply({"ok": True, "heard": heard.strip()})
            else:
                reply({"ok": False, "error": f"unknown request {req.get('cmd')!r}"})
        except Exception as e:   # one bad line shouldn't end the whole book
            reply({"ok": False, "error": f"{type(e).__name__}: {e}"[:400]})


if __name__ == "__main__":
    if sys.argv[1:] == ["--download"]:
        download()
    else:
        os.environ.setdefault("HF_HUB_OFFLINE", "1")   # everything was fetched at setup
        serve()
