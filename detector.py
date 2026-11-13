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
    findings = []
    for line in iter_lines(page):
        group = []
        for span in line:
            reasons = check_span(span, page.rect, pixmap)
            if reasons:
                group.append((span, reasons))
            elif group:
                findings.append(make_finding(page.number + 1, group))
                group = []
        if group:
            findings.append(make_finding(page.number + 1, group))
    return findings


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

def make_finding(page_number, group):
    """Merge neighbouring flagged spans of one line into a single finding."""
    spans = [s for s, _ in group]
    reasons = []
    for _, span_reasons in group:
        for reason in span_reasons:
            if reason not in reasons:
                reasons.append(reason)
    boxes = [fitz.Rect(s["bbox"]) for s in spans]
    union = boxes[0]
    for box in boxes[1:]:
        union |= box
    return {
        "page": page_number,
        "text": "".join(s["text"] for s in spans).strip(),
        "reason": "; ".join(reasons),
        "bbox": [round(v, 1) for v in union],
        "font_size": round(min(s["size"] for s in spans), 1),
        "color": "#%06x" % spans[0]["color"],
    }
