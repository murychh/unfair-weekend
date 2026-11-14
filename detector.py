"""Detection of text that is hidden in a PDF but readable by text extraction.

Three rules are applied to every text span:
  a. the text colour is almost the same as the page background behind it
  b. the font size is tiny
  c. the span lies outside the visible page area
"""
import re
from bisect import bisect_left, bisect_right

import fitz  # PyMuPDF

MIN_FONT_SIZE = 4.0      # points; smaller text is flagged
COLOR_DISTANCE = 40.0    # RGB distance (0..441); smaller means "same colour"
MIN_VISIBLE_CONTRAST = 100.0  # rule e: text must differ at least this much from the background to be tested
MIN_INK_SHARE = 0.005         # rule e: fewer text-coloured pixels than this share of the box = not drawn
RENDER_ZOOM = 2.0        # page render scale used for background sampling
RING_PIXELS = 3          # width of the background sample ring around a span
MAX_SAMPLES_PER_STRIP = 200

LINE_TOLERANCE = 0.3      # spans whose baselines differ by less than this x font size share a line
BLOCK_GAP = 2.0           # lines closer than this x font size (edge to edge) form one block
GAP_FACTOR = 0.1          # between spans a space is inserted when the gap exceeds this share of the font size

NOTE_UNREADABLE = ("Text couldn't be fully extracted from this file's font. "
                   "Hidden content is present but partly unreadable.")

REASON_COLOR = "Text colour is nearly identical to the background, so a person can't see it"
REASON_TINY = "Font size is below 4 pt, so a person can't read it"
REASON_TRANSPARENT = "Text is fully transparent or set to invisible, so a person can't see it."
REASON_NOT_DRAWN = "Text is covered or not drawn on the page, so a person can't see it."
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
    flagged, _ = classify_page(page)
    return [make_finding(page.number + 1, group) for group in group_by_baseline(flagged)]


def classify_page(page):
    """Split the spans of a page into hidden and visible ones.

    Returns (flagged, visible): flagged is a list of (span, reasons), visible a
    list of span texts in reading order.
    """
    pixmap = page.get_pixmap(matrix=fitz.Matrix(RENDER_ZOOM, RENDER_ZOOM), alpha=False)
    flagged, visible = [], []
    for line in iter_lines(page):
        for span in line:
            reasons = check_span(span, page.rect, pixmap)
            if reasons:
                flagged.append((span, reasons))
            else:
                visible.append(span["text"])
    return flagged, visible


def group_by_baseline(flagged):
    """Group flagged spans into lines, then lines into blocks.

    Two spans share a line when their baselines differ by less than
    LINE_TOLERANCE x font size. Consecutive lines form one block when they are
    vertically adjacent (gap < BLOCK_GAP x font size) and have the same reasons.
    Returns a list of blocks; a block is a list of lines; a line is a list of
    (span, reasons) sorted by x.
    """
    lines = []
    for item in sorted(flagged, key=lambda it: it[0]["origin"][1]):
        span = item[0]
        if lines and span["origin"][1] - lines[-1][0][0]["origin"][1] < LINE_TOLERANCE * span["size"]:
            lines[-1].append(item)
        else:
            lines.append([item])
    lines = [sorted(line, key=lambda it: it[0]["bbox"][0]) for line in lines]

    blocks = []
    for line in lines:
        if blocks and same_block(blocks[-1][-1], line):
            blocks[-1].append(line)
        else:
            blocks.append([line])
    return blocks


def line_reasons(line):
    return {r for _, reasons in line for r in reasons}


def line_bounds(line):
    top = min(s["bbox"][1] for s, _ in line)
    bottom = max(s["bbox"][3] for s, _ in line)
    size = max(s["size"] for s, _ in line)
    return top, bottom, size


def same_block(prev, line):
    """True when `line` directly follows `prev` and is hidden for the same reasons."""
    if line_reasons(prev) != line_reasons(line):
        return False
    _, prev_bottom, prev_size = line_bounds(prev)
    top, _, size = line_bounds(line)
    return top - prev_bottom < BLOCK_GAP * max(prev_size, size)


def iter_lines(page):
    """Yield each text line of the page as a list of non-empty spans."""
    type3 = sorted_by_y(type3_glyph_colors(page))
    for block in page.get_text("dict", flags=EXTRACT_FLAGS, clip=fitz.INFINITE_RECT())["blocks"]:
        for line in block.get("lines", []):
            spans = [s for s in line["spans"] if s["text"].strip()]
            for span in spans:
                fix_type3_color(span, type3)
            if spans:
                yield spans


def check_span(span, page_rect, pixmap):
    """Return the list of reasons why a span is hidden (empty if visible)."""
    reasons = []
    background = sample_background(pixmap, span["bbox"])
    if is_off_page(span["bbox"], page_rect):
        reasons.append(REASON_OFFPAGE)
    if is_tiny(span["size"]):
        reasons.append(REASON_TINY)
    if matches_background(span, pixmap, background):
        reasons.append(REASON_COLOR)
    if is_transparent(span):
        reasons.append(REASON_TRANSPARENT)
    if not reasons and is_not_drawn(span, pixmap, background):
        reasons.append(REASON_NOT_DRAWN)
    return reasons


# Colour of Type 3 fonts
#
# PyMuPDF reports black for the glyphs of Type 3 fonts (what Chrome and Skia
# produce when printing to PDF), whatever fill colour the page really sets.
# So white text in such a file looks black and rule a can never fire. We read
# the true fill colour of every glyph from the page's content stream instead.

TOKEN_RE = re.compile(rb"\[(?:\\.|[^\]])*\]|<[0-9A-Fa-f\s]*>|\((?:\\.|[^\\)])*\)|/[^\s/\[\]<>()]*|[^\s/\[\]<>()]+", re.S)
STRING_RE = re.compile(rb"<([0-9A-Fa-f\s]*)>|\(((?:\\.|[^\\)])*)\)", re.S)
TEXT_SHOW_OPS = {b"Tj", b"TJ", b"'", b'"'}


def string_length(token):
    """Number of glyph codes in the string(s) of a Tj/TJ operand."""
    count = 0
    for hexed, literal in STRING_RE.findall(token):
        if hexed or token.lstrip().startswith(b"<"):
            count += (len(re.sub(rb"\s", b"", hexed)) + 1) // 2
        else:
            count += len(re.sub(rb"\\([0-7]{1,3}|.)", b"x", literal))
    return count


def to_rgb(operator, operands):
    """Convert a colour operator's numeric operands to an (r, g, b) tuple, or None."""
    try:
        nums = [float(o) for o in operands]
    except ValueError:
        return None
    if operator == b"g" and len(nums) == 1:
        return (nums[0],) * 3
    if operator == b"rg" and len(nums) == 3:
        return tuple(nums)
    if operator == b"k" and len(nums) == 4:
        c, m, y, k = nums
        return tuple((1 - v) * (1 - k) for v in (c, m, y))
    return None


def content_glyph_colors(data):
    """Fill colour of each glyph code shown in a content stream, in drawing order."""
    colors, stack, fill, operands = [], [], (0.0, 0.0, 0.0), []
    for token in TOKEN_RE.findall(data):
        first = token[:1]
        if first in b"[</(" or re.fullmatch(rb"[-+.\d]+", token):
            operands.append(token)
            continue
        if token == b"q":
            stack.append(fill)
        elif token == b"Q":
            fill = stack.pop() if stack else fill
        elif token in (b"g", b"rg", b"k"):
            fill = to_rgb(token, operands) or fill
        elif token in (b"sc", b"scn") and len(operands) in (1, 3, 4):
            fill = to_rgb({1: b"g", 3: b"rg", 4: b"k"}[len(operands)], operands) or fill
        elif token in TEXT_SHOW_OPS and operands:
            colors.extend([fill] * string_length(operands[-1]))
        operands = []
    return colors


def type3_glyph_colors(page):
    """Map glyph origin -> true fill colour for text PyMuPDF cannot colour.

    Returns {} when the content stream cannot be matched glyph for glyph to
    PyMuPDF's own text trace, in which case the reported colours are kept.
    """
    try:
        data = b"\n".join(page.parent.xref_stream(x) for x in page.get_contents())
        trace = page.get_texttrace()
        colors = content_glyph_colors(data)
        # trace type 3 also means invisible render mode; keep only real Type 3 fonts
        other_fonts = {f[3] for f in page.get_fonts() if f[2] != "Type3"}
        glyphs = [(3 if t["type"] == 3 and t["font"] not in other_fonts else 0, c)
                  for t in trace for c in t["chars"]]
        if not glyphs or len(glyphs) != len(colors):
            return {}
        return {(round(c[2][0], 1), round(c[2][1], 1)): rgb
                for (kind, c), rgb in zip(glyphs, colors) if kind == 3}
    except Exception:
        return {}


def sorted_by_y(type3):
    """Glyph list sorted by y, plus the y keys, so spans can bisect into it."""
    glyphs = sorted((y, x, rgb) for (x, y), rgb in type3.items())
    return glyphs, [g[0] for g in glyphs]


def fix_type3_color(span, type3):
    """Replace the span colour with the true one when all its glyphs agree, and
    ignore the unreliable alpha of Type 3 text."""
    glyphs, ys = type3
    if not glyphs:
        return
    x0, y0, x1, y1 = span["bbox"]
    inside = [(x, rgb) for _, x, rgb in glyphs[bisect_left(ys, y0 - 0.1):bisect_right(ys, y1 + 0.1)]
              if x0 - 0.1 <= x <= x1 + 0.1]
    found = {rgb for _, rgb in inside}
    if inside:
        span["alpha"] = 255  # PyMuPDF reports alpha 0 for every Type 3 glyph; not reliable
    if len(found) == 1:
        r, g, b = (round(v * 255) for v in found.pop())
        span["color"] = (r << 16) | (g << 8) | b


# Rule b

def is_tiny(size):
    return size < MIN_FONT_SIZE


# Rule c

def is_off_page(bbox, page_rect):
    """True when the span box does not touch the visible page at all."""
    return not fitz.Rect(bbox).intersects(page_rect)


# Rule d

def is_transparent(span):
    """True when PyMuPDF reports zero alpha (fill opacity 0 or invisible render mode)."""
    return span.get("alpha", 255) == 0


# Rule e

def is_not_drawn(span, pixmap, background=None):
    """True when the text colour clearly differs from the background but the
    rendered box holds (almost) no pixel near the text colour, e.g. text under
    a white rectangle. A pixel counts as text-coloured when it is closer to the
    text colour than halfway towards the background."""
    if background is None:
        background = sample_background(pixmap, span["bbox"])
    if background is None:
        return False
    text = int_to_rgb(span["color"])
    contrast = color_distance(text, background)
    if contrast < MIN_VISIBLE_CONTRAST:
        return False
    x0, y0, x1, y1 = [int(v * RENDER_ZOOM) for v in span["bbox"]]
    x0, y0 = max(x0, 0), max(y0, 0)
    x1, y1 = min(x1 + 1, pixmap.width), min(y1 + 1, pixmap.height)
    if x1 <= x0 or y1 <= y0:
        return False
    needed = max(1, int(MIN_INK_SHARE * (x1 - x0) * (y1 - y0)))
    limit = (contrast / 2) ** 2
    n, stride, samples = pixmap.n, pixmap.stride, pixmap.samples_mv
    tr, tg, tb = text
    found = 0
    for y in range(y0, y1):
        base = y * stride
        for i in range(base + x0 * n, base + x1 * n, n):
            dr, dg, db = samples[i] - tr, samples[i + 1] - tg, samples[i + 2] - tb
            if dr * dr + dg * dg + db * db < limit:
                found += 1
                if found >= needed:
                    return False
    return True


# Rule a

def matches_background(span, pixmap, background=None):
    """True when the text colour is nearly the same as the surrounding background."""
    if background is None:
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
    return tuple(median(sorted(p[i] for p in pixels)) for i in range(3))


def median(ordered):
    """Median of an already sorted list (same result as statistics.median)."""
    mid = len(ordered) // 2
    return ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2


def read_pixels(pixmap, x0, y0, x1, y1):
    """Read RGB pixels of a rectangle (clipped to the image), subsampled."""
    x0, y0 = max(x0, 0), max(y0, 0)
    x1, y1 = min(x1, pixmap.width), min(y1, pixmap.height)
    if x1 <= x0 or y1 <= y0:
        return []
    step_x = max(1, (x1 - x0) // MAX_SAMPLES_PER_STRIP)
    step_y = max(1, (y1 - y0) // MAX_SAMPLES_PER_STRIP)
    n, stride, samples = pixmap.n, pixmap.stride, pixmap.samples_mv
    return [(samples[i], samples[i + 1], samples[i + 2])
            for i in (y * stride + x * n
                      for y in range(y0, y1, step_y)
                      for x in range(x0, x1, step_x))]


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


def make_finding(page_number, block):
    """Merge the lines of one block into a single finding (lines joined by newlines)."""
    reasons = []
    spans = []
    for line in block:
        for span, span_reasons in line:
            spans.append(span)
            for reason in span_reasons:
                if reason not in reasons:
                    reasons.append(reason)
    union = fitz.Rect(spans[0]["bbox"])
    for s in spans[1:]:
        union |= fitz.Rect(s["bbox"])
    text = "\n".join(join_spans([span for span, _ in line]) for line in block)
    return {
        "page": page_number,
        "text": text,
        "lines": len(block),
        "reasons": reasons,
        "note": NOTE_UNREADABLE if looks_unreadable(text) else None,
        "bbox": [round(v, 1) for v in union],
        "font_size": round(min(s["size"] for s in spans), 1),
        "color": "#%06x" % spans[0]["color"],
    }
