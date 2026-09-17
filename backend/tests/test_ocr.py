from __future__ import annotations

import zipfile

import cv2
import pymupdf
import pytest

from app.config import settings
from app.tasks.ocr import estimate_skew, ocr_available, ocr_page, render, rotate

from .corpus import book_content, make_book, scan, word_accuracy
from .test_api import _upload, _wait, client  # noqa: F401  (client is a fixture)

pytestmark = pytest.mark.skipif(not ocr_available(), reason="Tesseract is not installed")

QUOTES = str.maketrans({"“": '"', "”": '"', "‘": "'", "’": "'"})


def _epub_text(content: bytes) -> tuple[str, list[str]]:
    """(all chapter text, h1/h2 headings) of an EPUB."""
    import io
    import re
    from html import unescape

    with zipfile.ZipFile(io.BytesIO(content)) as z:
        html = "".join(z.read(n).decode() for n in sorted(z.namelist()) if re.search(r"/ch\d+(-\d+)?\.xhtml$", n))
    headings = [unescape(h) for h in re.findall(r"<h[12][^>]*>(.*?)</h[12]>", html)]
    paragraphs = [unescape(p) for p in re.findall(r"<p>(.*?)</p>", html)]
    return " ".join(paragraphs), headings


def test_skew_is_detected(tmp_path):
    book = make_book(tmp_path / "book.pdf", book_content(seed=3, chapters=1, paras=6))
    with pymupdf.open(book) as doc:
        gray = cv2.cvtColor(render(doc[0], 150), cv2.COLOR_RGB2GRAY)
    for applied in (-2.5, 1.2):
        assert estimate_skew(rotate(gray, applied)) == pytest.approx(-applied, abs=0.15)
    assert estimate_skew(gray) == 0.0


def test_upside_down_scan_is_read(tmp_path):
    content = book_content(seed=4, chapters=1, paras=3)
    src = make_book(tmp_path / "book.pdf", content)
    scanned = scan(src, tmp_path / "scan.pdf", rotate90=180)
    with pymupdf.open(scanned) as doc:
        page = ocr_page(doc[0], tmp_path)
    words = " ".join(b.text for b in page.blocks if b.kind == "text").translate(QUOTES).split()
    assert word_accuracy(content.words[:40], words[2:42]) > 0.95  # skip the running header


def test_scanned_two_column_book_end_to_end(client, tmp_path):  # noqa: F811
    content = book_content(seed=5, chapters=2, paras=6, figures=True)
    src = make_book(tmp_path / "book.pdf", content, columns=2)
    scanned = scan(src, tmp_path / "Scanned Book.pdf", skew_deg=0.8, noise=6, blur=True, jpeg_quality=80)

    job = _wait(client, _upload(client, scanned, "Scanned Book.pdf").json()["job_id"], timeout=120)
    assert job["status"] == "done", job
    assert job["failed_pages"] == [] and job["low_confidence_pages"] == []

    epub = client.get(f"/api/jobs/{job['job_id']}/download").content
    text, headings = _epub_text(epub)
    assert headings == [t for _, t in content.headings]
    expected = " ".join(content.paragraphs).split()
    assert word_accuracy(expected, text.translate(QUOTES).split()) >= 0.99
    with zipfile.ZipFile(__import__("io").BytesIO(epub)) as z:
        assert sum(n.endswith(".jpg") for n in z.namelist()) == 2  # both figures kept


def test_mixed_text_and_scanned_pages(client, tmp_path):  # noqa: F811
    content = book_content(seed=6, chapters=2, paras=4)
    src = make_book(tmp_path / "book.pdf", content)
    scanned = scan(src, tmp_path / "scan.pdf")
    mixed = pymupdf.open()
    with pymupdf.open(src) as text_doc, pymupdf.open(scanned) as scan_doc:
        mixed.insert_pdf(text_doc, from_page=0, to_page=0)
        mixed.insert_pdf(scan_doc, from_page=1, to_page=1)
    mixed.save(tmp_path / "mixed.pdf")

    job = _wait(client, _upload(client, tmp_path / "mixed.pdf").json()["job_id"], timeout=60)
    assert job["status"] == "done", job
    _, headings = _epub_text(client.get(f"/api/jobs/{job['job_id']}/download").content)
    assert headings == [t for _, t in content.headings]


def test_low_confidence_pages_are_flagged(client, tmp_path, monkeypatch):  # noqa: F811
    monkeypatch.setattr(settings, "ocr_flag_confidence", 99.9)  # nothing is that confident
    src = make_book(tmp_path / "book.pdf", book_content(seed=7, chapters=1, paras=3))
    job = _wait(client, _upload(client, scan(src, tmp_path / "scan.pdf")).json()["job_id"], timeout=60)
    assert job["status"] == "done"
    assert job["low_confidence_pages"] == [1]
    assert any("confidence" in w for w in job["warnings"])
