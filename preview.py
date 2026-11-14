"""Page images for the "Show on page" view.

For every page with findings we render, as PNGs:
  - the page as a person sees it, and the same page with a red outline and a
    light red fill around each hidden finding (no text is drawn here);
  - for each finding, a close-up crop of both versions.
The hidden text itself is shown by the browser as an HTML overlay, so any
script is displayed with the browser's own font fallback.
The uploaded file is never changed; drawing happens on an in-memory copy.

The work is limited so a file with very many findings stays fast and the response small:
at most MAX_PAGES pages are rendered and at most MAX_CROPS findings get a close-up.
render_pages reports when a limit was hit.
"""
import base64

import fitz

MAX_PAGES = 10        # pages with findings that are rendered per request
MAX_CROPS = 50        # findings that get a close-up per request
ZOOM = 1.5
CROP_ZOOM = 4.0       # close-ups are rendered sharper than the page image
CROP_PAD = 8.0        # points of context around a finding in a close-up
CROP_MIN_WIDTH = 110.0
CROP_MIN_HEIGHT = 40.0
RED = (0.85, 0.1, 0.1)


def png_data_url(pixmap):
    return "data:image/png;base64," + base64.b64encode(pixmap.tobytes("png")).decode("ascii")


def text_column(page):
    """Left and right edge of all text on the page (the page margins), or the page edges."""
    xs = [(b[0], b[2]) for b in page.get_text("blocks") if b[4].strip()]
    if not xs:
        return page.rect.x0, page.rect.x1
    return min(x[0] for x in xs), max(x[1] for x in xs)


def crop_rect(bbox, page_rect, column=None):
    """Close-up area around a finding: full text column wide, padded vertically, inside the page."""
    rect = fitz.Rect(bbox)
    if column:
        rect.x0 = min(rect.x0, column[0])
        rect.x1 = max(rect.x1, column[1])
    rect = fitz.Rect(rect.x0 - CROP_PAD, rect.y0 - CROP_PAD, rect.x1 + CROP_PAD, rect.y1 + CROP_PAD)
    grow_x = max(0.0, CROP_MIN_WIDTH - rect.width)
    grow_y = max(0.0, CROP_MIN_HEIGHT - rect.height)
    rect = fitz.Rect(rect.x0, rect.y0 - grow_y / 2, rect.x1 + grow_x, rect.y1 + grow_y / 2)
    # Move (not shrink) the area back inside the page when it sticks out.
    dx = max(page_rect.x0 - rect.x0, 0) + min(page_rect.x1 - rect.x1, 0)
    dy = max(page_rect.y0 - rect.y0, 0) + min(page_rect.y1 - rect.y1, 0)
    return (rect + (dx, dy, dx, dy)) & page_rect


def is_on_page(finding, page):
    return not (fitz.Rect(finding["bbox"]) & page.rect).is_empty


def draw_highlight(page, finding):
    """One red outline with a light fill around the whole finding block."""
    page.draw_rect(fitz.Rect(finding["bbox"]), color=RED, fill=RED, fill_opacity=0.15,
                   width=1, overlay=True)


def marked_crop(data, number, finding, area, matrix):
    """Close-up with only this finding highlighted (other findings in the area stay unmarked)."""
    doc = fitz.open(stream=data, filetype="pdf")
    try:
        page = doc[number - 1]
        draw_highlight(page, finding)
        return page.get_pixmap(matrix=matrix, clip=area, alpha=False)
    finally:
        doc.close()


def render_pages(data, findings):
    """Return (pages, truncated).

    pages has one dict per rendered page that has findings: sizes, both images and close-ups.
    truncated is True when MAX_PAGES or MAX_CROPS cut the result short. Findings past MAX_CROPS
    keep their entry with "crop": None; findings on pages past MAX_PAGES are left out.
    """
    doc = fitz.open(stream=data, filetype="pdf")
    matrix = fitz.Matrix(ZOOM, ZOOM)
    crop_matrix = fitz.Matrix(CROP_ZOOM, CROP_ZOOM)
    pages = []
    crops_left = MAX_CROPS
    numbers = sorted({f["page"] for f in findings})
    truncated = len(numbers) > MAX_PAGES
    try:
        for number in numbers[:MAX_PAGES]:
            page = doc[number - 1]
            items = [(i, f) for i, f in enumerate(findings) if f["page"] == number]
            drawable = [(i, f) for i, f in items if is_on_page(f, page)]
            column = text_column(page)
            cropped = drawable[:crops_left]
            crops_left -= len(cropped)
            if len(cropped) < len(drawable):
                truncated = True
            areas = {i: crop_rect(f["bbox"], page.rect, column) for i, f in cropped}
            normal = page.get_pixmap(matrix=matrix, alpha=False)
            crops_normal = {i: page.get_pixmap(matrix=crop_matrix, clip=a, alpha=False)
                            for i, a in areas.items()}
            for _, f in drawable:
                draw_highlight(page, f)
            marked = page.get_pixmap(matrix=matrix, alpha=False)
            entries = []
            for i, f in items:
                entry = {"index": i, "crop": None}
                if i in areas:
                    a = areas[i]
                    entry["crop"] = {
                        "box": [a.x0, a.y0, a.x1, a.y1],
                        "normal": png_data_url(crops_normal[i]),
                        "marked": png_data_url(marked_crop(data, number, f, a, crop_matrix)),
                    }
                entries.append(entry)
            pages.append({
                "page": number,
                "width": page.rect.width,
                "height": page.rect.height,
                "outside": len(items) - len(drawable),
                "normal": png_data_url(normal),
                "marked": png_data_url(marked),
                "findings": entries,
            })
    finally:
        doc.close()
    return pages, truncated
