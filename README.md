# ClearRead

Live demo: https://clearread-zq0c.onrender.com (the first load may take up to a minute)

Finds text hidden inside PDF resumes: text a person cannot see on the page,
but an AI screening tool reads after text extraction.

You upload a .pdf resume. ClearRead lists the hidden text it finds, shows where it sits on the
page, and can build a clean copy without it. Files aren't stored after checking.

## What counts as hidden text

`detector.py` flags a text span when any of these checks applies:

- the text colour is nearly identical to the background behind it
- the font size is below 4 pt
- the text is fully transparent or set to invisible
- the text is covered by something or isn't drawn on the page
- the text is placed outside the visible page area

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
      "findings": [ { "page", "text", "lines", "reasons", "note", "bbox", "font_size", "color" } ],
      "notes": [ "..." ] }

`notes` lists extra remarks, for example pages without a text layer that weren't checked.

`POST /api/preview` takes the same upload and returns `{ "pages": [ { "page", "width", "height", "outside", "normal", "marked", "findings" } ], "truncated" }`
for each page with findings. `normal` and `marked` are .png data URLs: the page as a person sees it, and the
same page with a red outline and light fill around each hidden finding (no text is drawn). `findings` lists, for
each finding, its `index` in the scan result and a `crop` with a close-up `box` (page points) and `normal` and
`marked` close-up images. The browser draws the hidden text itself, so any script displays correctly.

To keep the response small and fast, the work is limited (`MAX_PAGES` and `MAX_CROPS` in `preview.py`):
at most 10 pages are rendered and at most 50 findings get a close-up per request. When a limit is hit,
`truncated` is `true`. Findings past the close-up limit keep their entry in `findings` with `"crop": null`.
Findings on pages past the page limit are left out of `pages`, but they are still in the scan result.
Below the limits `truncated` is `false`. The app shows a short note when `truncated` is `true`.

The "Show on page" button in the app uses this. It opens two page images side by side: "What a person sees"
and "What the AI reads". On the second one the browser draws a numbered selection-blue box over each hidden
finding, and the selected finding gets a thicker outline. Click a finding in the list, or use the page
arrows, to change the selection. Under the pages, "Zoomed in" shows a close-up of the selected finding in both
versions, with only that finding highlighted, and the hidden text itself below it. Closing the view clears
the selection.

`POST /api/clean` takes the same upload and returns a new PDF with the hidden text removed
(redactions limited to the flagged spans, no visible fill; the original is never modified).
Before returning the file the server re-scans it (must give zero findings) and checks that the
visible text is unchanged. If a check fails it returns HTTP 400 saying which one.

## Test

    .venv/bin/python -m pytest

## Files

- `detector.py` - the detection checks
- `cleaner.py` - builds the clean copy and runs the safety checks
- `main.py` - FastAPI app
- `preview.py` - page images and close-ups for "Show on page"
- `static/index.html` - the whole frontend
- `tests/test_scan.py` - the test suite
- `samples/` - test resumes: clean, dirty (hidden text), gap (cases the checks must still catch), edge cases
  (scanned image, encrypted), multi-page and multi-language files
