"""Schedules conversion jobs: in-process (local mode) or on Celery workers."""

from __future__ import annotations

import os
from concurrent.futures import FIRST_COMPLETED, Future, ProcessPoolExecutor, ThreadPoolExecutor, wait
from typing import Protocol

from . import storage
from .config import settings
from .tasks import pipeline


class Runner(Protocol):
    def submit(self, job_id: str) -> None: ...
    def shutdown(self) -> None: ...


def _init_page_worker() -> None:
    # Pages already run one per core; Tesseract's own OpenMP threads would oversubscribe.
    os.environ["OMP_THREAD_LIMIT"] = "1"


class LocalRunner:
    """Jobs on a thread pool; their pages on a shared process pool (OCR is CPU-bound).
    Only this process touches job state, so the file state store is safe."""

    def __init__(self) -> None:
        self.jobs = ThreadPoolExecutor(max_workers=settings.job_workers, thread_name_prefix="job")
        self.pages = (
            ProcessPoolExecutor(max_workers=settings.page_workers, initializer=_init_page_worker)
            if settings.page_workers > 0
            else None
        )

    def submit(self, job_id: str) -> None:
        self.jobs.submit(self._run, job_id)

    def _run(self, job_id: str) -> None:
        with pipeline.guarded(job_id):
            state = storage.load_state(job_id)
            if state is None:
                return
            if state.kind == "epub_to_pdf":
                pipeline.run_epub_to_pdf(job_id)
                return
            needs_ocr = pipeline.plan_pages(job_id)
            args = [pipeline.page_args(job_id, i, flag) for i, flag in enumerate(needs_ocr)]
            if self.pages is None:
                for page in args:
                    pipeline.check_cancelled(job_id)
                    pipeline.process_page(*page)
                    storage.add_pages_done(job_id)
            else:
                self._run_parallel(job_id, args)
            pipeline.assemble(job_id)

    def _run_parallel(self, job_id: str, args: list[tuple]) -> None:
        pending: set[Future] = {self.pages.submit(pipeline.process_page, *page) for page in args}
        try:
            while pending:
                done, pending = wait(pending, timeout=1.0, return_when=FIRST_COMPLETED)
                for future in done:
                    future.result()  # re-raise worker crashes (process_page itself never raises)
                if done:
                    storage.add_pages_done(job_id, len(done))
                pipeline.check_cancelled(job_id)
        finally:
            for future in pending:
                future.cancel()

    def shutdown(self) -> None:
        self.jobs.shutdown(wait=False, cancel_futures=True)
        if self.pages is not None:
            self.pages.shutdown(wait=False, cancel_futures=True)


class CeleryRunner:
    def submit(self, job_id: str) -> None:
        from .celery_app import start_job

        start_job.delay(job_id)

    def shutdown(self) -> None:
        pass


def create_runner() -> Runner:
    return CeleryRunner() if settings.queue == "celery" else LocalRunner()
