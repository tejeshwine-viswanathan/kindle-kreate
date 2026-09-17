from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest

from app.config import settings

PAGE_W, PAGE_H = 612, 792
BODY = 11
LEADING = 15
LEFT, RIGHT = 72, 540


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    return tmp_path / "data"


class PageWriter:
    """Places text line by line so tests control wrapping, indents and fonts exactly."""

    def __init__(self, doc: pymupdf.Document, page_no: int, header: str = "A Test Book"):
        self.page = doc.new_page(width=PAGE_W, height=PAGE_H)
        self.y = 110.0
        if header:
            self.page.insert_text((LEFT, 45), header, fontsize=9)
            self.page.insert_text((PAGE_W / 2, PAGE_H - 40), str(page_no), fontsize=9)

    def heading(self, text: str, size: float = 24) -> None:
        self.y += size
        self.page.insert_text((LEFT, self.y), text, fontsize=size, fontname="hebo")
        self.y += size

    def lines(self, lines: list[str], x: float = LEFT, indent_first: bool = True) -> None:
        for i, line in enumerate(lines):
            self.page.insert_text((x + (18 if i == 0 and indent_first else 0), self.y), line, fontsize=BODY)
            self.y += LEADING
        self.y += LEADING  # paragraph gap


def make_pdf(path: Path, build) -> Path:
    doc = pymupdf.open()
    build(doc)
    doc.set_metadata({"title": "", "author": "Test Author"})
    doc.save(path)
    doc.close()
    return path


def book_pdf(doc: pymupdf.Document) -> None:
    p1 = PageWriter(doc, 1)
    p1.heading("Chapter 1")
    p1.lines([
        "It was a bright cold day in April, and the clocks were striking",
        "thirteen. The hallway smelt of boiled cabbage and old rag mats. At",
        "one end of it a coloured poster, too large for indoor display, had",
        "been tacked to the wall. It depicted simply an enormous face, more",
        "than a metre wide: the face of a man of about forty-five, with a heavy",
        "black moustache and ruggedly handsome features. The explan-",
        "ation was simple and the story continued onto the next",
    ])
    p2 = PageWriter(doc, 2)
    p2.lines([
        "page without any break in the sentence at all.",
    ], indent_first=False)
    p2.lines([
        "A well-known fact is that the second paragraph starts with an indent",
        "and ends with a full stop.",
    ])
    p3 = PageWriter(doc, 3)
    p3.heading("Chapter 2")
    p3.lines([
        "The second chapter begins here and it is also a well-",
        "known place to end a line with a real compound hyphen.",
    ])


def two_column_pdf(doc: pymupdf.Document) -> None:
    page = PageWriter(doc, 1, header="")
    page.heading("Columns", size=20)
    top = page.y
    page.lines([f"Left column line {i}" for i in range(1, 6)] + ["left end."], x=LEFT, indent_first=False)
    page.y = top
    page.lines([f"Right column line {i}" for i in range(1, 6)] + ["right end."], x=330, indent_first=False)


def image_pdf(doc: pymupdf.Document) -> None:
    page = PageWriter(doc, 1, header="")
    page.lines(["Text before the figure."], indent_first=False)
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 200, 120), False)
    pix.set_rect(pix.irect, (30, 120, 200))
    page.page.insert_image(pymupdf.Rect(LEFT, page.y, LEFT + 200, page.y + 120), pixmap=pix)
    page.y += 150
    page.lines(["Text after the figure."], indent_first=False)


def scanned_pdf(doc: pymupdf.Document) -> None:
    page = doc.new_page(width=PAGE_W, height=PAGE_H)
    pix = pymupdf.Pixmap(pymupdf.csGRAY, pymupdf.IRect(0, 0, 850, 1100), False)
    pix.clear_with(240)
    page.insert_image(page.rect, pixmap=pix)
