"""PDF text layer extraction: the one implementation the workflow and the eval harness share.

Why this exists instead of n8n's `Extract From File` node: that node space-joins every cell of a
table row (`Batteries AA 24pk 8 PACK 21.90 175.20 17.52`), so the model cannot tell where a
description ends or which number is the line total and which is the GST. pypdf emits one cell
per line, which the same model reads at 100% on the dev set versus 91% with n8n's text (see
docs/decisions.md). Same code on both sides means the eval measures what runs.
"""

import io

from pypdf import PdfReader


def extract_text(content: bytes) -> tuple[str, int]:
    """Return (text, page_count). Raises on a PDF pypdf cannot read (corrupt, encrypted)."""
    reader = PdfReader(io.BytesIO(content))
    if reader.is_encrypted:
        raise ValueError("PDF is encrypted")
    pages = [page.extract_text() or "" for page in reader.pages]
    return "\n".join(pages), len(pages)
