"""Render first page of each preview PDF as PNG for visual inspection."""
import os
import fitz

OUT = "output/pdf"
PREVIEW = "output/pdf/preview_images"
os.makedirs(PREVIEW, exist_ok=True)

FILES = [
    "preview-agent-daily.pdf",
    "preview-finance-weekly.pdf",
    "preview-admin-monthly.pdf",
    "preview-admin-result.pdf",
]

for name in FILES:
    doc = fitz.open(os.path.join(OUT, name))
    for i in range(min(doc.page_count, 3)):
        page = doc[i]
        pix = page.get_pixmap(dpi=110)
        out = os.path.join(PREVIEW, f"{name[:-4]}-p{i+1}.png")
        pix.save(out)
        print(out)
    doc.close()
