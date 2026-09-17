"""Celery mode: Valkey-backed job state and the page-task chord (run eagerly, with fakeredis)."""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor

import fakeredis
import pytest

from app import storage
from app.config import settings
from app.models import JobState

from .corpus import book_content, make_book


@pytest.fixture
def redis_store(monkeypatch):
    store = storage.RedisStateStore(fakeredis.FakeRedis())
    monkeypatch.setattr(settings, "queue", "celery")
    monkeypatch.setattr(storage, "state_store", lambda: store)
    return store


def _new_job(job_id: str = "a" * 32, **fields) -> JobState:
    state = JobState(job_id=job_id, filename="book.pdf", created_at=time.time(), **fields)
    storage.save_state(state)
    return state


def test_parallel_progress_updates_are_not_lost(redis_store):
    _new_job()
    with ThreadPoolExecutor(8) as pool:
        list(pool.map(lambda _: storage.add_pages_done("a" * 32), range(200)))
    assert storage.load_state("a" * 32).pages_done == 200


def test_field_updates_do_not_clobber_each_other(redis_store):
    _new_job()
    stale = storage.load_state("a" * 32)  # a worker's view, loaded before the cancel
    storage.update_state("a" * 32, cancelled=True)
    redis_store.redis.hset(redis_store._key("a" * 32), "status", '"processing"')  # worker writes one field
    assert stale.cancelled is False
    state = storage.load_state("a" * 32)
    assert state.cancelled is True and state.status == "processing"


def test_state_expires_with_job_ttl(redis_store):
    _new_job()
    ttl = redis_store.redis.ttl(redis_store._key("a" * 32))
    assert 0 < ttl <= settings.job_ttl_hours * 3600


def test_celery_chord_converts_a_pdf(redis_store, tmp_path, data_dir):
    from app.celery_app import app as celery

    celery.conf.task_always_eager = True
    try:
        job_id = "b" * 32
        storage.job_dir(job_id).mkdir(parents=True)
        content = book_content(seed=8, chapters=2, paras=3)
        make_book(storage.input_path(job_id, "pdf_to_epub"), content)
        _new_job(job_id)

        from app.celery_app import start_job

        start_job.delay(job_id)

        state = storage.load_state(job_id)
        assert state.status == "done", state
        assert state.pages_done == state.pages_total == 2
        assert storage.output_path(job_id, "pdf_to_epub").exists()
    finally:
        celery.conf.task_always_eager = False


def test_cancelled_job_skips_page_tasks(redis_store, tmp_path, data_dir):
    from app.celery_app import process_page

    job_id = "c" * 32
    storage.job_dir(job_id).mkdir(parents=True)
    make_book(storage.input_path(job_id, "pdf_to_epub"), book_content(seed=9, chapters=1, paras=2))
    _new_job(job_id, pages_total=1, cancelled=True)
    process_page.run(job_id, 0, False)
    assert not (storage.job_dir(job_id) / "pages").exists()
    assert storage.load_state(job_id).pages_done == 0
