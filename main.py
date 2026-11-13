from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse

import fitz

import detector

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="ClearRead")


SIGNATURE_BYTES = 1024
MSG_TOO_BIG = "File is larger than 10 MB."
MSG_EMPTY = "The file is empty."
MSG_NOT_PDF = "This file is not a PDF."
MSG_ENCRYPTED = "This PDF is password-protected. Please remove the password and upload it again."
MSG_NO_TEXT = "No text layer found \u2014 this looks like a scanned image."


def looks_like_pdf(head):
    """True when the first bytes start with the %PDF- signature (leading whitespace allowed)."""
    return head.lstrip().startswith(b"%PDF-")


def validate_upload(data):
    """Check an upload in a fixed order; raise HTTPException(400) at the first problem."""
    if len(data) == 0:
        raise HTTPException(400, MSG_EMPTY)
    if not looks_like_pdf(data[:SIGNATURE_BYTES]):
        raise HTTPException(400, MSG_NOT_PDF)
    try:
        doc = fitz.open(stream=data, filetype="pdf")
    except Exception:
        raise HTTPException(400, MSG_NOT_PDF)
    try:
        if doc.needs_pass:
            raise HTTPException(400, MSG_ENCRYPTED)
        if not has_text_layer(doc):
            raise HTTPException(400, MSG_NO_TEXT)
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(400, MSG_NOT_PDF)
    finally:
        doc.close()


def has_text_layer(doc):
    """True when any page holds at least one non-blank text character."""
    return any(page.get_text("text", clip=fitz.INFINITE_RECT()).strip() for page in doc)


@app.post("/api/scan")
async def scan(file: UploadFile = File(...)):
    # Check order: empty, not a PDF (first bytes only), too big, then the rest.
    head = await file.read(SIGNATURE_BYTES)
    if len(head) == 0:
        raise HTTPException(400, MSG_EMPTY)
    if not looks_like_pdf(head):
        raise HTTPException(400, MSG_NOT_PDF)
    data = head + await file.read(MAX_UPLOAD_BYTES + 1 - len(head))
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, MSG_TOO_BIG)
    validate_upload(data)
    try:
        findings = detector.scan_pdf(data)
    except Exception:  # PyMuPDF raises various errors for broken files
        raise HTTPException(400, MSG_NOT_PDF)
    return {
        "filename": file.filename,
        "status": "hidden_text_found" if findings else "clean",
        "findings": findings,
    }


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")
