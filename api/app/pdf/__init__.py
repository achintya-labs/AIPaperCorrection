import threading

import fitz  # PyMuPDF


class PdfError(ValueError):
    pass


# PyMuPDF is not thread-safe, and both the worker threads and the API threadpool use it.
# Operations here are seconds at most (model calls take minutes), so one global lock is cheap.
_lock = threading.Lock()


def _open(data: bytes) -> fitz.Document:
    try:
        doc = fitz.open(stream=data, filetype="pdf")
    except Exception as e:
        raise PdfError(f"Not a readable PDF: {e}") from e
    if doc.needs_pass:
        doc.close()
        raise PdfError("PDF is password protected")
    if doc.page_count == 0:
        doc.close()
        raise PdfError("PDF has no pages")
    return doc


def page_count(data: bytes) -> int:
    with _lock, _open(data) as doc:
        return doc.page_count


def extract_ranges(data: bytes, ranges: list[tuple[int, int]]) -> list[bytes]:
    """Cut a combined scan into one PDF per (first_page, last_page) range, 1-based and inclusive.

    Ranges may leave gaps (cover sheets, blank pages) but must not overlap.
    """
    with _lock, _open(data) as doc:
        last_end = 0
        for first, last in sorted(ranges):
            if first < 1 or last < first or last > doc.page_count:
                raise PdfError(f"Page range {first}-{last} is invalid for a {doc.page_count}-page PDF")
            if first <= last_end:
                raise PdfError(f"Page range {first}-{last} overlaps another range")
            last_end = last
        parts = []
        for first, last in ranges:
            part = fitz.open()
            part.insert_pdf(doc, from_page=first - 1, to_page=last - 1)
            parts.append(part.tobytes(garbage=3, deflate=True))
            part.close()
        return parts


def render_pages(data: bytes, dpi: int = 150) -> list[bytes]:
    """Return each page as PNG bytes (used by the worker before calling the grader).

    A list rather than a generator so the lock is never held across a caller's yield point.
    """
    with _lock, _open(data) as doc:
        return [page.get_pixmap(dpi=dpi).tobytes("png") for page in doc]
