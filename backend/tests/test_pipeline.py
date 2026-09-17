from __future__ import annotations

import pymupdf

from app.schema import Block, Line
from app.tasks.classify import page_needs_ocr
from app.tasks.extract import extract_page
from app.tasks.layout import order_blocks
from app.tasks.structure import Heading, Image, Paragraph, build_document, join_lines

from .conftest import book_pdf, image_pdf, make_pdf, scanned_pdf, two_column_pdf


def _pages(path, tmp_path):
    image_dir = tmp_path / "images"
    image_dir.mkdir(exist_ok=True)
    with pymupdf.open(path) as doc:
        return [extract_page(page, image_dir) for page in doc]


def test_classify_text_vs_scanned(tmp_path):
    with pymupdf.open(make_pdf(tmp_path / "book.pdf", book_pdf)) as doc:
        assert not any(page_needs_ocr(p) for p in doc)
    with pymupdf.open(make_pdf(tmp_path / "scan.pdf", scanned_pdf)) as doc:
        assert page_needs_ocr(doc[0])


def test_sparse_title_page_is_not_scanned(tmp_path):
    def build(doc):
        doc.new_page().insert_text((200, 300), "Chapter 1", fontsize=24)

    with pymupdf.open(make_pdf(tmp_path / "title.pdf", build)) as doc:
        assert not page_needs_ocr(doc[0])


def test_book_structure(tmp_path):
    pages = _pages(make_pdf(tmp_path / "book.pdf", book_pdf), tmp_path)
    doc = build_document(pages, fallback_title="book")
    kinds = [(type(el).__name__, getattr(el, "level", None)) for el in doc.elements]
    assert kinds == [
        ("Heading", 1), ("Paragraph", None), ("Paragraph", None),
        ("Heading", 1), ("Paragraph", None),
    ]
    first, second, third = (el.text for el in doc.elements if isinstance(el, Paragraph))
    # running header and page numbers are gone
    assert "A Test Book" not in first + second + third
    # wrapped lines joined, soft line-end hyphen removed, paragraph merged across the page break
    assert "striking thirteen." in first
    assert "The explanation was simple" in first
    assert first.endswith("continued onto the next page without any break in the sentence at all.")
    assert second.startswith("A well-known fact")
    # a compound seen elsewhere in the document keeps its hyphen at a line break
    assert "well-known place" in third
    assert doc.title == "book"


def test_two_columns_do_not_interleave(tmp_path):
    pages = _pages(make_pdf(tmp_path / "cols.pdf", two_column_pdf), tmp_path)
    doc = build_document(pages, fallback_title="cols")
    texts = [el.text for el in doc.elements]
    assert texts[0] == "Columns"
    assert texts[1].startswith("Left column line 1") and texts[1].endswith("left end.")
    assert texts[2].startswith("Right column line 1") and texts[2].endswith("right end.")


def test_image_kept_in_place(tmp_path):
    pages = _pages(make_pdf(tmp_path / "img.pdf", image_pdf), tmp_path)
    doc = build_document(pages, fallback_title="img")
    assert [type(el) for el in doc.elements] == [Paragraph, Image, Paragraph]
    assert (tmp_path / "images" / doc.elements[1].name).exists()


def test_xy_cut_full_width_heading_then_columns():
    def block(x0, y0, x1, y1, text):
        return Block(kind="text", bbox=(x0, y0, x1, y1), lines=[Line(text=text, bbox=(x0, y0, x1, y1), font_size=10)])

    blocks = [
        block(300, 100, 500, 140, "R1"),
        block(50, 100, 250, 130, "L1"),
        block(50, 20, 500, 50, "Title"),
        block(50, 140, 250, 200, "L2"),
        block(300, 150, 500, 200, "R2"),
    ]
    assert [b.text for b in order_blocks(blocks)] == ["Title", "L1", "L2", "R1", "R2"]


def test_join_lines():
    assert join_lines("explan-", "ation", set()) == "explanation"
    assert join_lines("well-", "known", {"well-known"}) == "well-known"
    assert join_lines("Hello", "World", set()) == "Hello World"
    assert join_lines("co\u00ad", "operate", set()) == "cooperate"
    assert join_lines("1990-", "1995", set()) == "1990- 1995"


def test_heading_levels_normalized(tmp_path):
    def build(doc):
        page = doc.new_page()
        page.insert_text((72, 100), "Only Section", fontsize=14)
        page.insert_text((72, 140), "Body text that is long enough to be the dominant font size here.", fontsize=11)

    pages = _pages(make_pdf(tmp_path / "h2.pdf", build), tmp_path)
    doc = build_document(pages, fallback_title="h2")
    assert doc.elements[0] == Heading(1, "Only Section")
