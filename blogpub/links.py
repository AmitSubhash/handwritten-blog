"""Detect handwritten URLs on a page image and their approximate positions."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

DEFAULT_MODEL = "claude-haiku-4-5-20251001"

_PROMPT_TEMPLATE = """Read the image at {image_path}. Look for any handwritten web \
addresses or URLs on this page (e.g. "example.com", "https://...", "arxiv.org/abs/1234").

For each one found, report its visible text, the full URL it points to (add \
"https://" if the handwriting omits it), and its approximate bounding box as \
fractions of the image width/height (0.0 to 1.0, origin at top-left).

Output ONLY a JSON array, no markdown fences, no commentary. Each element:
{{"text": "<exactly what's written>", "url": "<full url>", "bbox": [x0, y0, x1, y1]}}

If no handwritten URLs are visible, output exactly: []"""


@dataclass(frozen=True)
class LinkRegion:
    """A detected handwritten link on a page.

    Parameters
    ----------
    text : str
        The handwritten text as it appears on the page.
    url : str
        The URL it should link to.
    bbox : tuple of float
        ``(x0, y0, x1, y1)``, each a fraction of image width/height. Tint
        the ink blue via mix-blend-mode -- for manual links on non-text
        artwork (e.g. a hand-drawn logo), target just the label text next
        to it rather than the whole drawing, so only the text turns blue.
    """

    text: str
    url: str
    bbox: tuple[float, float, float, float]


def detect_links(png_path: Path, model: str = DEFAULT_MODEL) -> list[LinkRegion]:
    """Ask Claude to find handwritten URLs on a page image and their positions.

    Bounding-box precision from a vision model is approximate, not pixel-exact
    -- overlays built from this should be treated as a best-effort convenience,
    not a guarantee of perfect alignment.

    Parameters
    ----------
    png_path : Path
        Path to the rasterized page image.
    model : str, optional
        Claude model ID to use.

    Returns
    -------
    list of LinkRegion
        Empty if no handwritten URLs are found or the response can't be parsed.
    """
    prompt = _PROMPT_TEMPLATE.format(image_path=png_path.resolve())
    result = subprocess.run(
        ["claude", "-p", "--model", model, prompt],
        check=True,
        capture_output=True,
        text=True,
    )
    raw = result.stdout.strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        raw = raw[raw.find("[") : raw.rfind("]") + 1]

    try:
        entries = json.loads(raw)
    except json.JSONDecodeError:
        return []

    links = []
    for entry in entries:
        try:
            bbox = tuple(float(v) for v in entry["bbox"])
            if len(bbox) != 4:
                continue
            links.append(LinkRegion(text=entry["text"], url=entry["url"], bbox=bbox))
        except (KeyError, TypeError, ValueError):
            continue
    return links


def load_manual_links(path: Path) -> dict[str, dict[int, list[LinkRegion]]]:
    """Load manually-specified links for pages the auto-detector can't handle.

    Useful for things like hand-drawn icons with no literal URL text for
    the vision model to read. Expected JSON shape::

        {"<notebook-uuid>": {"<page-index>": [{"text", "url", "bbox"}, ...]}}

    Parameters
    ----------
    path : Path
        Path to the manual links JSON file.

    Returns
    -------
    dict
        Empty dict if the file doesn't exist. Otherwise maps notebook UUID
        to a dict of page index -> list of LinkRegion.
    """
    if not path.exists():
        return {}
    raw = json.loads(path.read_text())
    return {
        uuid: {
            int(page_idx): [
                LinkRegion(text=e["text"], url=e["url"], bbox=tuple(e["bbox"]))
                for e in entries
            ]
            for page_idx, entries in pages.items()
        }
        for uuid, pages in raw.items()
    }
