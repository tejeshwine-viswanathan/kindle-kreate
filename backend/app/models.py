from typing import Literal

from pydantic import BaseModel

JobStatus = Literal["queued", "classifying", "processing", "packaging", "done", "failed"]
JobKind = Literal["pdf_to_epub", "epub_to_pdf"]

# (input extension, output extension) per job kind
EXTENSIONS: dict[JobKind, tuple[str, str]] = {
    "pdf_to_epub": ("pdf", "epub"),
    "epub_to_pdf": ("epub", "pdf"),
}


class JobState(BaseModel):
    job_id: str
    kind: JobKind = "pdf_to_epub"
    filename: str
    status: JobStatus = "queued"
    pages_total: int = 0
    pages_done: int = 0
    error: str | None = None
    failed_pages: list[int] = []  # 1-based page numbers
    # OCR pages whose mean word confidence fell below the flag threshold (1-based)
    low_confidence_pages: list[int] = []
    warnings: list[str] = []
    cancelled: bool = False
    created_at: float


class JobCreated(BaseModel):
    job_id: str
    kind: JobKind
    status: JobStatus


class JobView(BaseModel):
    job_id: str
    kind: JobKind
    filename: str
    status: JobStatus
    pages_total: int
    pages_done: int
    error: str | None
    failed_pages: list[int]
    low_confidence_pages: list[int]
    warnings: list[str]
