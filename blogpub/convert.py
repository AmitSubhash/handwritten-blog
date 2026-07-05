"""Convert reMarkable .rm vector pages to rasterized PNG images."""

from __future__ import annotations

import io
import json
import subprocess
from pathlib import Path

from rmscene import read_tree

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
            str(out_png),
        ],
        check=True,
    )


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
    content = json.loads((cache_dir / f"{post.uuid}.content").read_text())
    page_ids = [page["id"] for page in content["cPages"]["pages"]]

    png_paths = []
    for i, page_id in enumerate(page_ids):
        rm_path = cache_dir / post.uuid / f"{page_id}.rm"
        out_png = out_dir / post.uuid / f"page_{i:03d}.png"
        if not rm_path.exists():
            continue
        convert_page_to_png(rm_path, out_png)
        png_paths.append(out_png)
    return png_paths
