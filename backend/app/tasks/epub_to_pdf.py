"""EPUB -> PDF: MuPDF lays the book out on fixed-size pages and each page is drawn into a PDF.

Text stays real, selectable text (fonts are embedded), and the EPUB's table of
contents becomes the PDF outline.
"""

from __future__ import annotations

import zipfile
from collections.abc import Callable
from pathlib import Path
from xml.etree import ElementTree

import pymupdf
from pymupdf import mupdf

PAGE_WIDTH, PAGE_HEIGHT = 612, 792  # US Letter, in points
FONT_SIZE = 11

# Font obfuscation is not DRM: readers (and MuPDF) undo it without any key.
FONT_OBFUSCATION = {
    "http://www.idpf.org/2008/embedding",
    "http://ns.adobe.com/pdf/enc#RC",
}


def is_epub(path: Path) -> bool:
    if not zipfile.is_zipfile(path):
        return False
    with zipfile.ZipFile(path) as zf:
        names = set(zf.namelist())
        if "mimetype" in names and zf.read("mimetype").strip() == b"application/epub+zip":
            return True
        return "META-INF/container.xml" in names


def has_drm(path: Path) -> bool:
    with zipfile.ZipFile(path) as zf:
        names = set(zf.namelist())
        if "META-INF/rights.xml" in names:  # Adobe ADEPT
            return True
        if "META-INF/encryption.xml" not in names:
            return False
        root = ElementTree.fromstring(zf.read("META-INF/encryption.xml"))
    algorithms = {el.get("Algorithm") for el in root.iter() if el.tag.endswith("EncryptionMethod")}
    return bool(algorithms - FONT_OBFUSCATION)


def convert(
    epub_path: Path,
    pdf_path: Path,
    max_pages: int,
    on_layout: Callable[[int], None],
    on_page: Callable[[int], None],
) -> None:
    """on_layout(total_pages) runs once pages are known; on_page(done) after each page.
    Either callback may raise to abort (e.g. on cancellation)."""
    try:
        src = pymupdf.open(epub_path, filetype="epub")
        src.layout(width=PAGE_WIDTH, height=PAGE_HEIGHT, fontsize=FONT_SIZE)
    except (pymupdf.FileDataError, RuntimeError) as exc:
        raise ValueError("The EPUB is corrupt or unreadable.") from exc

    with src:
        if src.page_count == 0:
            raise ValueError("The EPUB has no readable content.")
        if src.page_count > max_pages:
            raise ValueError(f"The book is longer than {max_pages} pages at this page size.")
        on_layout(src.page_count)

        writer = pymupdf.DocumentWriter(str(pdf_path), "compress,compress-fonts,garbage=3")
        try:
            for page in src:
                device = writer.begin_page(page.rect)
                # Page.run() is broken for DocumentWriter devices in PyMuPDF 1.28, so call MuPDF directly.
                mupdf.fz_run_page(page.this, device.this, mupdf.FzMatrix(), mupdf.FzCookie())
                writer.end_page()
                on_page(page.number + 1)
        finally:
            writer.close()

        toc = src.get_toc()
        metadata = src.metadata or {}

    with pymupdf.open(pdf_path) as out:
        out.set_toc(toc)
        out.set_metadata({"title": metadata.get("title") or "", "author": metadata.get("author") or ""})
        out.saveIncr()
