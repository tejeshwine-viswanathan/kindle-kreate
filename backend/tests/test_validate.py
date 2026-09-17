"""EPUBCheck integration: the warm JVM is reused, and invalid EPUBs are reported."""

from __future__ import annotations

import time
import zipfile

import pymupdf
import pytest

from app.tasks import validate
from app.tasks.epub_builder import build_epub
from app.tasks.extract import extract_page
from app.tasks.structure import build_document
from app.tasks.validate import epubcheck, validator_available

from .corpus import book_content, make_book

pytestmark = pytest.mark.skipif(not validator_available(), reason="Java or EPUBCheck is not installed")


@pytest.fixture
def epub(tmp_path):
    (tmp_path / "img").mkdir()
    with pymupdf.open(make_book(tmp_path / "book.pdf", book_content(seed=12, chapters=1, paras=2))) as doc:
        pages = [extract_page(p, tmp_path / "img") for p in doc]
    build_epub(build_document(pages, fallback_title="book"), tmp_path / "img", tmp_path / "book.epub", author="")
    return tmp_path / "book.epub"


def test_valid_epub_and_warm_jvm_reuse(epub):
    assert epubcheck(epub) == []
    has_server = (validate.SERVER_DIR / "EpubCheckServer.class").exists()
    assert (validate._warm is not None) == has_server
    if has_server:
        pid = validate._warm.proc.pid
        started = time.perf_counter()
        assert epubcheck(epub) == []
        assert validate._warm.proc.pid == pid  # same JVM, no schema re-initialisation
        assert time.perf_counter() - started < 3
        assert not epub.with_name("book.epub.epubcheck.json").exists()


def test_invalid_epub_is_reported(epub, tmp_path):
    broken = tmp_path / "broken.epub"
    with zipfile.ZipFile(epub) as src, zipfile.ZipFile(broken, "w") as dst:
        for item in src.infolist():
            data = src.read(item.filename)
            if item.filename.endswith(".opf"):
                data = data.replace(b"<dc:title>", b"<dc:nonsense>").replace(b"</dc:title>", b"</dc:nonsense>")
            dst.writestr(item, data)
    problems = epubcheck(broken)
    assert problems and all(":" in p for p in problems)
