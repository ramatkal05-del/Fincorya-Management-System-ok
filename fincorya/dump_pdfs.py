"""Dump full text of each preview PDF for content review."""
import os
import fitz

OUT = "output/pdf"
FILES = [
    "preview-agent-daily.pdf",
    "preview-finance-weekly.pdf",
    "preview-admin-monthly.pdf",
    "preview-admin-result.pdf",
]

for name in FILES:
    doc = fitz.open(os.path.join(OUT, name))
    print(f"\n{'='*70}\n{name} ({doc.page_count} pages)\n{'='*70}")
    for i in range(doc.page_count):
        text = doc[i].get_text()
        print(f"\n--- Page {i+1} ---\n{text}")
    doc.close()
