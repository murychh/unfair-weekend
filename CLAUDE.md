# ClearRead

ClearRead is a FastAPI + PyMuPDF app that finds hidden text in .pdf resumes and produces a clean copy.

## Rules

- Never change detection or cleaning logic unless the task explicitly asks for it.
- After every change, run the full pytest suite (`.venv/bin/python -m pytest`). All tests must pass. Then commit and push.
- Keep requirements.txt pinned to exact versions and cross-platform (Windows, macOS, Linux). Use environment markers for platform-specific packages.
- Test samples live in samples/.

## Text style

Applies to anything the user reads: the UI, finding reasons, error messages.

- Use contractions (can't, isn't, don't, doesn't, won't).
- Write file types as lower-case extensions with a dot (.pdf, .png), never "PDF" or "PNG".
- Write "resume" without accents.
- No em dashes or en dashes.
- Short, plain sentences.
