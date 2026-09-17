"""Conversion stages, independent of how they are scheduled.

PDF -> EPUB runs in three stages so pages can be processed in parallel:

    plan_pages(job)        which pages need OCR; sets pages_total
    process_page(...)      one page -> pages/NNNNN.json (pure: no job state, any process)
    assemble(job)          all page results -> structure -> EPUB

`app.runner.LocalRunner` drives them with a process pool; `app.celery_app` drives
them as a Celery chord. EPUB -> PDF is a single stage (`run_epub_to_pdf`).
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pymupdf

from .. import storage
from ..config import settings
from ..schema import PageResult
from . import epub_to_pdf
from .classify import classify_document
from .epub_builder import build_epub
from .extract import extract_page
from .ocr import ocr_available, ocr_page
from .structure import build_document
from .validate import validate_epub

log = logging.getLogger(__name__)

OCR_UNAVAILABLE = "scanned page, but Tesseract OCR is not installed"
# Producer-generated titles that shouldn't become the book title.
JUNK_TITLE_RE = re.compile(r"(^untitled|\.(docx?|pdf|indd|tex|odt)$|^microsoft word)", re.IGNORECASE)


class JobCancelled(Exception):
    pass


def check_cancelled(job_id: str) -> None:
    state = storage.load_state(job_id)
    if state is None or state.cancelled:
        raise JobCancelled


@contextmanager
def guarded(job_id: str) -> Iterator[None]:
    """Turn any failure inside a stage into a failed job with a readable message."""
    try:
        yield
    except JobCancelled:
        log.info("job %s cancelled", job_id)
    except Exception as exc:
        log.exception("job %s failed", job_id)
        storage.update_state(job_id, status="failed", error=str(exc) or type(exc).__name__)


# --- PDF -> EPUB ---------------------------------------------------------------------


def plan_pages(job_id: str) -> list[bool]:
    """Returns one flag per page: True if the page needs OCR."""
    if storage.update_state(job_id, status="classifying") is None:
        raise JobCancelled
    needs_ocr = classify_document(str(storage.input_path(job_id, "pdf_to_epub")))
    if all(needs_ocr) and not ocr_available():
        raise ValueError("This PDF is scanned (it has no text layer) and Tesseract OCR is not installed.")
    storage.pages_dir(job_id)
    storage.images_dir(job_id)
    storage.update_state(job_id, status="processing", pages_total=len(needs_ocr), pages_done=0)
    return needs_ocr


def page_args(job_id: str, index: int, needs_ocr: bool) -> tuple[str, int, bool, str, str]:
    """Arguments for process_page as plain strings, so they pickle/serialize anywhere."""
    job = storage.job_dir(job_id)
    return (str(storage.input_path(job_id, "pdf_to_epub")), index, needs_ocr, str(job / "pages"), str(job / "images"))


def process_page(pdf_path: str, index: int, needs_ocr: bool, pages_dir: str, image_dir: str) -> None:
    """Convert one page and write its normalized JSON. Never raises for a bad page:
    the failure is recorded in the result so the rest of the book still converts."""
    try:
        with pymupdf.open(pdf_path) as doc:
            page = doc[index]
            try:
                if not needs_ocr:
                    result = extract_page(page, Path(image_dir))
                elif ocr_available():
                    result = ocr_page(page, Path(image_dir))
                else:
                    result = _failed_page(page, OCR_UNAVAILABLE, "ocr")
            except Exception as exc:
                log.exception("page %d failed", index + 1)
                result = _failed_page(page, type(exc).__name__, "ocr" if needs_ocr else "text")
    except Exception:  # input deleted (job cancelled) or unreadable
        log.exception("could not open page %d", index + 1)
        return
    target = storage.page_path(Path(pages_dir), index)
    try:
        tmp = target.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(result.model_dump_json(), encoding="utf-8")
        os.replace(tmp, target)
    except FileNotFoundError:  # job directory removed by a cancel
        pass


def _failed_page(page: pymupdf.Page, reason: str, source: str) -> PageResult:
    return PageResult(
        index=page.number, width=page.rect.width, height=page.rect.height, source=source, error=reason
    )


def assemble(job_id: str) -> None:
    check_cancelled(job_id)
    state = storage.update_state(job_id, status="packaging")
    if state is None:
        raise JobCancelled
    pages_dir = storage.pages_dir(job_id)
    pdf_path = storage.input_path(job_id, "pdf_to_epub")

    with pymupdf.open(pdf_path) as doc:
        metadata = doc.metadata or {}
        pages = []
        for index in range(state.pages_total):
            path = storage.page_path(pages_dir, index)
            if path.exists():
                pages.append(PageResult.model_validate_json(path.read_text(encoding="utf-8")))
            else:
                pages.append(_failed_page(doc[index], "page was not processed", "text"))

    failed = [p for p in pages if p.error]
    if len(failed) == len(pages):
        raise ValueError("None of the pages in this PDF could be converted.")

    title = metadata.get("title") or ""
    document = build_document(
        pages,
        fallback_title=Path(state.filename).stem,
        metadata_title="" if JUNK_TITLE_RE.search(title) else title,
    )
    if not document.elements:
        raise ValueError("No readable text or images were found in this PDF.")
    check_cancelled(job_id)
    output = storage.output_path(job_id, "pdf_to_epub")
    build_epub(document, storage.images_dir(job_id), output, author=metadata.get("author") or "")
    validate_epub(output)

    low_confidence = [
        p.index + 1 for p in pages if p.confidence is not None and p.confidence < settings.ocr_flag_confidence
    ]
    warnings = []
    missing_ocr = sum(p.error == OCR_UNAVAILABLE for p in failed)
    if missing_ocr:
        warnings.append(f"{missing_ocr} scanned page(s) were skipped because Tesseract OCR is not installed.")
    if len(failed) > missing_ocr:
        warnings.append(f"{len(failed) - missing_ocr} page(s) could not be converted.")
    if low_confidence:
        warnings.append(f"OCR confidence was low on {len(low_confidence)} page(s); their text may contain errors.")
    storage.update_state(
        job_id,
        status="done",
        pages_done=state.pages_total,
        failed_pages=[p.index + 1 for p in failed],
        low_confidence_pages=low_confidence,
        warnings=warnings,
    )


# --- EPUB -> PDF ---------------------------------------------------------------------


def run_epub_to_pdf(job_id: str) -> None:
    storage.update_state(job_id, status="processing")

    def on_page(done: int) -> None:
        check_cancelled(job_id)
        storage.update_state(job_id, pages_done=done)

    epub_to_pdf.convert(
        storage.input_path(job_id, "epub_to_pdf"),
        storage.output_path(job_id, "epub_to_pdf"),
        max_pages=settings.max_pages,
        on_layout=lambda total: storage.update_state(job_id, pages_total=total),
        on_page=on_page,
    )
    storage.update_state(job_id, status="done")
