"""Analyze a page image via Claude: alt text plus any handwritten URLs."""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

DEFAULT_MODEL = "claude-haiku-4-5-20251001"

_PROMPT_TEMPLATE = """Read the image at {image_path}. This is a handwritten notebook page.

1. Write a concise one or two sentence description of what's written/drawn on \
this page, suitable as alt text for screen readers and search engines. \
Describe the actual content, not "a handwritten page".
2. Look for any handwritten web addresses or URLs (e.g. "example.com", \
"https://...", "arxiv.org/abs/1234"). For each one found, report its visible \
text, the full URL it points to (add "https://" if the handwriting omits it), \
and its approximate bounding box as fractions of the image width/height \
(0.0 to 1.0, origin at top-left).

Output ONLY JSON, no markdown fences, no commentary:
{{"alt_text": "<description>", "links": [{{"text": "...", "url": "...", "bbox": [x0, y0, x1, y1]}}]}}

If no handwritten URLs are visible, use an empty array for "links"."""


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


@dataclass(frozen=True)
class PageAnalysis:
    """Combined vision-model analysis of a single page image.

    Parameters
    ----------
    alt_text : str
        A short description of the page's content, for accessibility/SEO.
    links : list of LinkRegion
        Any handwritten URLs detected on the page.
    """

    alt_text: str
    links: list[LinkRegion]


def analyze_page(png_path: Path, model: str = DEFAULT_MODEL) -> PageAnalysis:
    """Ask Claude for alt text and any handwritten links, in one call.

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
    PageAnalysis
        Falls back to a generic alt text and no links if the response can't
        be parsed.
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
        raw = raw[raw.find("{") : raw.rfind("}") + 1]

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        print(
            f"  warning: vision response for {png_path.name} wasn't valid JSON, "
            f"falling back to generic alt text: {raw[:200]!r}",
            file=sys.stderr,
        )
        return PageAnalysis(alt_text="A handwritten notebook page.", links=[])

    links = []
    for entry in parsed.get("links", []):
        try:
            bbox = tuple(float(v) for v in entry["bbox"])
            if len(bbox) != 4:
                continue
            links.append(LinkRegion(text=entry["text"], url=entry["url"], bbox=bbox))
        except (KeyError, TypeError, ValueError):
            continue

    alt_text = parsed.get("alt_text") or "A handwritten notebook page."
    return PageAnalysis(alt_text=alt_text, links=links)


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
