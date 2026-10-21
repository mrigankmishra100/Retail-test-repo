"""Extract the native text layer without generating descriptions of page images."""

from io import BytesIO

from pypdf import PdfReader
from pypdf.errors import PyPdfError


class PDFTextError(ValueError):
    pass


def extract_pdf_text(content: bytes) -> str:
    try:
        reader = PdfReader(BytesIO(content))
        if reader.is_encrypted:
            raise PDFTextError("Upload an unencrypted, searchable PDF or a text version.")
        if not 1 <= len(reader.pages) <= 200:
            raise PDFTextError("Use a PDF with between 1 and 200 pages.")
        pages, total = [], 0
        for number, page in enumerate(reader.pages, 1):
            text = (page.extract_text() or "").replace("\x00", "").strip()
            if not text:
                raise PDFTextError(
                    f"Page {number} has no readable text layer. Run OCR and upload a searchable PDF, "
                    "or upload a TXT/Markdown version."
                )
            total += len(text)
            if total > 2_000_000:
                raise PDFTextError("The extracted PDF text is too large. Split it into smaller documents.")
            pages.append(f"[Page {number}]\n{text}")
        return "\n\n".join(pages)
    except PyPdfError as exc:
        raise PDFTextError(
            "The PDF text could not be read. Upload a searchable PDF or text version."
        ) from exc
