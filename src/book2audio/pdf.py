"""Reading PDFs (papers): each page's embedded text layer and an image of the page, from the
PDFKit helper (pdf.swift)."""
import json
import subprocess
import sys
from pathlib import Path

from . import ROOT
from .capture import ensure_helper

PDF_SRC = Path(__file__).with_name("pdf.swift")
PDF_BIN = ROOT / "pdfpages"


def read_pdf(path, image_dir, dpi=140):
    """Returns (info, pages). info: {"title", "author", "pages"}. Each page: {"page", "width",
    "height", "image" (path to its PNG), "text" (text layer, "" if scanned), "lines": [...]}."""
    ensure_helper(PDF_SRC, PDF_BIN, "PDF")
    r = subprocess.run([str(PDF_BIN), str(path), str(image_dir), "--dpi", str(dpi)],
                       capture_output=True, text=True)
    if r.returncode:
        sys.exit(f"Couldn't read {path}: {r.stderr.strip()[:300] or f'exit code {r.returncode}'}")
    recs = [json.loads(l) for l in r.stdout.splitlines() if l.strip()]
    info, pages = recs[0], recs[1:]
    for p in pages:
        p["image"] = Path(image_dir) / p["image"]
    return info, pages
