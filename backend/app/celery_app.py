"""Celery workers: one task per page, fanned out as a chord that assembles the EPUB.

Run a worker with:
    celery -A app.celery_app worker --loglevel=info                  (Linux / Docker)
    celery -A app.celery_app worker --loglevel=info --pool=threads   (Windows: prefork is unsupported)
"""

from __future__ import annotations

import os

from celery import Celery, chord

from . import storage
from .config import settings
from .tasks import pipeline

# Pages already run one per worker process; keep Tesseract single-threaded inside each.
os.environ.setdefault("OMP_THREAD_LIMIT", "1")

app = Celery("pdf2epub", broker=settings.redis_url, backend=settings.redis_url)
app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    # OCR tasks take seconds: hand each worker one at a time, and re-queue a task if its
    # worker dies mid-page instead of losing it (which would stall the chord).
    worker_prefetch_multiplier=1,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    result_expires=3600,
    broker_connection_retry_on_startup=True,
)


@app.task(name="pdf2epub.start_job")
def start_job(job_id: str) -> None:
    with pipeline.guarded(job_id):
        state = storage.load_state(job_id)
        if state is None:
            return
        if state.kind == "epub_to_pdf":
            pipeline.run_epub_to_pdf(job_id)
            return
        needs_ocr = pipeline.plan_pages(job_id)
        pages = [process_page.si(job_id, index, flag) for index, flag in enumerate(needs_ocr)]
        chord(pages)(assemble.si(job_id))


@app.task(name="pdf2epub.process_page")
def process_page(job_id: str, index: int, needs_ocr: bool) -> None:
    state = storage.load_state(job_id)
    if state is None or state.cancelled:
        return
    pipeline.process_page(*pipeline.page_args(job_id, index, needs_ocr))
    storage.add_pages_done(job_id)


@app.task(name="pdf2epub.assemble")
def assemble(job_id: str) -> None:
    with pipeline.guarded(job_id):
        pipeline.assemble(job_id)
