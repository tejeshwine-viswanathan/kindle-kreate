"""Reading-order reconstruction via recursive XY-cut.

Each step makes one cut through an empty band and orders both sides
recursively:

- a vertical cut (column gutter) wins when it leaves tall side-by-side parts,
  so columns are never interleaved;
- otherwise a horizontal cut is preferred next to a full-width element (a
  title, figure, or footnote spanning the columns), because a gap between two
  paragraphs can line up across columns by coincidence;
- failing both, the widest gap is cut.
"""

from __future__ import annotations

from ..schema import Block

MIN_GAP = 2.0  # points; smaller gaps count as touching
COLUMN_MIN_HEIGHT = 0.5  # side-by-side parts at least this tall (of the region) are columns
FULL_WIDTH = 0.6  # a block at least this wide (of the region) spans the columns


def _groups(blocks: list[Block], lo: int, hi: int) -> tuple[list[list[Block]], list[float]]:
    """Split blocks at every empty band along one axis (lo/hi are bbox indices).
    Returns the groups in order and the gap size between each consecutive pair."""
    ordered = sorted(blocks, key=lambda b: b.bbox[lo])
    groups: list[list[Block]] = [[ordered[0]]]
    gaps: list[float] = []
    reach = ordered[0].bbox[hi]
    for block in ordered[1:]:
        gap = block.bbox[lo] - reach
        if gap > MIN_GAP:
            groups.append([block])
            gaps.append(gap)
        else:
            groups[-1].append(block)
        reach = max(reach, block.bbox[hi])
    return groups, gaps


def _extent(blocks: list[Block], lo: int, hi: int) -> float:
    return max(b.bbox[hi] for b in blocks) - min(b.bbox[lo] for b in blocks)


def _cut(groups: list[list[Block]], index: int) -> list[list[Block]]:
    """Merge groups into the two sides of the cut after groups[index]."""
    return [[b for g in groups[: index + 1] for b in g], [b for g in groups[index + 1 :] for b in g]]


def order_blocks(blocks: list[Block]) -> list[Block]:
    if len(blocks) <= 1:
        return list(blocks)

    width, height = _extent(blocks, 0, 2), _extent(blocks, 1, 3)
    columns, x_gaps = _groups(blocks, 0, 2)
    rows, y_gaps = _groups(blocks, 1, 3)

    parts: list[list[Block]] | None = None
    if x_gaps:
        side_by_side = _cut(columns, max(range(len(x_gaps)), key=x_gaps.__getitem__))
        if all(_extent(p, 1, 3) >= height * COLUMN_MIN_HEIGHT for p in side_by_side):
            parts = side_by_side
    if parts is None and y_gaps:
        spans = [any(b.bbox[2] - b.bbox[0] >= width * FULL_WIDTH for b in g) for g in rows]
        structural = [i for i in range(len(y_gaps)) if spans[i] or spans[i + 1]]
        candidates = structural or list(range(len(y_gaps)))
        best_y = max(candidates, key=y_gaps.__getitem__)
        if x_gaps and not structural and max(x_gaps) > y_gaps[best_y]:
            parts = _cut(columns, max(range(len(x_gaps)), key=x_gaps.__getitem__))
        else:
            parts = _cut(rows, best_y)
    if parts is None and x_gaps:
        parts = _cut(columns, max(range(len(x_gaps)), key=x_gaps.__getitem__))
    if parts is None:  # mutually overlapping blocks: plain top-to-bottom, left-to-right
        return sorted(blocks, key=lambda b: (round(b.bbox[1]), b.bbox[0]))
    return [b for part in parts for b in order_blocks(part)]
