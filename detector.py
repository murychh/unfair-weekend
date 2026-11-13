"""Detection of text that is hidden in a PDF but readable by text extraction.

Three rules are applied to every text span:
  a. the text colour is almost the same as the page background behind it
  b. the font size is tiny
  c. the span lies outside the visible page area
"""
import statistics

import fitz  # PyMuPDF

MIN_FONT_SIZE = 4.0      # points; smaller text is flagged
COLOR_DISTANCE = 40.0    # RGB distance (0..441); smaller means "same colour"
RENDER_ZOOM = 2.0        # page render scale used for background sampling
RING_PIXELS = 3          # width of the background sample ring around a span
MAX_SAMPLES_PER_STRIP = 200

BASELINE_TOLERANCE = 1.0  # points; flagged spans this close in y share a line
GAP_FACTOR = 0.25         # a space is inserted when the gap exceeds this share of the font size

NOTE_UNREADABLE = ("Text could not be fully extracted from this file's font. "
                   "Hidden content is present but partly unreadable.")

REASON_COLOR = "Text colour is nearly identical to the background, so a person cannot see it"
REASON_TINY = "Font size is below 4 pt, so a person cannot read it"
REASON_OFFPAGE = "Text is placed outside the visible page area"

# PyMuPDF silently drops text outside the page unless it is given an explicit
# clip rectangle. We pass an infinite one, otherwise rule c could never fire.
EXTRACT_FLAGS = fitz.TEXT_PRESERVE_LIGATURES | fitz.TEXT_PRESERVE_WHITESPACE


def scan_pdf(data):
    """Scan PDF bytes. Returns a list of findings (dicts)."""
    doc = fitz.open(stream=data, filetype="pdf")
    findings = []
    for page in doc:
        findings.extend(scan_page(page))
    doc.close()
    return findings


def scan_page(page):
    """Return the findings of a single page."""
    pixmap = page.get_pixmap(matrix=fitz.Matrix(RENDER_ZOOM, RENDER_ZOOM), alpha=False)
    flagged = []
    for line in iter_lines(page):
        for span in line:
            reasons = check_span(span, page.rect, pixmap)
            if reasons:
                flagged.append((span, reasons))
    return [make_finding(page.number + 1, group) for group in group_by_baseline(flagged)]


def group_by_baseline(flagged):
    """Group flagged spans whose baselines are within BASELINE_TOLERANCE of each other."""
    groups = []
    for item in sorted(flagged, key=lambda it: it[0]["origin"][1]):
        y = item[0]["origin"][1]
        if groups and y - groups[-1][0][0]["origin"][1] <= BASELINE_TOLERANCE:
            groups[-1].append(item)
        else:
            groups.append([item])
    return groups


def iter_lines(page):
    """Yield each text line of the page as a list of non-empty spans."""
    for block in page.get_text("dict", flags=EXTRACT_FLAGS, clip=fitz.INFINITE_RECT())["blocks"]:
        for line in block.get("lines", []):
            spans = [s for s in line["spans"] if s["text"].strip()]
            if spans:
                yield spans


def check_span(span, page_rect, pixmap):
    """Return the list of reasons why a span is hidden (empty if visible)."""
    reasons = []
    if is_off_page(span["bbox"], page_rect):
        reasons.append(REASON_OFFPAGE)
    if is_tiny(span["size"]):
        reasons.append(REASON_TINY)
    if matches_background(span, pixmap):
        reasons.append(REASON_COLOR)
    return reasons


# Rule b

def is_tiny(size):
    return size < MIN_FONT_SIZE


# Rule c

def is_off_page(bbox, page_rect):
    """True when the span box does not touch the visible page at all."""
    return not fitz.Rect(bbox).intersects(page_rect)


# Rule a

def matches_background(span, pixmap):
    """True when the text colour is nearly the same as the surrounding background."""
    background = sample_background(pixmap, span["bbox"])
    if background is None:
        return False
    return color_distance(int_to_rgb(span["color"]), background) < COLOR_DISTANCE


def sample_background(pixmap, bbox):
    """Median colour of a thin ring of pixels around the span box.

    The ring is used instead of the box itself because the box contains the
    text, which would pollute the measurement. Returns None if the ring is
    outside the rendered page.
    """
    x0, y0, x1, y1 = [int(v * RENDER_ZOOM) for v in bbox]
    r = RING_PIXELS
    strips = [
        (x0 - r, y0 - r, x1 + r, y0),   # above
        (x0 - r, y1, x1 + r, y1 + r),   # below
        (x0 - r, y0, x0, y1),           # left
        (x1, y0, x1 + r, y1),           # right
    ]
    pixels = []
    for strip in strips:
        pixels.extend(read_pixels(pixmap, *strip))
    if not pixels:
        return None
    return tuple(statistics.median(p[i] for p in pixels) for i in range(3))


def read_pixels(pixmap, x0, y0, x1, y1):
    """Read RGB pixels of a rectangle (clipped to the image), subsampled."""
    x0, y0 = max(x0, 0), max(y0, 0)
    x1, y1 = min(x1, pixmap.width), min(y1, pixmap.height)
    if x1 <= x0 or y1 <= y0:
        return []
    step_x = max(1, (x1 - x0) // MAX_SAMPLES_PER_STRIP)
    step_y = max(1, (y1 - y0) // MAX_SAMPLES_PER_STRIP)
    return [pixmap.pixel(x, y)[:3]
            for y in range(y0, y1, step_y)
            for x in range(x0, x1, step_x)]


def int_to_rgb(value):
    return ((value >> 16) & 255, (value >> 8) & 255, value & 255)


def color_distance(a, b):
    return sum((x - y) ** 2 for x, y in zip(a, b)) ** 0.5


# Output

def join_spans(spans):
    """Join spans (sorted by x) into text; add a space only across a wide gap."""
    text = spans[0]["text"]
    for prev, span in zip(spans, spans[1:]):
        gap = span["bbox"][0] - prev["bbox"][2]
        if gap > GAP_FACTOR * span["size"] and not (text[-1:].isspace() or span["text"][:1].isspace()):
            text += " "
        text += span["text"]
    return text.strip()


def looks_unreadable(text):
    """True when extracted text looks broken: replacement characters or mostly single letters."""
    if not text:
        return False
    if (text.count("\ufffd") + sum(1 for c in text if 0xE000 <= ord(c) <= 0xF8FF)) / len(text) > 0.2:
        return True
    words = text.split()
    if len(words) < 4:
        return False
    single = sum(1 for w in words if len(w) == 1)
    return single / len(words) > 0.4


def make_finding(page_number, group):
    """Merge flagged spans sharing one baseline into a single finding."""
    group = sorted(group, key=lambda it: it[0]["bbox"][0])
    spans = [s for s, _ in group]
    reasons = []
    for _, span_reasons in group:
        for reason in span_reasons:
            if reason not in reasons:
                reasons.append(reason)
    union = fitz.Rect(spans[0]["bbox"])
    for s in spans[1:]:
        union |= fitz.Rect(s["bbox"])
    text = join_spans(spans)
    return {
        "page": page_number,
        "text": text,
        "reasons": reasons,
        "note": NOTE_UNREADABLE if looks_unreadable(text) else None,
        "bbox": [round(v, 1) for v in union],
        "font_size": round(min(s["size"] for s in spans), 1),
        "color": "#%06x" % spans[0]["color"],
    }
