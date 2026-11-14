"""Side-by-side page images for the "Show on page" view.

For every page with findings we render two PNGs: the page as a person sees it,
and the same page with each hidden finding drawn in red inside a red outline.
The uploaded file is never changed; drawing happens on an in-memory copy.
"""
import base64

import fitz

ZOOM = 1.5
RED = (0.85, 0.1, 0.1)
MIN_DRAWN_SIZE = 6.0  # points; hidden text is drawn at least this big so it can be read


def png_data_url(pixmap):
    return "data:image/png;base64," + base64.b64encode(pixmap.tobytes("png")).decode("ascii")


def draw_finding(page, finding):
    """Draw one finding in red with a red outline, clipped to the visible page."""
    rect = fitz.Rect(finding["bbox"])
    visible = rect & page.rect
    if visible.is_empty:
        return False
    page.draw_rect(rect, color=RED, fill=RED, fill_opacity=0.12, width=1, overlay=True)
    size = max(finding["font_size"], MIN_DRAWN_SIZE)
    # The outline keeps the true position; the text gets room to be readable.
    lines = finding["text"].count("\n") + 1
    box = fitz.Rect(rect.x0, rect.y0 - 1, max(rect.x1, page.rect.x1 - 20),
                    rect.y0 + size * 1.5 * lines + 4)
    while size >= MIN_DRAWN_SIZE and page.insert_textbox(
            box, finding["text"], fontsize=size, fontname="helv", color=RED, overlay=True) < 0:
        size -= 1
    return True


def render_pages(data, findings):
    """Return one dict per page that has findings: page number, size and both images."""
    doc = fitz.open(stream=data, filetype="pdf")
    matrix = fitz.Matrix(ZOOM, ZOOM)
    pages = []
    try:
        for number in sorted({f["page"] for f in findings}):
            page = doc[number - 1]
            normal = page.get_pixmap(matrix=matrix, alpha=False)
            on_page = [f for f in findings if f["page"] == number]
            outside = sum(1 for f in on_page if not draw_finding(page, f))
            marked = page.get_pixmap(matrix=matrix, alpha=False)
            pages.append({
                "page": number,
                "width": page.rect.width,
                "height": page.rect.height,
                "outside": outside,
                "normal": png_data_url(normal),
                "marked": png_data_url(marked),
            })
    finally:
        doc.close()
    return pages
