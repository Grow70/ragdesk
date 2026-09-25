# PDF parser test fixture

`pdf_mixed.pdf` is a small synthetic four-page PDF with no real company data:

1. Chinese text containing `680 元`, `2026-09-25`, and `NX-210-P`.
2. A physically blank page.
3. A displayed checkerboard image with no text layer, representing an image-only page.
4. Chinese text containing `15 个自然日`.

The text pages were exported from local HTML with LibreOffice and Noto CJK fonts; the blank and image-only pages were added with pypdf. Tests derive text-only, no-text, and encrypted variants from this fixed file. The image is synthetic and does not require or imply OCR.
