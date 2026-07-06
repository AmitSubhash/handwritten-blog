"""Convert reMarkable .rm vector pages to rasterized PNG images."""

from __future__ import annotations

import io
import json
import subprocess
from pathlib import Path

import numpy as np
from PIL import Image
from rmscene import read_tree

from .blocks import center_blocks
from .pull import PostInfo

PAGE_WIDTH = 1404
PAGE_HEIGHT = 1872

_palette_patched = False


def _patch_palette() -> None:
    """Fall back to black for pen colors outside rmc's built-in palette.

    See rmscribe's convert.py for the full explanation -- newer pen tools
    (e.g. ShadingMarker with a custom ARGB color) use color IDs rmc 0.3.0
    doesn't define, which otherwise aborts the whole page.
    """
    global _palette_patched
    if _palette_patched:
        return

    import rmc.exporters.writing_tools as writing_tools

    class _FallbackPalette(dict):
        def __missing__(self, _key: int) -> tuple[int, int, int]:
            return (0, 0, 0)

    writing_tools.RM_PALETTE = _FallbackPalette(writing_tools.RM_PALETTE)
    _palette_patched = True


def convert_page_to_png(rm_path: Path, out_png: Path) -> None:
    """Convert a single ``.rm`` page file to a PNG at native resolution.

    Parameters
    ----------
    rm_path : Path
        Path to the source ``.rm`` page file.
    out_png : Path
        Destination PNG path; parent directory is created if needed.
    """
    _patch_palette()
    from rmc.exporters.svg import tree_to_svg

    with open(rm_path, "rb") as f:
        tree = read_tree(f)

    svg_buffer = io.StringIO()
    tree_to_svg(tree, svg_buffer)

    out_png.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "rsvg-convert",
            "-w",
            str(PAGE_WIDTH),
            "-h",
            str(PAGE_HEIGHT),
            "-o",
            str(out_png),
        ],
        input=svg_buffer.getvalue().encode(),
        check=True,
    )
    # The reMarkable canvas is a fixed 1404x1872 regardless of how much of
    # the page is actually written on -- trim the blank paper so published
    # pages don't carry a large empty gap when a page isn't filled.
    subprocess.run(
        [
            "magick",
            str(out_png),
            "-trim",
            "+repage",
            "-bordercolor",
            "white",
            "-border",
            "40",
            # Strip metadata (incl. timestamps) so identical handwriting yields
            # byte-identical PNGs -- lets the vision cache key on content hash.
            "-strip",
            str(out_png),
        ],
        check=True,
    )
    # Center each content block (heading, paragraph, drawing, footer)
    # horizontally on its own, so off-center handwriting reads balanced.
    center_blocks(out_png, out_png)


def convert_post_pages(post: PostInfo, cache_dir: Path, out_dir: Path) -> list[Path]:
    """Convert every page of a post's notebook to PNGs, in reading order.

    Parameters
    ----------
    post : PostInfo
        The notebook to convert.
    cache_dir : Path
        Local cache directory containing the pulled notebook data.
    out_dir : Path
        Directory to write per-page PNGs into.

    Returns
    -------
    list of Path
        PNG paths in page order.
    """
    page_ids = first_page_ids(post, cache_dir)

    png_paths = []
    for i, page_id in enumerate(page_ids):
        rm_path = cache_dir / post.uuid / f"{page_id}.rm"
        out_png = out_dir / post.uuid / f"page_{i:03d}.png"
        if not rm_path.exists():
            continue
        convert_page_to_png(rm_path, out_png)
        png_paths.append(out_png)
    return png_paths


def first_page_ids(post: PostInfo, cache_dir: Path) -> list[str]:
    """Return a notebook's page ids in reading order.

    Parameters
    ----------
    post : PostInfo
        The notebook.
    cache_dir : Path
        Local cache directory containing the pulled ``.content`` file.

    Returns
    -------
    list of str
        Page ids in order.
    """
    content = json.loads((cache_dir / f"{post.uuid}.content").read_text())
    return [page["id"] for page in content["cPages"]["pages"]]


def wordmark_render_is_clean(png_path: Path, threshold: float = 0.25) -> bool:
    """Heuristic: does a rendered wordmark look like clean handwriting, not a
    filled blob?

    Some reMarkable pens (Calligraphy, thick brushes) don't convert cleanly
    through rmc -- their variable-width strokes render as solid jagged shapes.
    A filled shape survives a several-pixel erosion; thin handwriting mostly
    disappears. So the fraction of ink that survives erosion cleanly separates
    the two (measured: ~0.5 for a Calligraphy blob, ~0.08 for a Fineliner).

    Parameters
    ----------
    png_path : Path
        The rendered wordmark PNG.
    threshold : float, optional
        Survival ratio above which the render is treated as a blob.

    Returns
    -------
    bool
        True if the render looks like clean handwriting.
    """
    ink = float(
        subprocess.run(
            [
                "magick",
                str(png_path),
                "-threshold",
                "50%",
                "-format",
                "%[fx:1-mean]",
                "info:",
            ],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    )
    if ink <= 0:
        return False
    survived = float(
        subprocess.run(
            [
                "magick",
                str(png_path),
                "-threshold",
                "50%",
                "-negate",
                "-morphology",
                "Erode",
                "Disk:6",
                "-format",
                "%[fx:mean]",
                "info:",
            ],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    )
    return (survived / ink) < threshold


def thumbnail_to_wordmark(thumb_path: Path, out_png: Path) -> None:
    """Turn a device page thumbnail into a clean wordmark image.

    The device renders every pen correctly (unlike rmc), just at low
    resolution -- fine for a small header. Removes the page's faint ruled
    template lines by pushing near-white to pure white while keeping the
    ink's grayscale anti-aliasing, then trims tight and pads.

    Parameters
    ----------
    thumb_path : Path
        The device thumbnail PNG for the wordmark page.
    out_png : Path
        Destination path for the cleaned wordmark PNG.
    """
    out_png.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "magick",
            str(thumb_path),
            "-colorspace",
            "Gray",
            "-white-threshold",
            "72%",
            "-trim",
            "+repage",
            "-bordercolor",
            "white",
            "-border",
            "12",
            "-strip",
            str(out_png),
        ],
        check=True,
    )


def _ink_bbox(arr: np.ndarray, ink_threshold: int) -> tuple[int, int, int, int] | None:
    """Return the (left, top, right, bottom) bounding box of ink in a grayscale
    array, or None if there is no ink. ``right``/``bottom`` are exclusive."""
    ink = arr < ink_threshold
    rows = np.any(ink, axis=1)
    cols = np.any(ink, axis=0)
    if not rows.any():
        return None
    row_idx = np.flatnonzero(rows)
    col_idx = np.flatnonzero(cols)
    return int(col_idx[0]), int(row_idx[0]), int(col_idx[-1] + 1), int(row_idx[-1] + 1)


def normalize_icon_pair(
    paths: list[Path], *, ink_threshold: int = 200, margin_frac: float = 0.14
) -> None:
    """Make a set of small ink icons render at a matched size.

    Toggle icons are drawn by hand at whatever scale is convenient, so a sun
    and a moon can arrive very differently sized. This trims each to its ink,
    scales every icon so its longest ink dimension matches the largest icon's,
    then centers each in an identical square canvas. The result: both icons
    share the same footprint and the same visual weight, so the corner button
    is the same size in either toggle state and neither icon looks bigger.

    Modifies the files in place.

    Parameters
    ----------
    paths : list of Path
        Icon PNGs to normalize together (e.g. the sun and the moon).
    ink_threshold : int, optional
        Grayscale value below which a pixel counts as ink (0-255).
    margin_frac : float, optional
        White padding around the ink, as a fraction of the matched ink size.
    """
    cropped: list[Image.Image | None] = []
    for path in paths:
        arr = np.asarray(Image.open(path).convert("L"))
        bbox = _ink_bbox(arr, ink_threshold)
        cropped.append(
            Image.fromarray(arr[bbox[1] : bbox[3], bbox[0] : bbox[2]]) if bbox else None
        )

    reaches = [max(img.size) for img in cropped if img is not None]
    if not reaches:
        return
    target = max(reaches)
    side = int(round(target * (1 + 2 * margin_frac)))

    for img, path in zip(cropped, paths):
        if img is None:
            continue
        scale = target / max(img.size)
        width = max(1, round(img.width * scale))
        height = max(1, round(img.height * scale))
        resized = img.resize((width, height), Image.Resampling.LANCZOS)
        canvas = Image.new("L", (side, side), 255)
        canvas.paste(resized, ((side - width) // 2, (side - height) // 2))
        canvas.save(path)
