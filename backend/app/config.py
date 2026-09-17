import os
import shutil
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration. Every field can be overridden with a PDF2EPUB_* env var."""

    model_config = SettingsConfigDict(env_prefix="PDF2EPUB_")

    data_dir: Path = Path(__file__).resolve().parent.parent / "data"
    max_upload_mb: int = 500
    max_pages: int = 2000
    job_ttl_hours: int = 24
    # "local": jobs run inside the API process (threads for jobs, a process pool for pages).
    # "celery": jobs fan out to Celery workers; state lives in Valkey/Redis.
    queue: Literal["local", "celery"] = "local"
    redis_url: str = "redis://localhost:6379/0"
    # Concurrent jobs in local mode; pages within them share the page pool below.
    job_workers: int = 2
    cors_origins: list[str] = ["http://localhost:5173"]

    # OCR (scanned pages)
    tesseract_cmd: str | None = None  # auto-detected when unset
    ocr_lang: str = "eng"  # Tesseract language codes, e.g. "eng+deu"
    ocr_dpi: int = 300
    # Pages whose mean word confidence falls below this are flagged for review.
    ocr_flag_confidence: float = 80.0
    # Parallel page processes in local mode (OCR is CPU-bound, so ~one per core).
    # 0 processes pages inline in the job thread, which is handy for tests and debugging.
    page_workers: int = max(1, (os.cpu_count() or 2) - 1)

    # EPUB validation with W3C EPUBCheck: "auto" validates when Java + EPUBCheck are
    # installed, "required" fails jobs when they aren't, "off" skips validation.
    epubcheck: Literal["auto", "required", "off"] = "auto"
    epubcheck_jar: Path | None = None  # default: backend/tools/epubcheck/epubcheck.jar
    java_cmd: str | None = None

    def resolved_tesseract(self) -> str | None:
        """Explicit setting, then PATH, then the default Windows install location."""
        if self.tesseract_cmd:
            return self.tesseract_cmd
        found = shutil.which("tesseract")
        default_windows = Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe")
        return found or (str(default_windows) if default_windows.exists() else None)


settings = Settings()
