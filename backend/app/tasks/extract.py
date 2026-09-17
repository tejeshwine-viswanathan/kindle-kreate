"""Fast path: pull text, geometry, and embedded images from a PDF's text layer (no OCR)."""

from __future__ import annotations

import contextlib
import hashlib
import io
import os
import unicodedata
from pathlib import Path

import pymupdf

from ..schema import Block, Line, PageResult
from .layout import order_blocks

BOLD_FLAG = 1 << 4
MIN_IMAGE_PX = 48  # smaller images are bullets/rules/decoration
# A line whose font size differs from the previous one by more than this ratio
# starts a new block (PyMuPDF often glues a heading onto the paragraph below it).
FONT_BREAK_RATIO = 1.15
EPUB_SAFE_EXTS = {"png", "jpeg", "jpg", "gif"}


def clean_text(text: str) -> str:
    text = unicodedata.normalize("NFC", text)
    # whitespace (incl. tabs) collapses below; drop other control/format chars and U+FFFD,
    # but keep soft hyphens so line joining can de-hyphenate on them
    text = "".join(
        ch
        for ch in text
        if ch.isspace() or ch == "\u00ad" or (unicodedata.category(ch)[0] != "C" and ch != "\ufffd")
    )
    return " ".join(text.split())


def store_image(data: bytes, ext: str, image_dir: Path) -> str:
    """Save image bytes under a content hash; safe when parallel workers save the same image."""
    name = f"{hashlib.sha1(data).hexdigest()[:16]}.{ext}"
    path = image_dir / name
    if not path.exists():
        tmp = path.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_bytes(data)
        os.replace(tmp, path)
    return name


def _save_image(block: dict, image_dir: Path) -> str | None:
    if block.get("width", 0) < MIN_IMAGE_PX or block.get("height", 0) < MIN_IMAGE_PX:
        return None
    data: bytes = block["image"]
    ext = block.get("ext", "png").lower()
    if ext not in EPUB_SAFE_EXTS:  # jpx, jb2, tiff, ... aren't supported by e-readers
        try:
            data = pymupdf.Pixmap(data).tobytes("png")
        except Exception:
            return None
        ext = "png"
    return store_image(data, "jpg" if ext == "jpeg" else ext, image_dir)


def _same_baseline(left: Line, right: Line) -> bool:
    overlap = min(left.bbox[3], right.bbox[3]) - max(left.bbox[1], right.bbox[1])
    height = min(left.bbox[3] - left.bbox[1], right.bbox[3] - right.bbox[1])
    return overlap > height * 0.5 and right.bbox[0] >= left.bbox[2] - 2


def _merge_lines(left: Line, right: Line) -> Line:
    return Line(
        text=f"{left.text} {right.text}",
        bbox=(left.bbox[0], min(left.bbox[1], right.bbox[1]), right.bbox[2], max(left.bbox[3], right.bbox[3])),
        font_size=left.font_size if len(left.text) >= len(right.text) else right.font_size,
        bold=left.bold if len(left.text) >= len(right.text) else right.bold,
    )


def _text_blocks(raw: dict) -> list[Block]:
    blocks: list[Block] = []
    current: list[Line] = []

    def flush() -> None:
        if current:
            x0 = min(l.bbox[0] for l in current)
            y0 = min(l.bbox[1] for l in current)
            x1 = max(l.bbox[2] for l in current)
            y1 = max(l.bbox[3] for l in current)
            blocks.append(Block(kind="text", bbox=(x0, y0, x1, y1), lines=list(current)))
            current.clear()

    for line in raw["lines"]:
        dx, dy = line["dir"]
        if abs(dy) > 0.1 or dx < 0:  # rotated/vertical text: margin stamps, spine labels
            continue
        spans = [s for s in line["spans"] if s["text"].strip()]
        if not spans:
            continue
        text = clean_text("".join(s["text"] for s in line["spans"]))
        if not text:
            continue
        chars = sum(len(s["text"]) for s in spans)
        size = sum(s["size"] * len(s["text"]) for s in spans) / chars
        bold_chars = sum(
            len(s["text"]) for s in spans if s["flags"] & BOLD_FLAG or "bold" in s["font"].lower()
        )
        new_line = Line(text=text, bbox=tuple(line["bbox"]), font_size=size, bold=bold_chars / chars > 0.5)

        if current:
            prev = current[-1]
            ratio = max(size, prev.font_size) / max(min(size, prev.font_size), 0.1)
            if ratio <= FONT_BREAK_RATIO and _same_baseline(prev, new_line):
                # justified text is often reported as several "lines" per visual line
                current[-1] = _merge_lines(prev, new_line)
                continue
            if ratio > FONT_BREAK_RATIO or new_line.bold != prev.bold:
                flush()
        current.append(new_line)
    flush()
    return blocks


def _tables(page: pymupdf.Page, image_dir: Path) -> list[Block]:
    """Ruled tables: simple ones as cell text, ones with merged cells as an image of the region
    (mangled text from a complex table is worse than a picture of it)."""
    if not page.get_drawings():  # tables are found from ruling lines; skip plain text pages
        return []
    try:
        with contextlib.redirect_stdout(io.StringIO()):  # find_tables prints an advert
            found = page.find_tables()
    except Exception:
        return []
    blocks = []
    for table in found.tables:
        if table.row_count < 2 or table.col_count < 2:
            continue
        bbox = tuple(pymupdf.Rect(table.bbox) & page.rect)
        rows = table.extract()
        if any(cell is None for row in rows for cell in row):
            pix = page.get_pixmap(clip=bbox, dpi=200)
            blocks.append(Block(kind="image", bbox=bbox, image=store_image(pix.tobytes("png"), "png", image_dir)))
        else:
            blocks.append(Block(kind="table", bbox=bbox, rows=[[clean_text(cell) for cell in row] for row in rows]))
    return blocks


def _inside_any(block: Block, regions: list[Block]) -> bool:
    cx = (block.bbox[0] + block.bbox[2]) / 2
    cy = (block.bbox[1] + block.bbox[3]) / 2
    return any(r.bbox[0] <= cx <= r.bbox[2] and r.bbox[1] <= cy <= r.bbox[3] for r in regions)


def extract_page(page: pymupdf.Page, image_dir: Path) -> PageResult:
    raw = page.get_text("dict", flags=pymupdf.TEXTFLAGS_DICT & ~pymupdf.TEXT_PRESERVE_LIGATURES)
    page_rect = page.rect
    blocks: list[Block] = []

    for raw_block in raw["blocks"]:
        bbox = pymupdf.Rect(raw_block["bbox"]) & page_rect
        if bbox.is_empty:
            continue
        if raw_block["type"] == 1:
            name = _save_image(raw_block, image_dir)
            if name:
                blocks.append(Block(kind="image", bbox=tuple(bbox), image=name))
        else:
            blocks.extend(_text_blocks(raw_block))

    tables = _tables(page, image_dir)
    if tables:
        blocks = [b for b in blocks if not _inside_any(b, tables)] + tables

    return PageResult(
        index=page.number,
        width=page_rect.width,
        height=page_rect.height,
        source="text",
        blocks=order_blocks(blocks),
    )
