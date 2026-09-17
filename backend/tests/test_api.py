from __future__ import annotations

import io
import time
import zipfile

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app, uploads

from .conftest import book_pdf, make_pdf, scanned_pdf


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def _wait(client, job_id, timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = client.get(f"/api/jobs/{job_id}").json()
        if body["status"] in ("done", "failed"):
            return body
        time.sleep(0.1)
    raise AssertionError("job did not finish")


def _upload(client, path, name="book.pdf"):
    with path.open("rb") as f:
        return client.post("/api/jobs", files={"file": (name, f, "application/pdf")})


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_text_pdf_round_trip(client, tmp_path):
    res = _upload(client, make_pdf(tmp_path / "book.pdf", book_pdf))
    assert res.status_code == 202
    job = _wait(client, res.json()["job_id"])
    assert job["status"] == "done", job
    assert job["pages_done"] == job["pages_total"] == 3
    assert job["failed_pages"] == []

    dl = client.get(f"/api/jobs/{job['job_id']}/download")
    assert dl.status_code == 200
    assert dl.headers["content-type"] == "application/epub+zip"
    assert 'filename="book.epub"' in dl.headers["content-disposition"]

    with zipfile.ZipFile(io.BytesIO(dl.content)) as epub:
        names = epub.namelist()
        assert names[0] == "mimetype" and epub.read("mimetype") == b"application/epub+zip"
        chapters = sorted(n for n in names if n.endswith(".xhtml") and "/ch" in n)
        assert len(chapters) == 2
        ch1 = epub.read(chapters[0]).decode()
        assert "<h1>Chapter 1</h1>" in ch1 and "A Test Book" not in ch1
        nav = epub.read(next(n for n in names if n.endswith("nav.xhtml"))).decode()
        assert "Chapter 1" in nav and "Chapter 2" in nav


def test_blank_scan_fails_clearly(client, tmp_path):
    job = _wait(client, _upload(client, make_pdf(tmp_path / "scan.pdf", scanned_pdf)).json()["job_id"])
    assert job["status"] == "failed"
    assert job["error"] == "No readable text or images were found in this PDF."


def test_rejects_non_pdf(client):
    res = client.post("/api/jobs", files={"file": ("notes.pdf", b"hello", "application/pdf")})
    assert res.status_code == 415


def test_unknown_and_malformed_job_ids(client):
    assert client.get("/api/jobs/" + "0" * 32).status_code == 404
    assert client.get("/api/jobs/..%2F..%2Fetc").status_code == 404


def test_delete_job(client, tmp_path, data_dir):
    job_id = _upload(client, make_pdf(tmp_path / "book.pdf", book_pdf)).json()["job_id"]
    _wait(client, job_id)
    assert client.delete(f"/api/jobs/{job_id}").status_code == 204
    assert client.get(f"/api/jobs/{job_id}").status_code == 404
    assert not (data_dir / "jobs" / job_id).exists()


def test_upload_rate_limit(client, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "upload_rate_per_minute", 2)
    uploads.reset()
    pdf = make_pdf(tmp_path / "book.pdf", book_pdf)
    assert _upload(client, pdf).status_code == 202
    assert _upload(client, pdf).status_code == 202
    res = _upload(client, pdf)
    assert res.status_code == 429 and res.headers["retry-after"] == "60"
    uploads.reset()
    assert _upload(client, pdf).status_code == 202


def test_active_job_cap(client, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "max_active_jobs", 1)
    pdf = make_pdf(tmp_path / "book.pdf", book_pdf)
    job_id = _upload(client, pdf).json()["job_id"]
    from app import storage

    storage.update_state(job_id, status="processing")  # hold one job open
    res = _upload(client, pdf)
    assert res.status_code == 503 and "busy" in res.json()["detail"]
    storage.update_state(job_id, status="done")
    assert _upload(client, pdf).status_code == 202


def test_basic_auth(client, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "auth_user", "reader")
    monkeypatch.setattr(settings, "auth_password", "s3cret")
    pdf = make_pdf(tmp_path / "book.pdf", book_pdf)
    res = _upload(client, pdf)
    assert res.status_code == 401
    assert res.headers["www-authenticate"].startswith("Basic ")
    assert client.get("/api/jobs/" + "0" * 32, auth=("reader", "wrong")).status_code == 401
    assert client.get("/health").status_code == 200  # liveness stays open
    with pdf.open("rb") as f:
        res = client.post("/api/jobs", files={"file": ("book.pdf", f, "application/pdf")}, auth=("reader", "s3cret"))
    assert res.status_code == 202
