"""Job files on local disk, and job state in a file (local mode) or Valkey (Celery mode).

Each job owns one directory, shared by the API and workers:

    data/jobs/<job_id>/
        input.pdf | input.epub
        state.json              (local mode only)
        pages/00000.json ...    normalized per-page results (PDF -> EPUB)
        images/                 extracted figures (PDF -> EPUB)
        output.epub | output.pdf
"""

from __future__ import annotations

import json
import os
import shutil
import threading
import time
from functools import cache
from pathlib import Path

from .config import settings
from .models import EXTENSIONS, JobKind, JobState

# --- files ---------------------------------------------------------------------------


def jobs_root() -> Path:
    root = settings.data_dir / "jobs"
    root.mkdir(parents=True, exist_ok=True)
    return root


def job_dir(job_id: str) -> Path:
    return jobs_root() / job_id


def input_path(job_id: str, kind: JobKind) -> Path:
    return job_dir(job_id) / f"input.{EXTENSIONS[kind][0]}"


def output_path(job_id: str, kind: JobKind) -> Path:
    return job_dir(job_id) / f"output.{EXTENSIONS[kind][1]}"


def _subdir(job_id: str, name: str) -> Path:
    path = job_dir(job_id) / name
    path.mkdir(exist_ok=True)  # no parents: never resurrect a deleted job's directory
    return path


def images_dir(job_id: str) -> Path:
    return _subdir(job_id, "images")


def pages_dir(job_id: str) -> Path:
    return _subdir(job_id, "pages")


def page_path(pages: Path, index: int) -> Path:
    return pages / f"{index:05d}.json"


# --- state ---------------------------------------------------------------------------


class FileStateStore:
    """state.json next to the job files. Safe across threads of one process, which is
    how local mode uses it: only the API process reads or writes job state."""

    def __init__(self) -> None:
        self._lock = threading.RLock()

    def _path(self, job_id: str) -> Path:
        return job_dir(job_id) / "state.json"

    def save(self, state: JobState) -> None:
        path = self._path(state.job_id)
        with self._lock:
            if not path.parent.exists():  # job was deleted mid-flight
                return
            tmp = path.with_suffix(".tmp")
            tmp.write_text(state.model_dump_json(), encoding="utf-8")
            os.replace(tmp, path)

    def load(self, job_id: str) -> JobState | None:
        path = self._path(job_id)
        with self._lock:
            if not path.exists():
                return None
            return JobState.model_validate_json(path.read_text(encoding="utf-8"))

    def update(self, job_id: str, **changes) -> JobState | None:
        with self._lock:  # one lock around read-modify-write, so a cancel can't be lost
            state = self.load(job_id)
            if state is None:
                return None
            state = state.model_copy(update=changes)
            self.save(state)
            return state

    def add_pages_done(self, job_id: str, count: int = 1) -> None:
        with self._lock:
            state = self.load(job_id)
            if state is not None:
                self.update(job_id, pages_done=state.pages_done + count)

    def delete(self, job_id: str) -> None:
        pass  # state.json goes with the job directory


class RedisStateStore:
    """One hash per job, one field per state attribute. Fields are written independently,
    so a worker's progress update can't overwrite the API's cancel flag, and the page
    counter uses HINCRBY so parallel page tasks never lose increments."""

    def __init__(self, client) -> None:
        self.redis = client

    @staticmethod
    def _key(job_id: str) -> str:
        return f"pdf2epub:job:{job_id}"

    def save(self, state: JobState) -> None:
        key = self._key(state.job_id)
        fields = {name: json.dumps(value) for name, value in state.model_dump().items()}
        pipe = self.redis.pipeline()
        pipe.hset(key, mapping=fields)
        pipe.expire(key, settings.job_ttl_hours * 3600)
        pipe.execute()

    def load(self, job_id: str) -> JobState | None:
        raw = self.redis.hgetall(self._key(job_id))
        if not raw:
            return None
        return JobState.model_validate({_str(k): json.loads(v) for k, v in raw.items()})

    def update(self, job_id: str, **changes) -> JobState | None:
        current = self.load(job_id)
        if current is None:
            return None
        dumped = current.model_copy(update=changes).model_dump()
        self.redis.hset(self._key(job_id), mapping={name: json.dumps(dumped[name]) for name in changes})
        return self.load(job_id)

    def add_pages_done(self, job_id: str, count: int = 1) -> None:
        key = self._key(job_id)
        if self.redis.exists(key):
            self.redis.hincrby(key, "pages_done", count)

    def delete(self, job_id: str) -> None:
        self.redis.delete(self._key(job_id))


def _str(value: bytes | str) -> str:
    return value.decode() if isinstance(value, bytes) else value


@cache
def _file_store() -> FileStateStore:
    return FileStateStore()


@cache
def _redis_store() -> RedisStateStore:
    import redis

    return RedisStateStore(redis.Redis.from_url(settings.redis_url))


def state_store() -> FileStateStore | RedisStateStore:
    return _redis_store() if settings.queue == "celery" else _file_store()


def save_state(state: JobState) -> None:
    state_store().save(state)


def load_state(job_id: str) -> JobState | None:
    return state_store().load(job_id)


def update_state(job_id: str, **changes) -> JobState | None:
    return state_store().update(job_id, **changes)


def add_pages_done(job_id: str, count: int = 1) -> None:
    state_store().add_pages_done(job_id, count)


def delete_job(job_id: str) -> None:
    state_store().delete(job_id)
    shutil.rmtree(job_dir(job_id), ignore_errors=True)


def active_job_count() -> int:
    """Jobs that are queued or running (across all clients)."""
    count = 0
    for path in jobs_root().iterdir():
        if path.is_dir():
            state = load_state(path.name)
            if state and state.status not in ("done", "failed") and not state.cancelled:
                count += 1
    return count


def purge_expired() -> int:
    cutoff = time.time() - settings.job_ttl_hours * 3600
    removed = 0
    for path in jobs_root().iterdir():
        if not path.is_dir():
            continue
        state = load_state(path.name)
        created = state.created_at if state else path.stat().st_mtime
        if created < cutoff:
            delete_job(path.name)
            removed += 1
    return removed
