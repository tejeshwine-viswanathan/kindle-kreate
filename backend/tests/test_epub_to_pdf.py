from __future__ import annotations

import zipfile
from pathlib import Path

import pymupdf
from ebooklib import epub

from app.tasks.epub_to_pdf import has_drm, is_epub

from .test_api import _wait, client  # noqa: F401  (client is a fixture)

PARAGRAPH = "The quick brown fox jumps over the lazy dog while the river keeps running. " * 12


def make_epub(path: Path, chapters: int = 3) -> Path:
    book = epub.EpubBook()
    book.set_identifier("test-book")
    book.set_title("Sample Book")
    book.set_language("en")
    book.add_author("Test Author")
    items = []
    for n in range(1, chapters + 1):
        chapter = epub.EpubHtml(title=f"Chapter {n}", file_name=f"ch{n}.xhtml", lang="en")
        chapter.content = f"<h1>Chapter {n}</h1>" + f"<p>{PARAGRAPH}</p>" * 8
        book.add_item(chapter)
        items.append(chapter)
    book.toc = items
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.spine = ["nav", *items]
    epub.write_epub(str(path), book)
    return path


def add_encryption(path: Path, algorithm: str) -> Path:
    xml = f"""<?xml version="1.0"?>
<encryption xmlns="urn:oasis:names:tc:opendocument:xmlns:container"
            xmlns:enc="http://www.w3.org/2001/04/xmlenc#">
  <enc:EncryptedData>
    <enc:EncryptionMethod Algorithm="{algorithm}"/>
    <enc:CipherData><enc:CipherReference URI="EPUB/ch1.xhtml"/></enc:CipherData>
  </enc:EncryptedData>
</encryption>"""
    with zipfile.ZipFile(path, "a") as zf:
        zf.writestr("META-INF/encryption.xml", xml)
    return path


def test_detection(tmp_path):
    book = make_epub(tmp_path / "book.epub")
    assert is_epub(book) and not has_drm(book)
    assert has_drm(add_encryption(make_epub(tmp_path / "drm.epub"), "http://www.w3.org/2001/04/xmlenc#aes128-cbc"))
    assert not has_drm(add_encryption(make_epub(tmp_path / "fonts.epub"), "http://www.idpf.org/2008/embedding"))
    plain_zip = tmp_path / "files.zip"
    with zipfile.ZipFile(plain_zip, "w") as zf:
        zf.writestr("readme.txt", "hi")
    assert not is_epub(plain_zip)


def test_epub_round_trip(client, tmp_path):  # noqa: F811
    with make_epub(tmp_path / "Sample Book.epub").open("rb") as f:
        res = client.post("/api/jobs", files={"file": ("Sample Book.epub", f, "application/epub+zip")})
    assert res.status_code == 202
    assert res.json()["kind"] == "epub_to_pdf"

    job = _wait(client, res.json()["job_id"])
    assert job["status"] == "done", job
    assert job["pages_total"] > 3 and job["pages_done"] == job["pages_total"]

    dl = client.get(f"/api/jobs/{job['job_id']}/download")
    assert dl.headers["content-type"] == "application/pdf"
    assert "Sample%20Book.pdf" in dl.headers["content-disposition"]

    with pymupdf.open(stream=dl.content, filetype="pdf") as pdf:
        assert pdf.page_count == job["pages_total"]
        text = " ".join(page.get_text() for page in pdf)
        assert "Chapter 3" in text and "quick brown fox" in text  # real, searchable text
        assert [title for _, title, _ in pdf.get_toc()] == ["Chapter 1", "Chapter 2", "Chapter 3"]
        assert pdf.metadata["title"] == "Sample Book"
        assert pdf.metadata["author"] == "Test Author"


def test_rejects_drm_epub(client, tmp_path):  # noqa: F811
    path = add_encryption(make_epub(tmp_path / "drm.epub"), "http://www.w3.org/2001/04/xmlenc#aes128-cbc")
    with path.open("rb") as f:
        res = client.post("/api/jobs", files={"file": ("drm.epub", f, "application/epub+zip")})
    assert res.status_code == 422
    assert "DRM" in res.json()["detail"]


def test_corrupt_epub_fails_cleanly(client, tmp_path):  # noqa: F811
    path = tmp_path / "broken.epub"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("mimetype", "application/epub+zip")
        zf.writestr("META-INF/container.xml", "<not valid")
    with path.open("rb") as f:
        res = client.post("/api/jobs", files={"file": ("broken.epub", f, "application/epub+zip")})
    if res.status_code == 202:
        job = _wait(client, res.json()["job_id"])
        assert job["status"] == "failed" and job["error"] == "The EPUB is corrupt or unreadable."
    else:
        assert res.status_code == 422
