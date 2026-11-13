from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse

import detector

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="ClearRead")


@app.post("/api/scan")
async def scan(file: UploadFile = File(...)):
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "File is larger than 10 MB")
    if not data.startswith(b"%PDF"):
        raise HTTPException(400, "The file is not a PDF")
    try:
        findings = detector.scan_pdf(data)
    except Exception as exc:  # PyMuPDF raises various errors for broken files
        raise HTTPException(400, "Could not read the PDF: %s" % exc)
    return {
        "filename": file.filename,
        "status": "hidden_text_found" if findings else "clean",
        "findings": findings,
    }


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")
