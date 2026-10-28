"""book2audio: a video (or photos, or screenshots) of book pages -> an audiobook (.m4b)
with chapter markers, plus a transcript and short chapter summaries. A scientific paper's
PDF works too (beta): one chapter per section, figures and tables described.

Use it only on books you own, public-domain books, your own writing, or papers you're allowed
to read, and keep what it makes to yourself: the audiobook and transcript are copies."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]   # the checkout: models/, output/, the ocr helper
