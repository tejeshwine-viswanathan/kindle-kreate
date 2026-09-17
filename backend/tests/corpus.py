"""Synthetic document corpus with known ground truth.

`make_book` lays out prose with word wrapping across 1..N columns and pages
(running headers, page numbers, chapter/section headings, optional figures and
tables). `scan` turns any PDF into an image-only "scanned" PDF with realistic
degradation (skew, blur, noise, JPEG), so OCR accuracy can be measured
against the exact source text.
"""

from __future__ import annotations

import io
import random
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np
import pymupdf

PROSE = [
    "The harbour town of Wexmoor had never been large, but in the spring of 1887 it held nearly 4,200 people, most of whom depended in one way or another on the herring fleet.",
    "Margaret Hale kept the ledger for her father's chandlery. Every morning she recorded the price of rope, tar, and lamp oil, and every evening she compared those figures with what the captains had actually paid.",
    "\"You count too carefully,\" her brother told her once. \"Nobody else in this town bothers.\" She answered that nobody else in the town was owed quite so much money.",
    "By the end of March the accounts showed a shortfall of £312, a sum large enough to close the business within a year. The cause was not difficult to find: three vessels had sailed on credit and never settled.",
    "Chapter records from the harbour office suggest that the winter storms were unusually severe that year. At least eleven boats were damaged, and two were lost with all hands off the northern headland.",
    "Margaret wrote to the owners of the missing ships, politely at first and then with increasing firmness. Only one replied, promising payment \"as soon as circumstances permit,\" which in practice meant never.",
    "Her father, a well-meaning but disorganised man, preferred not to discuss the matter. He spent long afternoons at the Customs House, where the talk was of tariffs, railways, and the price of coal in Newcastle.",
    "In April a surveyor arrived from London with instructions to assess the harbour for a proposed breakwater. He was a quiet, methodical person who measured everything twice and wrote his notes in a tiny, precise hand.",
    "The survey took six weeks. When it was finished, the report recommended a stone wall 640 yards long, at an estimated cost of £18,500, to be funded partly by the Board of Trade and partly by local subscription.",
    "Opinion in the town was divided. Some believed the breakwater would bring larger ships and new trade; others feared the construction would disrupt the fishing season and ruin the smaller merchants entirely.",
    "Margaret studied the report closely. On page 23 she found a table of expected tonnage that seemed, to her eye, wildly optimistic, and she said so in a letter to the local newspaper, signed only with her initials.",
    "The letter caused a small sensation. For several days nobody could discover who \"M. H.\" might be, and the editor, who knew perfectly well, enjoyed the mystery far too much to reveal it.",
    "Eventually the surveyor himself called at the chandlery. He had read the letter, he said, and wished to thank its author, because she was right: the tonnage figures had been copied incorrectly from an older survey.",
    "What followed was not a romance, whatever the town later claimed, but a partnership of two careful people who distrusted easy answers. Together they rebuilt the estimates line by line.",
    "The revised report, submitted in September, reduced the projected cost to £14,900 and cut the expected tonnage by almost a third. It was less exciting than the original, but it was accurate, and it was approved.",
    "Construction began the following year. Margaret, by then managing the chandlery on her own, supplied most of the rope and timber, and for the first time in a decade the ledger balanced at the end of every month.",
]

FIRST_WORDS = ["Afterwards", "Meanwhile", "Nevertheless", "Later", "Still", "Even so", "In time"]


def prose(rng: random.Random, n: int) -> list[str]:
    out = []
    for i in range(n):
        text = rng.choice(PROSE)
        if i and rng.random() < 0.3:
            text = f"{rng.choice(FIRST_WORDS)}, {text[0].lower()}{text[1:]}"
        out.append(text)
    return out


@dataclass
class Book:
    items: list[tuple[str, str]] = field(default_factory=list)  # (kind, text): h1 | h2 | p | fig | table

    @property
    def paragraphs(self) -> list[str]:
        return [t for k, t in self.items if k == "p"]

    @property
    def headings(self) -> list[tuple[int, str]]:
        return [(1 if k == "h1" else 2, t) for k, t in self.items if k in ("h1", "h2")]

    @property
    def words(self) -> list[str]:
        return " ".join(t for k, t in self.items if k in ("h1", "h2", "p")).split()


def book_content(seed: int = 0, chapters: int = 3, paras: int = 6, figures: bool = False, tables: bool = False) -> Book:
    rng = random.Random(seed)
    book = Book()
    for c in range(1, chapters + 1):
        book.items.append(("h1", f"Chapter {c}"))
        body = prose(rng, paras)
        for i, p in enumerate(body):
            book.items.append(("p", p))
            if figures and i == 1:
                book.items.append(("fig", f"fig{c}"))
            if tables and i == 2:
                book.items.append(("table", f"table{c}"))
            if i == paras // 2:
                book.items.append(("h2", f"Accounts of {1886 + c}"))
    return book


TABLE_ROWS = [["Vessel", "Tons", "Owed (£)"], ["Northern Star", "84", "112"], ["Grace Hale", "61", "95"], ["Tern", "40", "105"]]


def make_book(
    path: Path,
    book: Book,
    columns: int = 1,
    font: str = "tiro",
    body_size: float = 11,
    width: float = 612,
    height: float = 792,
    header: str = "Wexmoor Harbour",
) -> Path:
    margin, gutter = 72, 24
    col_w = (width - 2 * margin - gutter * (columns - 1)) / columns
    doc = pymupdf.open()
    state = {"col": columns, "y": 0.0, "page": None, "shape": None}

    def new_column() -> None:
        state["col"] += 1
        if state["col"] >= columns:
            if state["shape"]:
                state["shape"].commit()
            page = doc.new_page(width=width, height=height)
            if header:
                page.insert_text((margin, 40), header, fontsize=8, fontname="tiro")
                page.insert_text((width / 2 - 4, height - 30), str(doc.page_count), fontsize=8, fontname="tiro")
            state.update(page=page, shape=page.new_shape(), col=0)
        state["y"] = margin

    def x0() -> float:
        return margin + state["col"] * (col_w + gutter)

    def ensure(space: float) -> None:
        if state["page"] is None or state["y"] + space > height - margin:
            new_column()

    def put_line(dx: float, text: str, size: float, fontname: str) -> None:
        ensure(size * 1.45)
        state["y"] += size * 1.45
        state["shape"].insert_text((x0() + dx, state["y"]), text, fontsize=size, fontname=fontname)

    bold = {"tiro": "tibo", "helv": "hebo", "cour": "cobo"}[font]
    for kind, text in book.items:
        if kind in ("h1", "h2"):
            size = body_size * (2 if kind == "h1" else 1.35)
            if kind == "h1" and state["page"] is not None and columns == 1:
                state["col"] = columns  # chapters start on a new page
                new_column()
            ensure(size * 4)
            state["y"] += size * 0.8
            put_line(0, text, size, bold)
            state["y"] += size * 0.4
        elif kind == "p":
            size, indent, line, first = body_size, body_size * 1.5, [], True
            for word in text.split():
                avail = col_w - (indent if first else 0)
                if line and pymupdf.get_text_length(" ".join([*line, word]), font, size) > avail:
                    put_line(indent if first else 0, " ".join(line), size, font)
                    line, first = [word], False
                else:
                    line.append(word)
            put_line(indent if first else 0, " ".join(line), size, font)
        elif kind == "fig":
            fig_h = min(col_w * 0.6, 180)
            ensure(fig_h + 20)
            rect = pymupdf.Rect(x0(), state["y"] + 10, x0() + col_w * 0.8, state["y"] + 10 + fig_h)
            state["page"].insert_image(rect, stream=_figure_png(int(rect.width * 3), int(rect.height * 3), text))
            state["y"] = rect.y1 + 10
        elif kind == "table":
            row_h = body_size * 1.8
            ensure(row_h * len(TABLE_ROWS) + 20)
            top = state["y"] + 10
            col_x = [x0(), x0() + col_w * 0.45, x0() + col_w * 0.7, x0() + col_w * 0.95]
            shape = state["shape"]
            for r in range(len(TABLE_ROWS) + 1):
                shape.draw_line((col_x[0], top + r * row_h), (col_x[-1], top + r * row_h))
            for cx in col_x:
                shape.draw_line((cx, top), (cx, top + row_h * len(TABLE_ROWS)))
            shape.finish(color=(0, 0, 0), width=0.6)
            for r, row in enumerate(TABLE_ROWS):
                for c, cell in enumerate(row):
                    shape.insert_text((col_x[c] + 4, top + r * row_h + row_h * 0.68), cell, fontsize=body_size * 0.9, fontname=bold if r == 0 else font)
            state["y"] = top + row_h * len(TABLE_ROWS) + 10
    if state["shape"]:
        state["shape"].commit()
    doc.set_metadata({"title": "", "author": ""})
    doc.save(path)
    doc.close()
    return path


def _figure_png(w: int, h: int, seed_text: str) -> bytes:
    rng = np.random.default_rng(abs(hash(seed_text)) % 2**32)
    img = np.full((h, w, 3), 245, np.uint8)
    for _ in range(12):
        center = (int(rng.integers(0, w)), int(rng.integers(0, h)))
        color = tuple(int(c) for c in rng.integers(40, 200, 3))
        cv2.circle(img, center, int(rng.integers(h // 10, h // 3)), color, -1)
    cv2.rectangle(img, (0, 0), (w - 1, h - 1), (60, 60, 60), 4)
    ok, buf = cv2.imencode(".png", img)
    assert ok
    return buf.tobytes()


def scan(
    src: Path,
    dest: Path,
    dpi: int = 300,
    skew_deg: float = 0.0,
    noise: float = 0.0,
    blur: bool = False,
    jpeg_quality: int = 85,
    rotate90: int = 0,
    seed: int = 0,
) -> Path:
    """Render every page to a degraded image and store it in an image-only PDF."""
    rng = np.random.default_rng(seed)
    out = pymupdf.open()
    with pymupdf.open(src) as doc:
        for page in doc:
            pix = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csRGB)
            img = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, 3).copy()
            if skew_deg:
                h, w = img.shape[:2]
                m = cv2.getRotationMatrix2D((w / 2, h / 2), skew_deg, 1.0)
                img = cv2.warpAffine(img, m, (w, h), borderValue=(255, 255, 255))
            if blur:
                img = cv2.GaussianBlur(img, (3, 3), 0.8)
            if noise:
                img = np.clip(img.astype(np.float32) + rng.normal(0, noise, img.shape), 0, 255).astype(np.uint8)
            if rotate90:
                img = np.ascontiguousarray(np.rot90(img, k=-rotate90 // 90))
            ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, jpeg_quality])
            assert ok
            w_pt, h_pt = img.shape[1] * 72 / dpi, img.shape[0] * 72 / dpi
            new = out.new_page(width=w_pt, height=h_pt)
            new.insert_image(new.rect, stream=io.BytesIO(buf.tobytes()).getvalue())
    out.save(dest)
    out.close()
    return dest


def word_accuracy(expected: list[str], actual: list[str]) -> float:
    """1 - word error rate (Levenshtein distance over words / reference length)."""
    n, m = len(expected), len(actual)
    prev = list(range(m + 1))
    for i in range(1, n + 1):
        cur = [i] + [0] * m
        for j in range(1, m + 1):
            cost = 0 if expected[i - 1] == actual[j - 1] else 1
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
        prev = cur
    return 1 - prev[m] / max(n, 1)
