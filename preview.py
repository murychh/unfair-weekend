"""Page images for the "Show on page" view.

For every page with findings we render, as PNGs:
  - the page as a person sees it, and the same page with a red outline and a
    light red fill around each hidden finding (no text is drawn here);
  - for each finding, a close-up crop of both versions.
The hidden text itself is shown by the browser as an HTML overlay, so any
script is displayed with the browser's own font fallback.
The uploaded file is never changed; drawing happens on an in-memory copy.
"""
import base64

import fitz

ZOOM = 1.5
CROP_ZOOM = 4.0       # close-ups are rendered sharper than the page image
CROP_PAD = 8.0        # points of context around a finding in a close-up
CROP_MIN_WIDTH = 110.0
CROP_MIN_HEIGHT = 40.0
RED = (0.85, 0.1, 0.1)


def png_data_url(pixmap):
    return "data:image/png;base64," + base64.b64encode(pixmap.tobytes("png")).decode("ascii")


def crop_rect(bbox, page_rect):
    """Close-up area around a finding: padded, at least a minimum size, inside the page."""
    rect = fitz.Rect(bbox)
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


def render_pages(data, findings):
    """Return one dict per page that has findings: sizes, both images and close-ups."""
    doc = fitz.open(stream=data, filetype="pdf")
    matrix = fitz.Matrix(ZOOM, ZOOM)
    crop_matrix = fitz.Matrix(CROP_ZOOM, CROP_ZOOM)
    pages = []
    try:
        for number in sorted({f["page"] for f in findings}):
            page = doc[number - 1]
            items = [(i, f) for i, f in enumerate(findings) if f["page"] == number]
            drawable = [(i, f) for i, f in items if is_on_page(f, page)]
            areas = {i: crop_rect(f["bbox"], page.rect) for i, f in drawable}
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
                        "marked": png_data_url(page.get_pixmap(matrix=crop_matrix, clip=a, alpha=False)),
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
    return pages
