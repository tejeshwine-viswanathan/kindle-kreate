"""Normalized per-page intermediate format.

Both the text-layer path and the OCR path emit a `PageResult`; the document
cleanup stage and the EPUB builder only ever consume this schema.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

BBox = tuple[float, float, float, float]


class Line(BaseModel):
    text: str
    bbox: BBox
    font_size: float
    bold: bool = False


class Block(BaseModel):
    kind: Literal["text", "image", "table"]
    bbox: BBox
    lines: list[Line] = []
    # image blocks: file name inside the job's images/ directory
    image: str | None = None
    # table blocks: cell text, row by row (first row is the header)
    rows: list[list[str]] = []

    @property
    def text(self) -> str:
        return " ".join(line.text for line in self.lines)

    @property
    def font_size(self) -> float:
        chars = sum(len(line.text) for line in self.lines) or 1
        return sum(line.font_size * len(line.text) for line in self.lines) / chars

    @property
    def bold(self) -> bool:
        chars = sum(len(line.text) for line in self.lines) or 1
        return sum(len(line.text) for line in self.lines if line.bold) / chars > 0.5


class PageResult(BaseModel):
    index: int  # 0-based
    width: float
    height: float
    source: Literal["text", "ocr"]
    blocks: list[Block] = []
    error: str | None = None
    # OCR only: character-weighted mean word confidence (0-100)
    confidence: float | None = None
