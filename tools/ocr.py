"""
ocr.py
------
Gemini Vision OCR — extracts text from images and scanned documents.

Uses the Google Generative AI SDK directly (not LangChain) for native
multimodal support with PDFs and images.

Public API:
    ocr_document(file_bytes, mime_type) -> str
"""

import base64
import logging

log = logging.getLogger("tools.ocr")

# Supported MIME types for Gemini Vision
SUPPORTED_MIME_TYPES = frozenset({
    "application/pdf",
    "image/jpeg",
    "image/png",
    "image/webp",
    "image/gif",
})


def ocr_document(file_bytes: bytes, mime_type: str = "application/pdf") -> str:
    """
    Use Gemini Vision to extract text from a document or image.

    Sends the file bytes directly to Gemini's multimodal API.
    No local OCR engine required — no extra dependencies.

    Args:
        file_bytes: Raw bytes of the file to process.
        mime_type:  MIME type of the file. Supported values:
                    "application/pdf", "image/jpeg", "image/png",
                    "image/webp", "image/gif".

    Returns:
        Extracted text string. Returns an error string on failure.

    Example:
        with open("resume.pdf", "rb") as f:
            text = ocr_document(f.read(), "application/pdf")
    """
    if mime_type not in SUPPORTED_MIME_TYPES:
        return f"[OCR: unsupported MIME type '{mime_type}']"

    log.info("[ocr] Processing document: mime_type=%s, size=%d bytes", mime_type, len(file_bytes))

    try:
        import google.generativeai as genai
        from discord_bot.config import GEMINI_API_KEY

        genai.configure(api_key=GEMINI_API_KEY)
        model = genai.GenerativeModel("gemini-2.5-flash")

        encoded = base64.b64encode(file_bytes).decode("utf-8")

        response = model.generate_content([
            {
                "inline_data": {
                    "mime_type": mime_type,
                    "data": encoded,
                }
            },
            (
                "Extract ALL text from this document exactly as it appears. "
                "Preserve formatting, bullet points, and section headers. "
                "Do not summarize or interpret. Return plain text only."
            ),
        ])

        text = response.text.strip()
        log.info("[ocr] Extracted %d characters", len(text))
        return text

    except Exception as exc:
        log.exception("[ocr] Gemini Vision OCR failed")
        return f"[OCR failed: {exc}]"


def extract_text_with_ocr_fallback(file_bytes: bytes, mime_type: str = "application/pdf") -> str:
    """
    Try standard text extraction first; fall back to Gemini Vision OCR
    if the result is empty (e.g. scanned/image-based PDF).

    Args:
        file_bytes: Raw bytes of the PDF or image.
        mime_type:  MIME type of the file.

    Returns:
        Extracted text string.
    """
    if mime_type == "application/pdf":
        from tools.document import extract_pdf_text
        text = extract_pdf_text(file_bytes).strip()
        if len(text) > 100:  # meaningful text extracted
            log.info("[ocr] Standard PDF extraction succeeded (%d chars)", len(text))
            return text
        log.info("[ocr] Standard PDF extraction empty/small — falling back to Gemini Vision")

    return ocr_document(file_bytes, mime_type)
