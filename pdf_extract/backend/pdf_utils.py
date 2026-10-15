import re

import pymupdf

MIN_TEXT_CHARS = 40


def open_pdf(path: str) -> pymupdf.Document:
    return pymupdf.open(path)


def inspect(path: str) -> dict:
    with open_pdf(path) as doc:
        text_pages = [len(p.get_text().strip()) >= MIN_TEXT_CHARS for p in doc]
        return {"page_count": doc.page_count, "has_text": all(text_pages) and bool(text_pages)}


def page_has_text(page: pymupdf.Page) -> bool:
    return len(page.get_text().strip()) >= MIN_TEXT_CHARS


def layout_text(page: pymupdf.Page, cols: int = 150) -> str:
    """Rebuild the page text so words keep their horizontal position (like pdftotext -layout).

    Plain text extraction often scrambles table columns; keeping positions lets a
    text-only model read tables correctly.
    """
    words = page.get_text("words")
    if not words:
        return ""
    width = page.rect.width or 612
    scale = cols / width
    words = sorted(words, key=lambda w: ((w[1] + w[3]) / 2, w[0]))

    rows: list[list] = []
    for w in words:
        yc = (w[1] + w[3]) / 2
        h = max(w[3] - w[1], 1)
        if rows:
            last = rows[-1]
            last_yc = sum((x[1] + x[3]) / 2 for x in last) / len(last)
            if abs(yc - last_yc) < h * 0.5:
                last.append(w)
                continue
        rows.append([w])

    lines = []
    prev_bottom = None
    for row in rows:
        row.sort(key=lambda w: w[0])
        top = min(w[1] for w in row)
        if prev_bottom is not None and top - prev_bottom > 14:
            lines.append("")
        prev_bottom = max(w[3] for w in row)
        line = ""
        for w in row:
            pos = int(w[0] * scale)
            if line:
                pos = max(pos, len(line) + 1)
            line = line.ljust(pos) + w[4]
        lines.append(line.rstrip())
    return "\n".join(lines)


# Claude downsizes any image whose long side exceeds this, so rendering larger only costs time and upload size.
MAX_SIDE = 1568


def render_page(page: pymupdf.Page, dpi: int = 150, max_side: int = MAX_SIDE, gray: bool = False) -> bytes:
    zoom = dpi / 72
    long_side = max(page.rect.width, page.rect.height) * zoom
    if long_side > max_side:
        zoom *= max_side / long_side
    pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False,
                          colorspace=pymupdf.csGRAY if gray else pymupdf.csRGB)
    return pix.tobytes("jpeg", jpg_quality=85)


def page_images(path: str, pages: list[int], dpi: int = 150, max_side: int = MAX_SIDE,
                gray: bool = False) -> list[tuple[int, bytes]]:
    """pages are 1-based."""
    out = []
    with open_pdf(path) as doc:
        for n in pages:
            out.append((n, render_page(doc[n - 1], dpi, max_side, gray)))
    return out


def page_snippets(path: str, pages: list[int], limit: int = 600) -> list[tuple[int, str]]:
    """First `limit` characters of each page's layout text (labels stay above/next to their values), with runs of
    spaces shortened to save tokens; "" for pages without a text layer."""
    out = []
    with open_pdf(path) as doc:
        for n in pages:
            p = doc[n - 1]
            if not page_has_text(p):
                out.append((n, ""))
                continue
            lines = (re.sub(r" {2,}", "   ", ln.strip()) for ln in layout_text(p).splitlines())
            out.append((n, "\n".join(ln for ln in lines if ln)[:limit]))
    return out


def page_texts(path: str, pages: list[int]) -> list[tuple[int, str]]:
    out = []
    with open_pdf(path) as doc:
        for n in pages:
            p = doc[n - 1]
            out.append((n, layout_text(p) if page_has_text(p) else ""))
    return out


def full_plain_text(path: str) -> str:
    with open_pdf(path) as doc:
        return "\n".join(p.get_text() for p in doc)
