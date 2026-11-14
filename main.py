from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response

import fitz

import cleaner
import detector

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="ClearRead")


SIGNATURE_BYTES = 1024
MSG_TOO_BIG = "The file is larger than 10 MB."
MSG_EMPTY = "The file is empty."
MSG_NO_EXTENSION = "Only .pdf files are supported. This file has no extension."
MSG_DAMAGED = "This .pdf file can't be opened. It may be damaged or renamed."
MSG_ENCRYPTED = "This .pdf file is password-protected. Remove the password and upload it again."
MSG_NO_TEXT = "No text layer found. This looks like a scanned image."


def msg_wrong_extension(name):
    """Message for an upload whose name does not end in .pdf."""
    ext = (name or "").rpartition(".")[2].lower() if "." in (name or "") else ""
    if not ext:
        return MSG_NO_EXTENSION
    return f"Only .pdf files are supported. This is a .{ext} file."


def looks_like_pdf(head):
    """True when the first bytes start with the %PDF- signature (leading whitespace allowed)."""
    return head.lstrip().startswith(b"%PDF-")


def validate_upload(data):
    """Check an upload in a fixed order; raise HTTPException(400) at the first problem."""
    if len(data) == 0:
        raise HTTPException(400, MSG_EMPTY)
    if not looks_like_pdf(data[:SIGNATURE_BYTES]):
        raise HTTPException(400, MSG_DAMAGED)
    try:
        doc = fitz.open(stream=data, filetype="pdf")
    except Exception:
        raise HTTPException(400, MSG_DAMAGED)
    if doc.is_repaired:  # MuPDF had to fix a format or syntax error, e.g. a truncated file
        doc.close()
        raise HTTPException(400, MSG_DAMAGED)
    try:
        if doc.needs_pass:
            raise HTTPException(400, MSG_ENCRYPTED)
        if not has_text_layer(doc):
            raise HTTPException(400, MSG_NO_TEXT)
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(400, MSG_DAMAGED)
    finally:
        doc.close()


def page_has_text(page):
    """True when the page holds at least one non-blank text character."""
    return bool(page.get_text("text", clip=fitz.INFINITE_RECT()).strip())


def has_text_layer(doc):
    """True when any page holds at least one non-blank text character."""
    return any(page_has_text(page) for page in doc)


def scan_notes(data):
    """Extra remarks about a scan, as a list of strings (empty when there are none)."""
    doc = fitz.open(stream=data, filetype="pdf")
    try:
        skipped = [page.number + 1 for page in doc if not page_has_text(page)]
    finally:
        doc.close()
    if not skipped:
        return []
    if len(skipped) == 1:
        return [f"Page {skipped[0]} has no text layer and wasn't checked."]
    pages = ", ".join(str(n) for n in skipped)
    return [f"Pages {pages} have no text layer and weren't checked."]


async def read_upload(file):
    """Read an upload and validate it in the fixed order; return its bytes."""
    # Check order: empty, wrong name / not a PDF (first bytes only), too big, then the rest.
    head = await file.read(SIGNATURE_BYTES)
    if len(head) == 0:
        raise HTTPException(400, MSG_EMPTY)
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(400, msg_wrong_extension(file.filename))
    if not looks_like_pdf(head):
        raise HTTPException(400, MSG_DAMAGED)
    data = head + await file.read(MAX_UPLOAD_BYTES + 1 - len(head))
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, MSG_TOO_BIG)
    validate_upload(data)
    return data


@app.post("/api/scan")
async def scan(file: UploadFile = File(...)):
    data = await read_upload(file)
    try:
        findings = detector.scan_pdf(data)
        notes = scan_notes(data)
    except Exception:  # PyMuPDF raises various errors for broken files
        raise HTTPException(400, MSG_DAMAGED)
    return {
        "filename": file.filename,
        "status": "hidden_text_found" if findings else "clean",
        "findings": findings,
        "notes": notes,
    }


@app.post("/api/clean")
async def clean(file: UploadFile = File(...)):
    """Return a copy of the PDF without its hidden text, or 400 if a safety check fails."""
    data = await read_upload(file)
    try:
        cleaned = cleaner.clean_pdf(data)
    except cleaner.CleanError as err:
        raise HTTPException(400, str(err))
    except Exception:
        raise HTTPException(400, MSG_DAMAGED)
    name = "clean_" + (file.filename or "resume.pdf")
    return Response(cleaned, media_type="application/pdf",
                    headers={"Content-Disposition": "attachment; filename*=UTF-8''" + quote(name)})


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")
