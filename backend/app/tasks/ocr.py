"""OCR path for scanned pages: rasterize -> deskew -> binarize -> Tesseract -> PageResult.

Tesseract's own page segmentation (PSM 3) finds text blocks and paragraphs, so
columns come back as separate blocks; the shared reading-order and structure
stages then treat OCR pages exactly like text-layer pages. Coordinates are
converted from pixels to PDF points so both paths share one geometry.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
import pymupdf
import pytesseract

from ..config import settings
from ..schema import Block, Line, PageResult
from .extract import clean_text, store_image
from .layout import order_blocks

# Below this mean confidence a page is probably rotated; check orientation and retry.
ROTATION_CHECK_CONFIDENCE = 60
# Consecutive lines whose estimated font sizes differ by more than this start a new block.
# OCR size estimates are noisier than a text layer's, hence a looser ratio than extract.py's.
FONT_BREAK_RATIO = 1.3
NOISY_SCAN_SIGMA = 3.0
# Height of a word's box as a fraction of the font size, by which letter shapes it contains
# (typical book faces: ascender ~0.70em, x-height ~0.46em, descender ~0.22em).
ASCENDERS = set("bdfhiklt0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ([{/|'\"!?£$€%&#@")
DESCENDERS = set("gjpqyQ()[]{}/|,;")
HEIGHT_EM = {(True, True): 0.92, (True, False): 0.70, (False, True): 0.68, (False, False): 0.46}
FIGURE_MIN_WIDTH = 0.15  # fraction of page width
FIGURE_MIN_HEIGHT = 0.06  # fraction of page height
FIGURE_MAX_WIDTH_PX = 1600
NOISE_WORD_RE = re.compile(r"^[^\w£$€%&@#]{1,3}$")
# Lines below this mean confidence are Tesseract "reading" an illustration or scanner
# noise ("‘Zz", "fay)"); genuine text on a poor scan still scores well above it.
MIN_LINE_CONFIDENCE = 40
# A page with fewer words than this at low overall confidence is an illustration page
# (cover, frontispiece): its figures are kept, its "text" is not.
SPARSE_PAGE_WORDS = 20
SPARSE_PAGE_CONFIDENCE = 60


class OcrUnavailable(RuntimeError):
    pass


@dataclass
class Word:
    text: str
    conf: float
    box: tuple[int, int, int, int]  # x0, y0, x1, y1 in pixels
    key: tuple[int, int, int]  # (block, paragraph, line)


# --- image preprocessing -----------------------------------------------------------


def render(page: pymupdf.Page, dpi: int) -> np.ndarray:
    pix = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csRGB, alpha=False)
    return np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, 3).copy()


def rotate(img: np.ndarray, degrees: float) -> np.ndarray:
    h, w = img.shape[:2]
    matrix = cv2.getRotationMatrix2D((w / 2, h / 2), degrees, 1.0)
    border = (255, 255, 255) if img.ndim == 3 else 255
    return cv2.warpAffine(img, matrix, (w, h), flags=cv2.INTER_LINEAR, borderValue=border)


def estimate_skew(gray: np.ndarray, max_degrees: float = 5.0) -> float:
    """Projection-profile search: text lines are sharpest (row sums vary most) when level."""
    scale = 800 / max(gray.shape)
    small = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    _, ink = cv2.threshold(small, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)
    if cv2.countNonZero(ink) < ink.size * 0.002:  # blank page
        return 0.0

    def score(angle: float) -> float:
        profile = rotate(ink, angle).sum(axis=1, dtype=np.float64)
        return float(np.sum(np.diff(profile) ** 2))

    def best(angles: np.ndarray) -> float:
        return float(max(angles, key=score))

    coarse = best(np.arange(-max_degrees, max_degrees + 1e-6, 0.5))
    fine = best(np.arange(coarse - 0.5, coarse + 0.5 + 1e-6, 0.05))
    return fine if score(fine) > score(0.0) * 1.01 else 0.0


def noise_level(gray: np.ndarray) -> float:
    """Robust estimate of pixel noise (sigma) from the residual of a median filter."""
    residual = gray.astype(np.int16) - cv2.medianBlur(gray, 3).astype(np.int16)
    return float(np.median(np.abs(residual)) * 1.4826)


def binarize(gray: np.ndarray) -> np.ndarray:
    # Global Otsu thresholding. A median filter helps only on noisy scans: on clean ones
    # it erodes thin strokes ("e" -> "c", "rn" -> "m") and cost ~4% word accuracy in tests.
    if noise_level(gray) > NOISY_SCAN_SIGMA:
        gray = cv2.medianBlur(gray, 3)
    _, bw = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
    return bw


# --- tesseract ------------------------------------------------------------------------


def _configure() -> None:
    cmd = settings.resolved_tesseract()
    if not cmd:
        raise OcrUnavailable("Tesseract is not installed")
    pytesseract.pytesseract.tesseract_cmd = cmd


def ocr_available() -> bool:
    return settings.resolved_tesseract() is not None


def run_tesseract(img: np.ndarray, psm: int = 3) -> list[Word]:
    data = pytesseract.image_to_data(
        img, lang=settings.ocr_lang, config=f"--oem 1 --psm {psm}", output_type=pytesseract.Output.DICT
    )
    words = []
    for i, raw in enumerate(data["text"]):
        conf = float(data["conf"][i])
        text = clean_text(raw)
        if conf < 0 or not text:
            continue
        if conf < 40 and NOISE_WORD_RE.match(text):  # specks read as punctuation
            continue
        x, y, w, h = data["left"][i], data["top"][i], data["width"][i], data["height"][i]
        key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
        words.append(Word(text, conf, (x, y, x + w, y + h), key))
    return words


def drop_noise(words: list[Word]) -> list[Word]:
    """Remove lines Tesseract wasn't really reading, and the whole page's text when it
    is only a handful of doubtful words on top of a picture."""
    by_line: dict[tuple[int, int, int], list[Word]] = defaultdict(list)
    for w in words:
        by_line[w.key].append(w)
    kept = [w for line in by_line.values() if mean_confidence(line) >= MIN_LINE_CONFIDENCE for w in line]
    if len(kept) < SPARSE_PAGE_WORDS and mean_confidence(kept) < SPARSE_PAGE_CONFIDENCE:
        return []
    return kept


def mean_confidence(words: list[Word]) -> float:
    chars = sum(len(w.text) for w in words)
    return sum(w.conf * len(w.text) for w in words) / chars if chars else 100.0


def detect_rotation(img: np.ndarray) -> int:
    try:
        osd = pytesseract.image_to_osd(img, config="--psm 0")
    except pytesseract.TesseractError:  # too little text to decide
        return 0
    match = re.search(r"Rotate: (\d+)", osd)
    return int(match.group(1)) if match else 0


# --- figures -------------------------------------------------------------------------


def find_figures(bw: np.ndarray, words: list[Word]) -> list[tuple[int, int, int, int]]:
    """Large regions of ink that remain once recognized words are masked out."""
    ink = cv2.bitwise_not(bw)
    for w in words:
        if w.conf >= 40:
            x0, y0, x1, y1 = w.box
            ink[max(0, y0 - 3) : y1 + 3, max(0, x0 - 3) : x1 + 3] = 0
    h, w = ink.shape
    merged = cv2.dilate(ink, cv2.getStructuringElement(cv2.MORPH_RECT, (25, 25)))
    count, _, stats, _ = cv2.connectedComponentsWithStats(merged, connectivity=8)
    figures = []
    for x, y, bw_, bh, _area in stats[1:count]:
        if bw_ < w * FIGURE_MIN_WIDTH or bh < h * FIGURE_MIN_HEIGHT:
            continue
        if x <= 2 or y <= 2 or x + bw_ >= w - 2 or y + bh >= h - 2:  # scanner borders/shadows
            continue
        # undo the dilation margin
        figures.append((x + 12, y + 12, x + bw_ - 12, y + bh - 12))
    return figures


def save_figure(rgb: np.ndarray, box: tuple[int, int, int, int], image_dir: Path) -> str:
    x0, y0, x1, y1 = box
    crop = rgb[y0:y1, x0:x1]
    if crop.shape[1] > FIGURE_MAX_WIDTH_PX:
        factor = FIGURE_MAX_WIDTH_PX / crop.shape[1]
        crop = cv2.resize(crop, None, fx=factor, fy=factor, interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", cv2.cvtColor(crop, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, 85])
    if not ok:
        raise ValueError("could not encode figure")
    return store_image(buf.tobytes(), "jpg", image_dir)


def _rule_positions(profile: np.ndarray, length: int) -> list[int]:
    """Centers of runs where a ruling line covers most of the region's length."""
    positions, start = [], None
    for i, covered in enumerate(profile > length * 0.9):
        if covered and start is None:
            start = i
        elif not covered and start is not None:
            positions.append((start + i - 1) // 2)
            start = None
    if start is not None:
        positions.append((start + len(profile) - 1) // 2)
    return positions


def read_table(bw: np.ndarray, box: tuple[int, int, int, int]) -> list[list[str]] | None:
    """Cells of a fully ruled grid, or None if the region isn't a simple table (then it is
    kept as an image: no grid, merged cells, or words straddling a column rule).

    Page-level segmentation treats a ruled table as a picture and skips its text, so the
    region is OCR'd again on its own, with the ruling lines erased, in sparse-text mode."""
    x0, y0, x1, y1 = box
    ink = cv2.bitwise_not(bw[y0:y1, x0:x1])
    h, w = ink.shape
    if h < 20 or w < 20:
        return None
    horizontal = cv2.morphologyEx(ink, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (max(w // 3, 10), 1)))
    vertical = cv2.morphologyEx(ink, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(h // 3, 10))))
    rows = _rule_positions(horizontal.sum(axis=1) / 255, w)
    cols = _rule_positions(vertical.sum(axis=0) / 255, h)
    if len(rows) < 3 or len(cols) < 3:  # need at least a header row + one row, two columns
        return None
    # a vertical rule that stops part-way down means merged cells (pixels right next to a
    # full rule are only partly covered when a trace of skew remains, so skip those)
    coverage = vertical.sum(axis=0) / 255
    near_rule = np.zeros(w, bool)
    for c in cols:
        near_rule[max(0, c - 6) : c + 7] = True
    if np.any((coverage > h * 0.25) & (coverage < h * 0.9) & ~near_rule):
        return None

    rules = cv2.dilate(cv2.bitwise_or(horizontal, vertical), np.ones((5, 5), np.uint8))
    text_only = cv2.bitwise_not(cv2.subtract(ink, rules))
    cells: list[list[list[Word]]] = [[[] for _ in cols[1:]] for _ in rows[1:]]
    for wd in run_tesseract(text_only, psm=11):
        if wd.conf < 30:
            continue
        wx0, wx1 = wd.box[0], wd.box[2]
        if any(wx0 + 2 < c < wx1 - 2 for c in cols):
            return None  # text crosses a column rule
        cx, cy = (wx0 + wx1) / 2, (wd.box[1] + wd.box[3]) / 2
        r = next((i for i in range(len(rows) - 1) if rows[i] <= cy < rows[i + 1]), None)
        c = next((i for i in range(len(cols) - 1) if cols[i] <= cx < cols[i + 1]), None)
        if r is not None and c is not None:
            cells[r][c].append(wd)
    table = [
        [" ".join(wd.text for wd in sorted(cell, key=lambda wd: (wd.key, wd.box[0]))) for cell in row]
        for row in cells
    ]
    return table if any(any(cell for cell in row) for row in table) else None


def _inside(word: Word, box: tuple[int, int, int, int]) -> bool:
    cx = (word.box[0] + word.box[2]) / 2
    cy = (word.box[1] + word.box[3]) / 2
    return box[0] <= cx <= box[2] and box[1] <= cy <= box[3]


# --- assembly ------------------------------------------------------------------------


def _lines(words: list[Word], scale: float) -> dict[tuple[int, int], list[Line]]:
    """Group words into lines, keyed by (block, paragraph)."""
    by_line: dict[tuple[int, int, int], list[Word]] = defaultdict(list)
    for w in words:
        by_line[w.key].append(w)

    paragraphs: dict[tuple[int, int], list[Line]] = defaultdict(list)
    for key in sorted(by_line):
        line_words = sorted(by_line[key], key=lambda w: w.box[0])
        x0 = min(w.box[0] for w in line_words)
        y0 = min(w.box[1] for w in line_words)
        x1 = max(w.box[2] for w in line_words)
        y1 = max(w.box[3] for w in line_words)
        paragraphs[key[:2]].append(
            Line(
                text=" ".join(w.text for w in line_words),
                bbox=(x0 * scale, y0 * scale, x1 * scale, y1 * scale),
                font_size=_font_size(line_words, y1 - y0) * scale,
            )
        )
    return paragraphs


def _font_size(words: list[Word], line_height: int) -> float:
    """Median per-word em size, correcting each box height for the letter shapes it holds.
    ("Accounts of 1887" has no descenders, so its raw box is shorter than body text's.)"""
    estimates = []
    for w in words:
        if not any(ch.isalnum() for ch in w.text):
            continue
        shape = (any(ch in ASCENDERS for ch in w.text), any(ch in DESCENDERS for ch in w.text))
        estimates.append((w.box[3] - w.box[1]) / HEIGHT_EM[shape])
    return float(np.median(estimates)) if estimates else line_height / HEIGHT_EM[(True, True)]


def _blocks(paragraphs: dict[tuple[int, int], list[Line]]) -> list[Block]:
    blocks = []
    for lines in paragraphs.values():
        current: list[Line] = []
        for line in lines:
            if current:
                prev = current[-1]
                ratio = max(line.font_size, prev.font_size) / max(min(line.font_size, prev.font_size), 0.1)
                if ratio > FONT_BREAK_RATIO:
                    blocks.append(_text_block(current))
                    current = []
            current.append(line)
        if current:
            blocks.append(_text_block(current))
    return blocks


def merge_stacked_blocks(blocks: list[Block]) -> list[Block]:
    """Rejoin text blocks that continue each other within a column.

    Tesseract's paragraph grouping is unreliable (it splits where a line happens to end
    a sentence, and glues the next paragraph's first lines onto the previous one), so
    blocks at normal line spacing with the same font size are merged, and paragraphs are
    re-split later by indentation, the same rule used for text-layer PDFs."""
    text = sorted((b for b in blocks if b.kind == "text"), key=lambda b: b.bbox[1])
    others = [b for b in blocks if b.kind != "text"]
    merged = True
    while merged:
        merged = False
        for i, upper in enumerate(text):
            for j in range(i + 1, len(text)):
                lower = text[j]
                if _stacked(upper, lower) and not any(
                    _between(upper, lower, other) for other in text + others if other is not upper and other is not lower
                ):
                    text[i] = _text_block(upper.lines + lower.lines)
                    del text[j]
                    merged = True
                    break
            if merged:
                break
    return others + text


def _stacked(upper: Block, lower: Block) -> bool:
    size = min(upper.font_size, lower.font_size)
    gap = lower.bbox[1] - upper.bbox[3]
    overlap = min(upper.bbox[2], lower.bbox[2]) - max(upper.bbox[0], lower.bbox[0])
    narrower = min(upper.bbox[2] - upper.bbox[0], lower.bbox[2] - lower.bbox[0])
    ratio = max(upper.font_size, lower.font_size) / max(size, 0.1)
    return -size * 0.5 < gap < size * 0.9 and overlap > narrower * 0.5 and ratio <= 1.15


def _between(upper: Block, lower: Block, other: Block) -> bool:
    overlaps_x = min(upper.bbox[2], other.bbox[2]) > max(upper.bbox[0], other.bbox[0])
    return overlaps_x and other.bbox[1] >= upper.bbox[3] - 1 and other.bbox[3] <= lower.bbox[1] + 1


def _text_block(lines: list[Line]) -> Block:
    return Block(
        kind="text",
        bbox=(
            min(l.bbox[0] for l in lines),
            min(l.bbox[1] for l in lines),
            max(l.bbox[2] for l in lines),
            max(l.bbox[3] for l in lines),
        ),
        lines=list(lines),
    )


def ocr_page(page: pymupdf.Page, image_dir: Path) -> PageResult:
    _configure()
    dpi = settings.ocr_dpi
    scale = 72 / dpi

    rgb = render(page, dpi)
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    angle = estimate_skew(gray)
    if angle:
        rgb, gray = rotate(rgb, angle), rotate(gray, angle)
    bw = binarize(gray)
    words = run_tesseract(bw)

    if mean_confidence(words) < ROTATION_CHECK_CONFIDENCE or not words:
        turn = detect_rotation(bw)
        if turn:
            code = {90: cv2.ROTATE_90_CLOCKWISE, 180: cv2.ROTATE_180, 270: cv2.ROTATE_90_COUNTERCLOCKWISE}[turn]
            rgb, bw = cv2.rotate(rgb, code), cv2.rotate(bw, code)
            words = run_tesseract(bw)

    words = drop_noise(words)
    blocks: list[Block] = []
    for box in find_figures(bw, words):
        bbox = tuple(v * scale for v in box)
        rows = read_table(bw, box)
        if rows:
            blocks.append(Block(kind="table", bbox=bbox, rows=rows))
        else:
            blocks.append(Block(kind="image", bbox=bbox, image=save_figure(rgb, box, image_dir)))
        words = [w for w in words if not _inside(w, box)]
    blocks.extend(_blocks(_lines(words, scale)))
    blocks = merge_stacked_blocks(blocks)

    height, width = bw.shape
    return PageResult(
        index=page.number,
        width=width * scale,
        height=height * scale,
        source="ocr",
        blocks=order_blocks(blocks),
        confidence=round(mean_confidence(words), 1) if words else None,
    )
