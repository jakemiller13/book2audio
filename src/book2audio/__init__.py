"""book2audio: a video (or photos, or screenshots) of book pages -> an audiobook (.m4b)
with chapter markers, plus a transcript and short chapter summaries.

Use it only on books you own, public-domain books, or your own writing, and keep what it
makes to yourself: the audiobook, transcript and summaries are copies of the book."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]   # the checkout: models/, output/, the ocr helper
