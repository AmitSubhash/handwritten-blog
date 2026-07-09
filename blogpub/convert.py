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
    """Convert a single ``.rm`` page file to a PNG at the page's native width.

    Renders to a fixed *width* and lets the height follow the page's real
    aspect ratio. reMarkable pages are a fixed width but a *variable* height --
    an extended / scrolling page can be several times taller than a standard
    one -- so forcing a fixed height (the old ``-h``) squashed tall pages
    vertically and stretched them horizontally into an unreadable smear.

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
        ["rsvg-convert", "-w", str(PAGE_WIDTH), "-o", str(out_png)],
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


def wordmark_render_is_clean(
    png_path: Path, survive_threshold: float = 0.25, fill_threshold: float = 0.45
) -> bool:
    """Heuristic: does a rendered drawing look like clean line art, not a
    filled blob?

    Some reMarkable pens (Calligraphy, thick brushes) don't convert cleanly
    through rmc -- their variable-width strokes render as solid jagged shapes.
    Two signals separate a solid blob from detailed line art:

    * **erosion survival** -- a filled shape survives a several-pixel erosion;
      thin handwriting mostly disappears (~0.5 for a Calligraphy blob, ~0.08
      for a Fineliner).
    * **bounding-box fill** -- a filled blob inks most of its bounding box
      (~0.58 measured); detailed line art, even with *thick* strokes (e.g. a
      rayed sun with an open ring), inks only a small fraction (~0.25).

    Survival alone misfires on thick-but-detailed line art, whose chunky
    strokes survive erosion just like a fill. Requiring a blob to *both*
    survive erosion *and* fill its box lets that detailed art through while
    still catching genuine calligraphy blobs.

    Parameters
    ----------
    png_path : Path
        The rendered PNG.
    survive_threshold : float, optional
        Erosion-survival ratio above which a render may be a blob.
    fill_threshold : float, optional
        Bounding-box ink fraction above which a surviving render is a blob.

    Returns
    -------
    bool
        True if the render looks like clean line art (use it directly).
    """

    def _measure(*ops: str) -> float:
        return float(
            subprocess.run(
                ["magick", str(png_path), *ops, "info:"],
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
        )

    ink = _measure("-threshold", "50%", "-format", "%[fx:1-mean]")
    if ink <= 0:
        return False
    survived = _measure(
        "-threshold", "50%", "-negate", "-morphology", "Erode", "Disk:6",
        "-format", "%[fx:mean]",
    )
    fill = _measure("-threshold", "50%", "-trim", "+repage", "-format", "%[fx:1-mean]")
    is_blob = (survived / ink) >= survive_threshold and fill >= fill_threshold
    return not is_blob


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


def _main_circle(
    arr: np.ndarray, ink_threshold: int, n_r: int = 70
) -> tuple[float, float, float, float] | None:
    """Locate an icon's dominant circle: the sun's ring, the moon's outline.

    Works in the icon's bounding-box frame (its center is stable, unlike the
    ink centroid, which a lopsided feature such as edge shading can drag off).
    Bins ink by radius and takes the peak of *circumferential density* (ink per
    unit circumference, ``count / r``): a thick continuous circle spikes there,
    while sparse rays or interior stipple do not. That gives the sun's ring
    (rays sit at larger radii, lower density) and the moon's outline (interior
    stipple sits at smaller radii).

    Returns ``(cx, cy, circle_radius, max_radius)`` or None if there's no ink.
    """
    ink = arr < ink_threshold
    ys, xs = np.nonzero(ink)
    if ys.size == 0:
        return None
    cx = (xs.min() + xs.max()) / 2.0
    cy = (ys.min() + ys.max()) / 2.0
    radii = np.sqrt((ys - cy) ** 2 + (xs - cx) ** 2)
    max_radius = float(radii.max())
    counts, edges = np.histogram(radii, bins=n_r, range=(0.0, max_radius))
    centers = (edges[:-1] + edges[1:]) / 2.0
    density = counts / np.maximum(centers, 1.0)
    lo = int(0.15 * n_r)  # skip the cluttered core
    peak = lo + int(np.argmax(density[lo:]))
    return cx, cy, float(centers[peak]), max_radius


def normalize_icon_pair(
    paths: list[Path], *, ink_threshold: int = 128, margin_frac: float = 0.06
) -> None:
    """Align a set of icons on their dominant circle and pad to one canvas.

    Toggle icons are drawn by hand: a sun (an inner ring with rays reaching
    past it) and a moon (an outline disc) arrive at different scales, and their
    *main circles* are at different fractions of their overall size. Matching
    bounding boxes would leave the sun's ring smaller than the moon's disc.

    Instead this finds each icon's main circle (see :func:`_main_circle`),
    scales every icon so those circles share one radius, and centers each on
    its circle in an identical square canvas. So the sun's ring and the moon's
    disc come out the same diameter and concentric: toggling holds that circle
    in place while the sun's rays (or the moon's craters) swap around it. The
    canvas is sized to the widest icon (the sun, including its rays), so the
    moon simply sits with more space around its disc.

    Modifies the files in place.

    Parameters
    ----------
    paths : list of Path
        Icon PNGs to normalize together (e.g. the sun and the moon).
    ink_threshold : int, optional
        Grayscale value below which a pixel counts as ink (0-255).
    margin_frac : float, optional
        White padding around the widest icon, as a fraction of its extent.
    """
    entries = []
    for path in paths:
        img = Image.open(path).convert("L")
        circle = _main_circle(np.asarray(img), ink_threshold)
        entries.append((path, img, circle))

    circles = [c for _, _, c in entries if c is not None]
    if not circles:
        return
    target_radius = max(circle_radius for *_, circle_radius, _ in circles)
    half = max(
        max_radius * (target_radius / circle_radius)
        for *_, circle_radius, max_radius in circles
    )
    side = int(round(2.0 * half * (1.0 + margin_frac)))

    for path, img, circle in entries:
        if circle is None:
            continue
        cx, cy, circle_radius, _ = circle
        scale = target_radius / circle_radius
        resized = img.resize(
            (max(1, round(img.width * scale)), max(1, round(img.height * scale))),
            Image.Resampling.LANCZOS,
        )
        canvas = Image.new("L", (side, side), 255)
        offset_x = int(round(side / 2.0 - cx * scale))
        offset_y = int(round(side / 2.0 - cy * scale))
        canvas.paste(resized, (offset_x, offset_y))
        canvas.save(path)
