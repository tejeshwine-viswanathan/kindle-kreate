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


def _page(index, blocks, height=519.0):
    from app.schema import PageResult

    return PageResult(index=index, width=400, height=height, source="ocr", blocks=blocks)


def _text_block(lines):
    return Block(
        kind="text",
        bbox=(min(l.bbox[0] for l in lines), min(l.bbox[1] for l in lines), max(l.bbox[2] for l in lines), max(l.bbox[3] for l in lines)),
        lines=lines,
    )


def _line(text, y, size=11.0, x=40.0):
    return Line(text=text, bbox=(x, y, x + 300, y + size), font_size=size)


def test_ocr_running_heads_are_stripped():
    """Headers whose page numbers OCR garbles ("XX1"), headers glued onto the body block,
    page numbers just outside the old 10% zone, and OCR-noisy header text."""
    from app.tasks.structure import strip_running_heads

    body = lambda i: [_line(f"Body line {i} one.", 60), _line("Body line two.", 75)]
    pages = [
        _page(0, [_text_block([_line("INTRODUCTION xix", 20, 14.2)]), _text_block(body(0))]),
        _page(1, [_text_block([_line("XX INTRODUCTION", 20), *body(1)])]),  # glued
        _page(2, [_text_block([_line("INTRODUCTION XX1", 18, 7.9)]), _text_block(body(2))]),
        _page(3, [_text_block([_line("XXU INTRODUCTI0N", 20), *body(3)])]),  # OCR noise
        _page(4, [_text_block(body(4)), _text_block([_line("xliti", 500)])]),  # garbled roman page number
        _page(5, [_text_block(body(5)), _text_block([_line("26", 466)])]),  # 89.8% down the page
    ]
    strip_running_heads(pages)
    texts = [l.text for p in pages for b in p.blocks for l in b.lines]
    assert all("INTRODUCT" not in t for t in texts), texts
    assert "xliti" not in texts and "26" not in texts
    assert texts.count("Body line two.") == 6
    assert pages[1].blocks[0].bbox[1] == 60  # bbox recomputed after the header line was dropped


def test_toc_entries_are_not_headings():
    from app.tasks.structure import heading_level

    toc = Block(kind="text", bbox=(0, 0, 300, 12), lines=[Line(text="1 Introduction to Deep Learning 4", bbox=(0, 0, 300, 12), font_size=10, bold=True)])
    assert heading_level(toc, 10.0) is None
    short_toc = Block(kind="text", bbox=(0, 0, 300, 14), lines=[Line(text="8 Acknowledgments 35", bbox=(0, 0, 300, 14), font_size=12)])
    assert heading_level(short_toc, 10.0) is None
    leaders = Block(kind="text", bbox=(0, 0, 300, 14), lines=[Line(text="Abstract ........ 1", bbox=(0, 0, 300, 14), font_size=12)])
    assert heading_level(leaders, 10.0) is None
    real = Block(kind="text", bbox=(0, 0, 300, 12), lines=[Line(text="Related Work", bbox=(0, 0, 300, 12), font_size=10, bold=True)])
    assert heading_level(real, 10.0) == 3
    numbered = Block(kind="text", bbox=(0, 0, 300, 14), lines=[Line(text="3.2 Attention", bbox=(0, 0, 300, 14), font_size=12)])
    assert heading_level(numbered, 10.0) == 2
