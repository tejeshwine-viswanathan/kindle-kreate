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
from difflib import SequenceMatcher

from ..schema import Block, Line, PageResult

MARGIN_ZONE = 0.12  # top/bottom fraction of the page where running headers live
# Signatures (see _signature) that are nothing but a page number.
PAGE_NUMBER_SIGNATURES = {"#", "page #", "# of #", "# #", "p #"}
ROMAN_RE = re.compile(r"^[ivxlcdm]{1,7}$")
# OCR reads roman numerals with these look-alikes ("XX1", "xvi|", "xl!").
ROMAN_LOOKALIKES = str.maketrans({"1": "i", "l": "i", "|": "i", "!": "i", "0": "o"})
# Near-identical margin lines count as repeats (OCR noise: "INTRODUCTI0N", "xliti").
FUZZY_MIN_CHARS = 10
FUZZY_RATIO = 0.8
CHAPTER_RE = re.compile(r"^(chapter|part|book|prologue|epilogue|appendix)\b", re.IGNORECASE)
SENTENCE_END = tuple('.!?:;"”’)»…')
HYPHENS = ("-", "\u00ad", "\u2010")

# Table-of-contents entries look like headings but start with a section number and end
# with a page number ("8 Acknowledgments 35"), or end in dot leaders ("Abstract ...... 1").
TOC_ENTRY_RE = re.compile(r"^(\d+(\.\d+)*\s+\S.*\s\d+|.*\.{3,}\s*\d+)$")
MAX_HEADING_CHARS = 120
# On OCR pages a heading must read like words: tokens of letters with a vowel (or a short
# function word), no OCR debris (digits inside words, stray symbols).
WORDLIKE_RE = re.compile(r"^[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ'’-]*$")
VOWELS = set("aeiouyAEIOUYàáâäèéêëìíîïòóôöùúûü")
SHORT_WORDS = {"a", "i", "an", "of", "to", "in", "on", "by", "at", "or", "and", "the", "for", "mr", "mrs", "dr", "st"}
DEBRIS_RE = re.compile(r"[^\w\s.,;:'’\"“”!?()&-]|\d[A-Za-z]|[A-Za-z]\d")
# a capital inside a word ("CrRCULATION") is an OCR slip, except in Mc/Mac/O' names
NAME_PREFIX_RE = re.compile(r"\b(Mc|Mac|O')(?=[A-Z])")
INNER_CAPITAL_RE = re.compile(r"[a-z][A-Z]")
# A run of this many short same-level heading candidates on one page, with no body text
# between them, is a list (authors on a title page, a contents page), not headings.
HEADING_RUN = 3
HEADING_RUN_MAX_WORDS = 6
# OCR pages (ours, or a scan's embedded text layer) below this confidence get no headings.
MIN_HEADING_PAGE_CONFIDENCE = 60
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


def _is_page_token(token: str) -> bool:
    return token.isdigit() or ROMAN_RE.match(token.translate(ROMAN_LOOKALIKES)) is not None


def _lone_page_number(text: str) -> bool:
    """A margin line that is one short token made (mostly) of roman-numeral letters, e.g.
    "xliti" (OCR for xliii): not a valid numeral, but nothing else either."""
    words = re.sub(r"[^\w\s]", " ", text.lower()).split()
    if len(words) != 1 or len(words[0]) > 7:
        return False
    token = words[0].translate(ROMAN_LOOKALIKES)
    return token.isdigit() or sum(ch in "ivxlcdm" for ch in token) >= 0.7 * len(token)


def _signature(text: str) -> str:
    """Lower-cased words with page-number-like tokens replaced by '#', so "INTRODUCTION xix"
    and "INTRODUCTION XX1" (OCR for xxi) look like the same running head. OCR look-alike
    characters are folded inside words as well ("INTRODUCTI0N"); signatures are only ever
    compared with each other, so the folding just has to be consistent."""
    words = re.sub(r"[^\w\s]", " ", text.lower()).split()
    return " ".join("#" if _is_page_token(w) else w.translate(ROMAN_LOOKALIKES) for w in words)


def _in_margin(bbox: tuple[float, float, float, float], page: PageResult) -> bool:
    return bbox[3] <= page.height * MARGIN_ZONE or bbox[1] >= page.height * (1 - MARGIN_ZONE)


def _margin_lines(page: PageResult) -> list[tuple[Block, Line]]:
    """Lines that could be running heads: a whole block in the margin, or the first/last
    line of a block that starts or ends there (OCR often glues a header onto the body)."""
    found = []
    for block in page.blocks:
        if block.kind != "text" or not block.lines:
            continue
        if _in_margin(block.bbox, page):
            found.extend((block, line) for line in block.lines)
        else:
            edges = {id(block.lines[0]): block.lines[0], id(block.lines[-1]): block.lines[-1]}
            found.extend((block, line) for line in edges.values() if _in_margin(line.bbox, page))
    return found


class _SignatureCounter:
    """Counts signatures, folding near-identical ones together."""

    def __init__(self) -> None:
        self.counts: Counter[str] = Counter()

    def canonical(self, signature: str) -> str:
        if len(signature) >= FUZZY_MIN_CHARS:
            for known in self.counts:
                if len(known) >= FUZZY_MIN_CHARS and SequenceMatcher(None, signature, known).ratio() >= FUZZY_RATIO:
                    return known
        return signature

    def add(self, signatures: set[str]) -> None:
        for signature in signatures:
            self.counts[self.canonical(signature)] += 1

    def __getitem__(self, signature: str) -> int:
        return self.counts[self.canonical(signature)]


def strip_running_heads(pages: list[PageResult]) -> None:
    text_pages = [p for p in pages if p.blocks]
    min_repeats = max(2, min(3, math.ceil(len(text_pages) / 2)))

    counter = _SignatureCounter()
    for page in text_pages:
        counter.add({_signature(line.text) for _, line in _margin_lines(page)})

    for page in text_pages:
        doomed: set[int] = set()
        for block, line in _margin_lines(page):
            signature = _signature(line.text)
            if (
                signature in PAGE_NUMBER_SIGNATURES
                or _lone_page_number(line.text)
                or counter[signature] >= min_repeats
            ):
                doomed.add(id(line))
        if not doomed:
            continue
        kept_blocks = []
        for block in page.blocks:
            if block.kind == "text" and any(id(l) in doomed for l in block.lines):
                block.lines = [l for l in block.lines if id(l) not in doomed]
                if not block.lines:
                    continue
                block.bbox = (
                    min(l.bbox[0] for l in block.lines),
                    min(l.bbox[1] for l in block.lines),
                    max(l.bbox[2] for l in block.lines),
                    max(l.bbox[3] for l in block.lines),
                )
            kept_blocks.append(block)
        page.blocks = kept_blocks


# --- headings ------------------------------------------------------------------------


def body_font_size(pages: list[PageResult]) -> float:
    sizes: Counter[float] = Counter()
    for page in pages:
        for block in page.blocks:
            for line in block.lines:
                sizes[round(line.font_size * 2) / 2] += len(line.text)
    return sizes.most_common(1)[0][0] if sizes else 10.0


def _beside(block: Block, other: Block) -> bool:
    """`other` sits on the same line as `block`, right next to it (a word gap, not a column
    gutter). OCR text layers box a paragraph's first word separately and oversize it."""
    top, bottom = max(block.bbox[1], other.bbox[1]), min(block.bbox[3], other.bbox[3])
    height = min(block.bbox[3] - block.bbox[1], other.bbox[3] - other.bbox[1])
    if bottom - top < height * 0.5:
        return False
    gap = max(other.bbox[0] - block.bbox[2], block.bbox[0] - other.bbox[2])
    return -height * 0.2 < gap < other.font_size * 1.2  # a word gap in the neighbour's text


def demote_inline_headings(levels: dict[int, int | None], blocks: list[Block]) -> None:
    text_blocks = [b for b in blocks if b.kind == "text"]
    for block in text_blocks:
        if levels.get(id(block)) is None:
            continue
        if any(other is not block and levels.get(id(other)) is None and _beside(block, other) for other in text_blocks):
            levels[id(block)] = None


STRICT_ROMAN_RE = re.compile(r"^M{0,3}(CM|CD|D?C{0,3})(XC|XL|L?X{0,3})(IX|IV|V?I{0,3})\.?$")


def _plausible_ocr_title(text: str) -> bool:
    """On OCR pages sizes lie, so the text itself must look like a title: real words, and
    either 3+ of them, a "Chapter ..." line, or ALL CAPS with at least three letters (or a
    roman numeral). Sentence fragments starting in lower case are never titles."""
    if not _reads_like_words(text) or text[:1].islower():
        return False
    if CHAPTER_RE.match(text) or len(text.split()) >= 3:
        return True
    letters = sum(ch.isalpha() for ch in text)
    return text.isupper() and (letters >= 3 or STRICT_ROMAN_RE.match(text) is not None)


def _reads_like_words(text: str) -> bool:
    if DEBRIS_RE.search(text) or INNER_CAPITAL_RE.search(NAME_PREFIX_RE.sub("", text)):
        return False
    tokens = [t.strip(".,;:!?()'’\"“”") for t in text.split()]
    tokens = [t for t in tokens if t and not ROMAN_RE.match(t.lower()) and not t.isdigit()]
    if not tokens:
        return True
    good = sum(
        1 for t in tokens
        if WORDLIKE_RE.match(t) and (t.lower() in SHORT_WORDS or (len(t) >= 2 and any(ch in VOWELS for ch in t)))
    )
    return good >= len(tokens) * 0.75


def demote_heading_runs(levels: dict[int, int | None], blocks: list[Block]) -> None:
    """Three or more short headings of one level in a row are a list, not headings: author
    names on a paper's title page (each may be followed by a one-line affiliation), a
    contents page. A heading directly followed by real body text is kept: it is that
    text's heading."""
    run: list[Block] = []
    last_was_heading = False

    def flush(keep_last: bool) -> None:
        candidates = run[:-1] if keep_last else run
        if len(candidates) >= HEADING_RUN:
            for b in candidates:
                levels[id(b)] = None
        run.clear()

    for block in blocks:
        if block.kind != "text":
            continue
        level = levels.get(id(block))
        if level is not None and len(block.text.split()) <= HEADING_RUN_MAX_WORDS:
            if run and levels[id(run[-1])] != level:
                flush(keep_last=False)
            run.append(block)
            last_was_heading = True
            continue
        if run and last_was_heading and level is None and len(block.lines) <= 2 and len(block.text) <= 80:
            last_was_heading = False  # a short tail (affiliation + e-mail, page reference) stays in the run
            continue
        flush(keep_last=last_was_heading and level is None)
        last_was_heading = False
    flush(keep_last=False)


def heading_level(block: Block, body_size: float, noisy: bool = False) -> int | None:
    """`noisy`: the sizes come from OCR boxes, so a lone oversized word ("He") is not
    evidence of a heading; demand a real title (3+ words, ALL CAPS, or "Chapter ...")."""
    text = block.text
    if (
        len(text) > MAX_HEADING_CHARS
        or len(block.lines) > MAX_HEADING_LINES
        or text.endswith((".", ",", ";", ":"))
        or not any(ch.isalpha() for ch in text)
    ):
        return None
    if noisy and not _plausible_ocr_title(text):
        return None
    ratio = block.font_size / body_size
    if ratio >= 1.6 or (ratio >= 1.15 and CHAPTER_RE.match(text)):
        return 1
    if TOC_ENTRY_RE.match(text):
        return None
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

        noisy = page.source == "ocr"
        if noisy and page.confidence is not None and page.confidence < MIN_HEADING_PAGE_CONFIDENCE:
            levels = {id(b): None for b in page.blocks if b.kind == "text"}
        else:
            levels = {id(b): heading_level(b, body_size, noisy) for b in page.blocks if b.kind == "text"}
        demote_inline_headings(levels, page.blocks)
        demote_heading_runs(levels, page.blocks)
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
