"""Produce a clean copy of a PDF: remove hidden text, keep everything else.

Hidden text is removed with PyMuPDF redactions limited to the bounding boxes of
the spans the detector flagged. The redactions have no fill and leave images
and drawings alone, so the visible page looks identical (a rectangle that
covered hidden text stays where it is).
"""
import fitz  # PyMuPDF

import detector

MSG_STILL_HIDDEN = "Safety check failed: the cleaned PDF still contains hidden text."
MSG_TEXT_CHANGED = "Safety check failed: the visible text of the cleaned PDF differs from the original."


class CleanError(Exception):
    """Raised when a safety check fails; the message says which one."""


def normalize(texts):
    """Visible text of a page as one string without whitespace (spans may be split differently)."""
    return "".join("".join(texts).split())


def clean_pdf(data):
    """Return the bytes of a copy of the PDF with all hidden text removed.

    Raises CleanError when the cleaned copy fails a safety check:
      a. re-scanning it must give zero findings
      b. its visible text must equal that of the original
    """
    doc = fitz.open(stream=data, filetype="pdf")  # the upload is read from memory, never modified on disk
    try:
        original_visible = []
        for page in doc:
            flagged, visible = detector.classify_page(page)
            original_visible.append(normalize(visible))
            for span, _ in flagged:
                page.add_redact_annot(fitz.Rect(span["bbox"]), fill=False)
            if flagged:
                page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE,
                                      graphics=fitz.PDF_REDACT_LINE_ART_NONE,
                                      text=fitz.PDF_REDACT_TEXT_REMOVE)
        cleaned = doc.tobytes(garbage=3, deflate=True)
    finally:
        doc.close()

    check = fitz.open(stream=cleaned, filetype="pdf")
    try:
        cleaned_visible = []
        for page in check:
            flagged, visible = detector.classify_page(page)
            if flagged:
                raise CleanError(MSG_STILL_HIDDEN)
            cleaned_visible.append(normalize(visible))
    finally:
        check.close()
    if cleaned_visible != original_visible:
        raise CleanError(MSG_TEXT_CHANGED)
    return cleaned
