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


def test_scan_background_behind_text_layer_is_dropped(tmp_path):
    """Internet Archive / Acrobat-OCR PDFs: a full-page scan image with the OCR text on top."""
    from .conftest import PAGE_H, PAGE_W

    def build(doc):
        page = doc.new_page(width=PAGE_W, height=PAGE_H)
        pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 1200, 1550), False)
        pix.set_rect(pix.irect, (235, 235, 225))
        page.insert_image(page.rect, pixmap=pix)
        for i in range(12):
            page.insert_text((72, 120 + i * 15), f"Line {i} of the OCR text layer sits on top of the scan.", fontsize=11)

    pages = _pages(make_pdf(tmp_path / "ia.pdf", build), tmp_path)
    assert [b.kind for b in pages[0].blocks] == ["text"]
    assert not any((tmp_path / "images").iterdir())


def test_full_page_figure_without_text_is_kept(tmp_path):
    def build(doc):
        page = doc.new_page()
        pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 400, 500), False)
        pix.set_rect(pix.irect, (30, 120, 200))
        page.insert_image(pymupdf.Rect(40, 40, 550, 700), pixmap=pix)
        page.insert_text((72, 740), "Figure 1: a plate that fills the page.", fontsize=10)

    pages = _pages(make_pdf(tmp_path / "plate.pdf", build), tmp_path)
    assert [b.kind for b in pages[0].blocks] == ["image", "text"]


def test_oversized_images_are_downscaled(tmp_path):
    from app.tasks.extract import MAX_IMAGE_PX

    def build(doc):
        page = doc.new_page()
        pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 4000, 3000), False)
        pix.set_rect(pix.irect, (200, 30, 30))
        page.insert_image(pymupdf.Rect(72, 72, 540, 423), stream=pix.tobytes("jpeg"))
        page.insert_text((72, 500), "Caption under a very large photo.", fontsize=10)

    pages = _pages(make_pdf(tmp_path / "big.pdf", build), tmp_path)
    image = next(b for b in pages[0].blocks if b.kind == "image")
    stored = pymupdf.Pixmap(str(tmp_path / "images" / image.image))
    assert stored.width <= MAX_IMAGE_PX and image.image.endswith(".jpg")


def test_oversized_first_word_beside_body_is_not_a_heading():
    from app.tasks.structure import demote_inline_headings, heading_level

    def blk(x0, y0, x1, y1, text, size):
        return Block(kind="text", bbox=(x0, y0, x1, y1), lines=[Line(text=text, bbox=(x0, y0, x1, y1), font_size=size)])

    body = 9.5
    first_word = blk(17, 93, 59, 109, "He was", 11.6)
    rest = blk(66, 98, 322, 112, "quite young, wonderfully handsome, extremely", 9.7)
    column_heading = blk(17, 200, 150, 216, "3 Model Architecture", 12.0)
    other_column = blk(180, 202, 322, 214, "body text in the right column", body)  # 30pt gutter
    blocks = [first_word, rest, column_heading, other_column]
    levels = {id(b): heading_level(b, body) for b in blocks}
    assert levels[id(first_word)] == 2 and levels[id(column_heading)] == 2
    demote_inline_headings(levels, blocks)
    assert levels[id(first_word)] is None
    assert levels[id(column_heading)] == 2  # a heading beside another column stays a heading


def test_noisy_pages_need_real_titles_for_headings():
    from app.tasks.structure import heading_level

    def blk(text, size):
        return Block(kind="text", bbox=(0, 0, 200, size), lines=[Line(text=text, bbox=(0, 0, 200, size), font_size=size)])

    assert heading_level(blk("He", 12.9), 9.5) == 2  # a clean text layer is trusted
    assert heading_level(blk("He", 12.9), 9.5, noisy=True) is None
    assert heading_level(blk("Dy LZ", 25.6), 9.5, noisy=True) is None
    assert heading_level(blk("CHAPTER", 15.6), 9.5, noisy=True) == 1
    assert heading_level(blk("Chapter 3", 12.0), 9.5, noisy=True) == 1
    assert heading_level(blk("The Red-Headed League", 15.0), 9.5, noisy=True) == 2


def test_text_layer_over_scan_is_marked_ocr(tmp_path):
    from .conftest import PAGE_H, PAGE_W

    def build(doc):
        page = doc.new_page(width=PAGE_W, height=PAGE_H)
        pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 1200, 1550), False)
        pix.set_rect(pix.irect, (235, 235, 225))
        page.insert_image(page.rect, pixmap=pix)
        for i in range(12):
            page.insert_text((72, 120 + i * 15), f"Line {i} of the OCR text layer sits on top of the scan.", fontsize=11)

    assert _pages(make_pdf(tmp_path / "ia.pdf", build), tmp_path)[0].source == "ocr"


def test_ocr_headings_must_read_like_words():
    from app.tasks.structure import _reads_like_words, heading_level

    for junk in ["M&", "CrRCULATION", "ITU ^->", "H H", "iy Hy Name of School...~nW0 -LTee at KA", "4H"]:
        assert not _reads_like_words(junk), junk
    for real in ["ADVENTURES OF SHERLOCK HOLMES", "CHAPTER XLIX.", "The Red-Headed League", "A. CONAN DOYLE", "Mr. Bennet's Reply", "Part II", "McDonald's Farm"]:
        assert _reads_like_words(real), real
    big = Block(kind="text", bbox=(0, 0, 200, 20), lines=[Line(text="ITU ^->", bbox=(0, 0, 200, 20), font_size=20)])
    assert heading_level(big, 9.5) == 1 and heading_level(big, 9.5, noisy=True) is None


def test_author_list_on_title_page_is_not_headings():
    from app.tasks.structure import demote_heading_runs

    def blk(text, y):
        return Block(kind="text", bbox=(0, y, 200, y + 12), lines=[Line(text=text, bbox=(0, y, 200, y + 12), font_size=12)])

    title = blk("Attention Is All You Need", 0)
    authors = [blk("Ashish Vaswani", 30), blk("Noam Shazeer", 60), blk("Niki Parmar", 90), blk("Jakob Uszkoreit", 120)]
    def affiliation(org, mail, y):  # two lines, as PyMuPDF reports them
        return Block(kind="text", bbox=(0, y, 200, y + 24), lines=[Line(text=org, bbox=(0, y, 200, y + 12), font_size=10), Line(text=mail, bbox=(0, y + 12, 200, y + 24), font_size=10)])

    affiliations = [affiliation("Google Brain", "avaswani@google.com", 45), affiliation("Google Brain", "noam@google.com", 75), affiliation("Google Research", "nikip@google.com", 105)]
    abstract = blk("Abstract", 150)
    body = blk("The dominant sequence transduction models are based on complex recurrent networks.", 165)
    intro = blk("1 Introduction", 200)
    blocks = [title, authors[0], affiliations[0], authors[1], affiliations[1], authors[2], affiliations[2], authors[3], abstract, body, intro]
    levels = {id(b): None for b in blocks}
    levels.update({id(title): 1, **{id(a): 2 for a in authors}, id(abstract): 2, id(intro): 2})
    demote_heading_runs(levels, blocks)
    assert levels[id(title)] == 1 and all(levels[id(a)] is None for a in authors)
    assert levels[id(abstract)] == 2 and levels[id(intro)] == 2


def test_short_all_caps_ocr_fragments_are_not_headings():
    from app.tasks.structure import _plausible_ocr_title

    for junk in ["OT", "NE", "A", "of whom he had", "made up my mind"]:
        assert not _plausible_ocr_title(junk), junk
    for real in ["CHAPTER XL", "XLIX.", "L", "CONTENTS", "The two young ladies", "Chapter 3"]:
        assert _plausible_ocr_title(real), real
