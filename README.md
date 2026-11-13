# ClearRead

Finds text hidden inside PDF resumes: text a person cannot see on the page,
but an AI screening tool reads after text extraction.

Step 1 (done): upload a PDF, detect hidden text, show the findings.
Not implemented yet: producing a cleaned copy of the file, storing results.

## Run

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

## Test

    .venv/bin/python -m pytest

## Files

- `detector.py` - the three detection rules
- `main.py` - FastAPI app
- `static/index.html` - the whole frontend
- `samples/` - one clean resume and one with hidden text
