from dataclasses import dataclass
import html
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


def render_page(data: bytes, index: int, dpi: int = 110) -> bytes:
    """Return a single 1-based page as PNG bytes."""
    with _lock, _open(data) as doc:
        if index < 1 or index > doc.page_count:
            raise PdfError(f"Page {index} is invalid for a {doc.page_count}-page PDF")
        return doc[index - 1].get_pixmap(dpi=dpi).tobytes("png")


@dataclass(frozen=True)
class QuestionComment:
    question_number: str
    score: float | None
    max_points: float
    feedback: str


def _comments_html(comments: list[QuestionComment]) -> str:
    cards = []
    for c in comments:
        score = "–" if c.score is None else f"{c.score:g}"
        header = f"Q{html.escape(c.question_number)} — {score} / {c.max_points:g} pts"
        body = (
            html.escape(c.feedback).replace("\n", "<br>")
            if c.feedback.strip()
            else '<span style="color:#64748b;">(No comment)</span>'
        )
        cards.append(
            '<div style="margin-bottom:8px;padding:6px 8px;background:#ffffff;'
            'border:1px solid #cbd5e1;border-left:3px solid #64748b;">'
            f'<div style="font-weight:bold;color:#0f172a;margin-bottom:3px;">{header}</div>'
            f'<div style="color:#334155;">{body}</div>'
            "</div>"
        )
    return '<div style="font-family:sans-serif;font-size:9.5px;line-height:1.35;">' + "".join(cards) + "</div>"


def _fit_margin_height(html_text: str, box_w: float, min_h: float) -> float:
    """Return a box height >= min_h that fits html_text without shrinking or clipping."""
    with fitz.open() as probe:
        h = min_h
        for _ in range(6):
            p = probe.new_page(width=box_w + 20, height=h + 20)
            spare, _ = p.insert_htmlbox(fitz.Rect(0, 0, box_w, h), html_text, scale_low=1.0)
            if spare >= 0:
                return h
            h *= 1.5
        return h


def annotate_pdf(data: bytes, page_comments: dict[int, list[QuestionComment]], margin_w: float = 240.0) -> bytes:
    """Extend pages to the right and render question comments in the added margin.

    Nothing on the original answer sheet is obscured or rescaled.
    """
    if not any(page_comments.values()):
        return data

    pad = 12.0
    box_w = margin_w - 2 * pad
    with _lock, _open(data) as src, fitz.open() as dst:
        for idx, src_page in enumerate(src, start=1):
            orig_w = src_page.rect.width
            orig_h = src_page.rect.height
            comments = page_comments.get(idx, [])
            if comments:
                markup = _comments_html(comments)
                needed_h = _fit_margin_height(markup, box_w, max(1.0, orig_h - 2 * pad)) + 2 * pad
                dst_h = max(orig_h, needed_h)
            else:
                markup = ""
                dst_h = orig_h

            dst_page = dst.new_page(width=orig_w + margin_w, height=dst_h)
            dst_page.show_pdf_page(fitz.Rect(0, 0, orig_w, orig_h), src, idx - 1)
            dst_page.draw_rect(
                fitz.Rect(orig_w, 0, orig_w + margin_w, dst_h),
                color=None,
                fill=(0.973, 0.980, 0.988),
            )
            dst_page.draw_line(
                fitz.Point(orig_w, 0),
                fitz.Point(orig_w, dst_h),
                color=(0.796, 0.835, 0.882),
                width=0.75,
            )
            if markup:
                dst_page.insert_htmlbox(
                    fitz.Rect(orig_w + pad, pad, orig_w + margin_w - pad, dst_h - pad),
                    markup,
                    scale_low=1.0,
                )
        return dst.tobytes(garbage=3, deflate=True)


@dataclass(frozen=True)
class SectionScore:
    name: str
    score: float
    max_points: float
    # (question number, score or None if ungraded, max points)
    questions: tuple[tuple[str, float | None, float], ...] = ()


def _cover_html(exam_name: str, student: str, sections: list[SectionScore], note: str, width: float) -> str:
    total = sum(s.score for s in sections)
    max_total = sum(s.max_points for s in sections)
    rows = []
    for s in sections:
        qs = " &nbsp;·&nbsp; ".join(
            f"<b>{html.escape(n)}</b> {'–' if sc is None else f'{sc:g}'}/{mx:g}" for n, sc, mx in s.questions
        )
        rows.append(
            '<div style="padding:9px 4px;border-bottom:1px solid #cbd5e1;">'
            f'<span style="font-weight:bold;font-size:13px;">{html.escape(s.name)}</span>'
            f'<span style="font-size:13px;">&nbsp;&nbsp;&nbsp;&nbsp;<b style="font-size:16px;">{s.score:g}</b>'
            f'<span style="color:#475569;"> / {s.max_points:g}</span></span>'
            f'<div style="font-size:9px;color:#475569;margin-top:3px;">{qs}</div></div>'
        )
    pct = f"{100 * total / max_total:.1f}%" if max_total else ""
    note_html = f'<p style="margin-top:16px;font-size:9px;color:#475569;">{html.escape(note)}</p>' if note else ""
    return (
        '<div style="font-family:sans-serif;color:#0f172a;">'
        '<div style="font-size:9px;letter-spacing:1px;color:#64748b;">MARKS SUMMARY</div>'
        f'<div style="font-size:24px;font-weight:bold;margin-top:4px;">{html.escape(exam_name)}</div>'
        f'<div style="font-size:13px;color:#334155;margin-top:4px;">{html.escape(student)}</div>'
        '<div style="margin-top:22px;padding:14px 16px;border:1px solid #94a3b8;background:#f1f5f9;">'
        '<div style="font-size:9px;letter-spacing:1px;color:#64748b;">TOTAL MARKS OBTAINED</div>'
        f'<div style="font-size:34px;font-weight:bold;">{total:g} <span style="font-size:18px;color:#475569;">/ {max_total:g}</span>'
        f' <span style="font-size:12px;color:#475569;">&nbsp;{pct}</span></div></div>'
        '<div style="margin-top:22px;font-size:9px;letter-spacing:1px;color:#64748b;">BREAKDOWN BY SECTION</div>'
        '<div style="margin-top:4px;border-top:2px solid #334155;">'
        + "".join(rows)
        + "</div>"
        + note_html
        + "</div>"
    )


def add_cover_page(
    data: bytes, exam_name: str, student: str, sections: list[SectionScore], note: str = "", margin: float = 48.0
) -> bytes:
    """Insert a marks-summary page in front, the same size as the document's first page."""
    with _lock, _open(data) as doc:
        r = doc[0].rect
        markup = _cover_html(exam_name, student, sections, note, r.width - 2 * margin)
        page = doc.new_page(pno=0, width=r.width, height=r.height)
        page.insert_htmlbox(
            fitz.Rect(margin, margin, r.width - margin, r.height - margin), markup, scale_low=0.4
        )
        return doc.tobytes(garbage=3, deflate=True)
