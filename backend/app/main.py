from __future__ import annotations

import asyncio
import base64
import logging
import re
import secrets
import time
import uuid
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path
from xml.etree import ElementTree

import pymupdf
from fastapi import FastAPI, HTTPException, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from . import storage
from .config import settings
from .limits import RateLimiter
from .models import EXTENSIONS, JobCreated, JobKind, JobState, JobView
from .tasks import epub_to_pdf
from .runner import create_runner

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

JOB_ID_RE = re.compile(r"^[0-9a-f]{32}$")
CHUNK = 1 << 20
MEDIA_TYPES = {"pdf": "application/pdf", "epub": "application/epub+zip"}


async def _purge_loop() -> None:
    while True:
        removed = await asyncio.to_thread(storage.purge_expired)
        if removed:
            log.info("purged %d expired job(s)", removed)
        await asyncio.sleep(3600)


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.runner = create_runner()
    purge = asyncio.create_task(_purge_loop())
    yield
    purge.cancel()
    app.state.runner.shutdown()


app = FastAPI(title="Kindle Kreate", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware, allow_origins=settings.cors_origins, allow_methods=["*"], allow_headers=["*"]
)
uploads = RateLimiter()


def _authorized(header: str) -> bool:
    scheme, _, credentials = header.partition(" ")
    if scheme.lower() != "basic":
        return False
    try:
        user, _, password = base64.b64decode(credentials.strip()).decode("utf-8").partition(":")
    except (ValueError, UnicodeDecodeError):
        return False
    expected = f"{settings.auth_user}:{settings.auth_password}"
    return secrets.compare_digest(f"{user}:{password}".encode(), expected.encode())


@app.middleware("http")
async def basic_auth(request: Request, call_next):
    """HTTP Basic auth on the API when PDF2EPUB_AUTH_USER/PASSWORD are set. Browsers
    handle the challenge natively, so fetch, XHR and the download link all just work."""
    protected = settings.auth_user and settings.auth_password and request.url.path.startswith("/api/")
    if protected and request.method != "OPTIONS" and not _authorized(request.headers.get("authorization", "")):
        return Response(
            '{"detail":"Sign in to use the converter."}',
            status_code=401,
            media_type="application/json",
            headers={"WWW-Authenticate": 'Basic realm="Kindle Kreate", charset="UTF-8"'},
        )
    return await call_next(request)


def _client_ip(request: Request) -> str:
    if settings.trust_proxy and (forwarded := request.headers.get("x-forwarded-for")):
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _get_state(job_id: str) -> JobState:
    state = storage.load_state(job_id) if JOB_ID_RE.match(job_id) else None
    if state is None:
        raise HTTPException(404, "Job not found.")
    return state


def _detect_kind(path: Path) -> JobKind:
    """The upload's content, not its name, decides the conversion direction."""
    with path.open("rb") as f:
        if b"%PDF-" in f.read(1024):
            _validate_pdf(path)
            return "pdf_to_epub"
    try:
        if epub_to_pdf.is_epub(path):
            if epub_to_pdf.has_drm(path):
                raise HTTPException(422, "DRM-protected EPUBs can't be converted.")
            return "epub_to_pdf"
    except (zipfile.BadZipFile, ElementTree.ParseError):
        raise HTTPException(422, "The EPUB is corrupt or unreadable.")
    raise HTTPException(415, "File is not a PDF or EPUB.")


def _validate_pdf(path: Path) -> None:
    try:
        with pymupdf.open(path, filetype="pdf") as doc:
            if doc.needs_pass:
                raise HTTPException(422, "Password-protected PDFs are not supported.")
            if doc.page_count == 0:
                raise HTTPException(422, "The PDF has no pages.")
            if doc.page_count > settings.max_pages:
                raise HTTPException(422, f"The PDF has more than {settings.max_pages} pages.")
    except (pymupdf.FileDataError, RuntimeError):
        raise HTTPException(422, "The PDF is corrupt or unreadable.")


@app.post("/api/jobs", response_model=JobCreated, status_code=202)
async def create_job(request: Request, file: UploadFile) -> JobCreated:
    if not uploads.allow(_client_ip(request), settings.upload_rate_per_minute):
        raise HTTPException(429, "Too many uploads from your address; try again in a minute.", {"Retry-After": "60"})
    if settings.max_active_jobs and await asyncio.to_thread(storage.active_job_count) >= settings.max_active_jobs:
        raise HTTPException(503, "The server is busy converting other files; try again shortly.", {"Retry-After": "30"})
    job_id = uuid.uuid4().hex
    storage.job_dir(job_id).mkdir(parents=True)
    dest = storage.job_dir(job_id) / "upload"
    limit = settings.max_upload_mb * 1024 * 1024
    try:
        size = 0
        with dest.open("wb") as out:
            while chunk := await file.read(CHUNK):
                size += len(chunk)
                if size > limit:
                    raise HTTPException(413, f"File exceeds the {settings.max_upload_mb} MB limit.")
                out.write(chunk)
        kind = await asyncio.to_thread(_detect_kind, dest)
        dest.rename(storage.input_path(job_id, kind))
    except BaseException:
        storage.delete_job(job_id)
        raise

    filename = file.filename or f"document.{EXTENSIONS[kind][0]}"
    storage.save_state(JobState(job_id=job_id, kind=kind, filename=filename, created_at=time.time()))
    app.state.runner.submit(job_id)
    return JobCreated(job_id=job_id, kind=kind, status="queued")


@app.get("/api/jobs/{job_id}", response_model=JobView)
def get_job(job_id: str) -> JobState:
    return _get_state(job_id)


@app.get("/api/jobs/{job_id}/download")
def download(job_id: str) -> FileResponse:
    state = _get_state(job_id)
    if state.status != "done":
        raise HTTPException(409, "The file is not ready yet.")
    ext = EXTENSIONS[state.kind][1]
    return FileResponse(
        storage.output_path(job_id, state.kind),
        media_type=MEDIA_TYPES[ext],
        filename=f"{Path(state.filename).stem}.{ext}",
    )


@app.delete("/api/jobs/{job_id}", status_code=204)
def delete_job(job_id: str) -> Response:
    _get_state(job_id)
    storage.update_state(job_id, cancelled=True)  # a running job stops at its next page
    storage.delete_job(job_id)
    return Response(status_code=204)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
