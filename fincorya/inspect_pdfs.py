"""Inspect generated PDF previews: page count, text presence, basic layout checks."""
import os
import sys

import fitz  # PyMuPDF

OUT = "output/pdf"
FILES = [
    "preview-agent-daily.pdf",
    "preview-finance-weekly.pdf",
    "preview-admin-monthly.pdf",
    "preview-admin-result.pdf",
]

EXPECTED = {
    "preview-agent-daily.pdf": ["FINCORYA", "Agent", "2026"],
    "preview-finance-weekly.pdf": ["FINCORYA", "Trésorerie", "2026"],
    "preview-admin-monthly.pdf": ["FINCORYA", "2026", "Septembre"],
    "preview-admin-result.pdf": ["FINCORYA", "Résultat", "2026"],
}

for name in FILES:
    path = os.path.join(OUT, name)
    doc = fitz.open(path)
    pages = doc.page_count
    text = "".join(page.get_text() for page in doc)
    print(f"=== {name} ===")
    print(f"pages={pages} chars={len(text)}")
    missing = [tok for tok in EXPECTED.get(name, []) if tok.lower() not in text.lower()]
    print("missing_tokens=", missing)
    # First page dimensions
    p0 = doc[0]
    print(f"page0_size={p0.rect.width:.0f}x{p0.rect.height:.0f}")
    # Show first 400 chars of text
    snippet = " ".join(text.split())[:400]
    print("snippet:", snippet)
    print()
    doc.close()
