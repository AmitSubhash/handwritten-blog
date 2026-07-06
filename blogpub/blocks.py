"""Center each content block of a handwritten page independently.

A scanned handwritten page usually holds several distinct blocks -- a short
heading, a paragraph, a row of icons, a footer -- separated by vertical
whitespace. Centering the whole page by one global shift can't center each of
those on its own: correcting a left-drifted heading would drag an
already-centered paragraph off to the side.

This segments the page into blocks by their vertical whitespace gaps (a
row-projection profile: rows with ink vs empty rows; a gap taller than a
threshold starts a new block) and horizontally centers each block on its own,
anchoring on the ink bounding-box midpoint (robust to ink-density asymmetry,
e.g. a heavily-filled icon), preserving vertical positions and gaps.

Design guards (validated with the Fable design pass) keep it from touching
layouts where horizontal position encodes meaning:
  * dead-zone -- leave already-near-centered blocks untouched,
  * skip-not-clamp -- a block needing a huge shift is mis-segmented or placed
    on purpose (corner signature), so leave it, don't yank it partway,
  * left-aligned groups -- consecutive blocks sharing a left edge are a
    deliberate list/outline; don't center them individually,
  * corner marginalia -- a small block far off-center is placed on purpose,
  * over-segmentation fallback -- if too many blocks appear, treat the page as
    one block (a plain global center).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

GAP_FRAC = 0.022
GAP_FLOOR_PX = 40  # short trimmed pages must not over-split
DEADZONE_FRAC = 0.022  # only correct clear leans (>~2% off); keep near-centered as-is
MAX_SHIFT_FRAC = 0.18  # beyond this a block is mis-segmented / placed on purpose
INK_THRESHOLD = 128
MIN_INK_ROW_FRAC = 0.003  # a row needs >=0.3% dark px to count (rejects speckle)
LEFT_EDGE_TOL_FRAC = 0.04  # blocks within this share a "left edge"
MARGINALIA_MAX_WIDTH_FRAC = 0.20  # a block narrower than this...
MARGINALIA_MIN_OFFSET_FRAC = 0.25  # ...and this far off-center is left alone
MAX_BLOCKS = 8  # more than this = over-segmentation


@dataclass(frozen=True)
class BlockShift:
    """How one block was moved: its ``rows``/``cols`` extents, the shift that
    would perfectly center it (``raw_shift``), and the shift actually applied
    (``applied_shift``; 0 means left untouched)."""

    rows: tuple[int, int]
    cols: tuple[int, int]
    raw_shift: int
    applied_shift: int


def _segment(
    ink: np.ndarray, gap_thresh: int, min_ink_px: int
) -> list[tuple[int, int]]:
    row_ink = ink.sum(axis=1) >= min_ink_px
    ink_rows = np.flatnonzero(row_ink)
    if ink_rows.size == 0:
        return []
    blocks: list[tuple[int, int]] = []
    start = prev = int(ink_rows[0])
    for r in ink_rows[1:]:
        r = int(r)
        if r - prev - 1 >= gap_thresh:
            blocks.append((start, prev))
            start = r
        prev = r
    blocks.append((start, prev))
    return blocks


def _extent(ink: np.ndarray, r0: int, r1: int) -> tuple[int, int] | None:
    cols = np.flatnonzero(ink[r0 : r1 + 1].any(axis=0))
    if cols.size == 0:
        return None
    return int(cols[0]), int(cols[-1])


def center_blocks(
    in_path: Path,
    out_path: Path,
    *,
    gap_frac: float = GAP_FRAC,
    deadzone_frac: float = DEADZONE_FRAC,
    max_shift_frac: float = MAX_SHIFT_FRAC,
    ink_threshold: int = INK_THRESHOLD,
) -> list[BlockShift]:
    """Segment a page into vertical blocks and horizontally center each one.

    Parameters
    ----------
    in_path : Path
        Source page PNG (dark ink on a light background).
    out_path : Path
        Where to write the result (may equal ``in_path``).
    gap_frac : float, optional
        A vertical whitespace gap at least ``max(gap_frac*height, 40px)`` tall
        starts a new block -- paragraph-scale, so lines group into paragraphs.
    deadzone_frac : float, optional
        Blocks whose required shift is smaller than this (fraction of width)
        are left untouched.
    max_shift_frac : float, optional
        A block needing a larger shift than this is left untouched (skip, not
        clamp) -- it's mis-segmented or intentionally placed.
    ink_threshold : int, optional
        Grayscale value below which a pixel counts as ink (0-255).

    Returns
    -------
    list of BlockShift
        One record per detected block.
    """
    img = Image.open(in_path).convert("L")
    arr = np.asarray(img)
    height, width = arr.shape
    ink = arr < ink_threshold

    gap_thresh = max(GAP_FLOOR_PX, int(gap_frac * height))
    deadzone = int(deadzone_frac * width)
    max_shift = int(max_shift_frac * width)
    min_ink_px = max(3, int(MIN_INK_ROW_FRAC * width))
    left_tol = int(LEFT_EDGE_TOL_FRAC * width)
    page_center = width / 2.0

    blocks = _segment(ink, gap_thresh, min_ink_px)
    if not blocks:
        img.save(out_path)
        return []

    # Over-segmentation -> treat the whole page as one block (plain global center).
    if len(blocks) > MAX_BLOCKS:
        blocks = [(blocks[0][0], blocks[-1][1])]

    extents = {b: _extent(ink, *b) for b in blocks}

    # Left-aligned group: >=3 consecutive blocks sharing a left edge (differing
    # centers) is a deliberate list/outline -- don't center those individually.
    skip_left_aligned: set[tuple[int, int]] = set()
    run: list[tuple[int, int]] = []

    def _flush(run: list[tuple[int, int]]) -> None:
        if len(run) >= 3:
            skip_left_aligned.update(run)

    for b in blocks:
        e = extents[b]
        if e is None:
            _flush(run)
            run = []
            continue
        prev_e = extents[run[-1]] if run else None
        if prev_e is not None and abs(e[0] - prev_e[0]) <= left_tol:
            run.append(b)
        else:
            _flush(run)
            run = [b]
    _flush(run)

    out = np.full_like(arr, 255)
    shifts: list[BlockShift] = []
    for b in blocks:
        r0, r1 = b
        e = extents[b]
        if e is None:
            continue
        left, right = e
        raw_shift = int(round(page_center - (left + right) / 2.0))
        block_w = right - left + 1
        block_off = abs((left + right) / 2.0 - page_center)

        skip = (
            b in skip_left_aligned
            or abs(raw_shift) < deadzone
            or abs(raw_shift) > max_shift
            or (
                block_w < MARGINALIA_MAX_WIDTH_FRAC * width
                and block_off > MARGINALIA_MIN_OFFSET_FRAC * width
            )
        )
        shift = 0 if skip else raw_shift
        if left + shift < 0:
            shift = -left
        if right + shift > width - 1:
            shift = (width - 1) - right

        src = arr[r0 : r1 + 1]
        if shift == 0:
            out[r0 : r1 + 1] = src
        elif shift > 0:
            out[r0 : r1 + 1, shift:] = src[:, : width - shift]
        else:
            out[r0 : r1 + 1, : width + shift] = src[:, -shift:]

        shifts.append(BlockShift((r0, r1), (left, right), raw_shift, shift))

    Image.fromarray(out).save(out_path)
    return shifts
