"""Document-level cleanup: turn per-page blocks into a clean semantic flow.

- strips running headers/footers and page numbers
- classifies headings from font-size/boldness relative to the body text
- rebuilds paragraphs: joins wrapped lines, de-hyphenates, and merges
  paragraphs that continue across page breaks
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field

from ..schema import Block, Line, PageResult

MARGIN_ZONE = 0.1  # top/bottom fraction of the page where running headers live
PAGE_NUMBER_RE = re.compile(r"^(page\s+)?([0-9]+|[ivxlcdm]+)(\s+(of|/)\s+[0-9]+)?$", re.IGNORECASE)
CHAPTER_RE = re.compile(r"^(chapter|part|book|prologue|epilogue|appendix)\b", re.IGNORECASE)
SENTENCE_END = tuple('.!?:;"”’)»…')
HYPHENS = ("-", "\u00ad", "\u2010")

MAX_HEADING_CHARS = 120
MAX_HEADING_LINES = 3


@dataclass
class Heading:
    level: int
    text: str


@dataclass
class Paragraph:
    text: str


@dataclass
class Image:
    name: str


@dataclass
class Table:
    rows: list[list[str]]  # first row is the header


@dataclass
class Notice:
    """Placeholder for content that could not be converted (e.g. a failed page)."""

    text: str


Element = Heading | Paragraph | Image | Table | Notice


@dataclass
class Document:
    title: str
    elements: list[Element] = field(default_factory=list)


# --- running headers / footers -------------------------------------------------------


def _signature(text: str) -> str:
    return re.sub(r"\d+", "#", text.lower()).strip()


def _in_margin(block: Block, page: PageResult) -> bool:
    return block.bbox[3] <= page.height * MARGIN_ZONE or block.bbox[1] >= page.height * (1 - MARGIN_ZONE)


def strip_running_heads(pages: list[PageResult]) -> None:
    text_pages = [p for p in pages if p.blocks]
    min_repeats = max(2, min(3, math.ceil(len(text_pages) / 2)))

    counts: Counter[str] = Counter()
    for page in text_pages:
        counts.update(
            {_signature(b.text) for b in page.blocks if b.kind == "text" and _in_margin(b, page)}
        )

    for page in text_pages:
        page.blocks = [
            b
            for b in page.blocks
            if not (
                b.kind == "text"
                and _in_margin(b, page)
                and (PAGE_NUMBER_RE.match(b.text) or counts[_signature(b.text)] >= min_repeats)
            )
        ]


# --- headings ------------------------------------------------------------------------


def body_font_size(pages: list[PageResult]) -> float:
    sizes: Counter[float] = Counter()
    for page in pages:
        for block in page.blocks:
            for line in block.lines:
                sizes[round(line.font_size * 2) / 2] += len(line.text)
    return sizes.most_common(1)[0][0] if sizes else 10.0


def heading_level(block: Block, body_size: float) -> int | None:
    text = block.text
    if (
        len(text) > MAX_HEADING_CHARS
        or len(block.lines) > MAX_HEADING_LINES
        or text.endswith((".", ",", ";", ":"))
        or not any(ch.isalpha() for ch in text)
    ):
        return None
    ratio = block.font_size / body_size
    if ratio >= 1.6 or (ratio >= 1.15 and CHAPTER_RE.match(text)):
        return 1
    if ratio >= 1.2:
        return 2
    if block.bold and ratio >= 0.95 and len(text) <= 80 and len(block.lines) == 1:
        return 3
    return None


# --- paragraphs ----------------------------------------------------------------------


def _hyphenated_words(pages: list[PageResult]) -> set[str]:
    """Compounds hyphenated mid-line ("well-known"), used to keep real hyphens at line ends."""
    words: set[str] = set()
    for page in pages:
        for block in page.blocks:
            for line in block.lines:
                words.update(w.lower() for w in re.findall(r"\b\w+-\w+\b", line.text))
    return words


def join_lines(left: str, right: str, compounds: set[str]) -> str:
    if not left:
        return right
    if left.endswith(HYPHENS) and len(left) > 1 and left[-2].isalpha() and right[:1].islower():
        stem = left[:-1]
        head = re.findall(r"\w+$", stem)
        tail = re.findall(r"^\w+", right)
        if left.endswith("-") and head and tail and f"{head[0]}-{tail[0]}".lower() in compounds:
            return left + right
        return stem + right
    return f"{left} {right}"


def _room_for(line: Line, gap: float) -> bool:
    """Would this line's first word have fit in `gap` at the end of the previous line?
    If so, the previous line ended early on purpose: a paragraph break, not word wrap."""
    first_word = line.text.split(" ", 1)[0]
    char_width = (line.bbox[2] - line.bbox[0]) / max(len(line.text), 1)
    return gap > (len(first_word) + 1) * char_width


def split_paragraphs(block: Block) -> list[list[Line]]:
    """Split a text block where a new first-line indent starts, or where a sentence ends
    on a line with room left for the next word."""
    left_edge = min(l.bbox[0] for l in block.lines)
    right_edge = max(l.bbox[2] for l in block.lines)
    paragraphs: list[list[Line]] = [[block.lines[0]]]
    for prev, line in zip(block.lines, block.lines[1:]):
        # indented relative to the line above too, so block quotes don't split per line;
        # no punctuation check, so an OCR slip ("initials," for "initials.") can't hide a break
        indent = line.bbox[0] - max(left_edge, prev.bbox[0])
        if indent > line.font_size * 0.8 or (
            prev.text.endswith(SENTENCE_END) and _room_for(line, right_edge - prev.bbox[2])
        ):
            paragraphs.append([line])
        else:
            paragraphs[-1].append(line)
    return paragraphs


@dataclass
class _Tail:
    """The most recent paragraph, kept open in case the next block continues it."""

    para: Paragraph
    line: Line  # its last line
    page: int
    bottom: float
    right_gap: float  # space left between its last line and the column's right edge


def _frame(line: Line, page_lines: list[Line]) -> tuple[float, float]:
    """Left/right edges of the column a line sits in: body lines overlapping most of it."""
    width = line.bbox[2] - line.bbox[0]
    peers = [
        l for l in page_lines if min(l.bbox[2], line.bbox[2]) - max(l.bbox[0], line.bbox[0]) > width * 0.5
    ] or [line]
    return min(l.bbox[0] for l in peers), max(l.bbox[2] for l in peers)


def _continues(tail: _Tail, text: str, first: Line, left_edge: float, flow_break: bool) -> bool:
    prev = tail.para.text
    if prev.endswith(HYPHENS) or text[:1].islower():
        return True
    if not prev.endswith(SENTENCE_END):
        return flow_break
    # A sentence ending exactly at the bottom of a column or page: the paragraph goes on
    # if that line was full width and the next column's first line isn't indented.
    indented = first.bbox[0] - left_edge > first.font_size * 0.8
    return flow_break and not indented and not _room_for(first, tail.right_gap)


# --- assembly ------------------------------------------------------------------------


def build_document(pages: list[PageResult], fallback_title: str, metadata_title: str = "") -> Document:
    pages = sorted(pages, key=lambda p: p.index)
    strip_running_heads(pages)
    body_size = body_font_size(pages)
    compounds = _hyphenated_words(pages)

    elements: list[Element] = []
    tail: _Tail | None = None

    for page in pages:
        if page.error:
            elements.append(Notice(f"[Page {page.index + 1} could not be converted: {page.error}]"))
            tail = None
            continue

        levels = {id(b): heading_level(b, body_size) for b in page.blocks if b.kind == "text"}
        body_lines = [l for b in page.blocks if b.kind == "text" and levels[id(b)] is None for l in b.lines]

        for block in page.blocks:
            if block.kind == "image":
                elements.append(Image(block.image))
                continue  # a figure doesn't end the paragraph flowing around it
            if block.kind == "table":
                elements.append(Table(block.rows))
                continue

            level = levels[id(block)]
            if level is not None:
                elements.append(Heading(level, block.text.replace("\u00ad", "")))
                tail = None
                continue

            for i, lines in enumerate(split_paragraphs(block)):
                text = ""
                for line in lines:
                    text = join_lines(text, line.text, compounds)
                first, last = lines[0], lines[-1]
                left_edge, _ = _frame(first, body_lines)
                # a new page, or a jump back up the page (the next column), breaks the flow
                flow_break = (
                    i == 0 and tail is not None and (tail.page != page.index or block.bbox[1] < tail.bottom - 1)
                )
                if tail is not None and _continues(tail, text, first, left_edge, flow_break):
                    tail.para.text = join_lines(tail.para.text, text, compounds)
                    para = tail.para
                else:
                    para = Paragraph(text)
                    elements.append(para)
                _, right_edge = _frame(last, body_lines)
                tail = _Tail(
                    para=para,
                    line=last,
                    page=page.index,
                    bottom=last.bbox[3],
                    right_gap=right_edge - last.bbox[2],
                )

    for el in elements:
        if isinstance(el, Paragraph):
            el.text = el.text.replace("\u00ad", "")

    _normalize_heading_levels(elements)
    # the first heading is usually "Chapter 1", so the file name is the better fallback
    return Document(title=metadata_title.strip() or fallback_title, elements=elements)


def _normalize_heading_levels(elements: list[Element]) -> None:
    """Shift levels so the top-most level in use becomes 1 (e.g. a document with only h2s)."""
    levels = sorted({el.level for el in elements if isinstance(el, Heading)})
    remap = {level: i + 1 for i, level in enumerate(levels)}
    for el in elements:
        if isinstance(el, Heading):
            el.level = remap[el.level]
