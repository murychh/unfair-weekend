# ClearRead

Finds text hidden inside PDF resumes: text a person cannot see on the page,
but an AI screening tool reads after text extraction.

Step 1 (done): upload a PDF, detect hidden text, show the findings.
Not implemented yet: producing a cleaned copy of the file, storing results.

## Run

On macOS / Linux (Terminal):

    python3 -m venv .venv
    .venv/bin/pip install -r requirements.txt
    .venv/bin/uvicorn main:app --reload

On Windows (Command Prompt or PowerShell):

    python -m venv .venv
    .venv\Scripts\activate
    pip install -r requirements.txt
    python -m uvicorn main:app --port 8000

Open http://127.0.0.1:8000 and drop a PDF on the page.

## API

`POST /api/scan` with a multipart field `file` (PDF, up to 10 MB) returns:

    { "filename", "status": "clean" | "hidden_text_found",
      "findings": [ { "page", "text", "lines", "reasons", "note", "bbox", "font_size", "color" } ] }

`POST /api/clean` takes the same upload and returns a new PDF with the hidden text removed
(redactions limited to the flagged spans, no visible fill; the original is never modified).
Before returning the file the server re-scans it (must give zero findings) and checks that the
visible text is unchanged. If a check fails it returns HTTP 400 saying which one.

## Test

    .venv/bin/python -m pytest

## Files

- `detector.py` - the three detection rules
- `cleaner.py` - builds the clean copy and runs the safety checks
- `main.py` - FastAPI app
- `static/index.html` - the whole frontend
- `samples/` - one clean resume and one with hidden text
