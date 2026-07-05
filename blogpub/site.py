"""Render posts and an index into a static site for GitHub Pages."""

from __future__ import annotations

import html
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

from .links import LinkRegion
from .pull import PostInfo

ABOUT_NAMES = {"about", "hi"}

STYLE = """
body {
    max-width: 720px;
    margin: 3rem auto;
    padding: 0 1.5rem;
    font-family: Georgia, "Times New Roman", serif;
    color: #111;
    background: #fdfdfb;
    line-height: 1.5;
}
h1, h2 { font-weight: normal; }
a { color: #111; }
.post-date { color: #666; font-size: 0.9rem; }
.about-section { margin-bottom: 3rem; }
.page-wrap {
    position: relative;
    margin: 1.5rem 0;
}
.page-image {
    width: 100%;
    border: 1px solid #ddd;
    display: block;
}
.link-overlay {
    position: absolute;
    display: block;
    background-color: #35c;
    mix-blend-mode: screen;
}
.link-icon-hitbox {
    position: absolute;
    display: block;
}
.link-icon-underline {
    position: absolute;
    display: block;
    height: 3px;
    border-radius: 2px;
    background-color: #35c;
    pointer-events: none;
}
ul.post-list { list-style: none; padding: 0; }
ul.post-list li { margin-bottom: 1.2rem; }
footer { margin-top: 4rem; color: #999; font-size: 0.85rem; }
"""


def slugify(name: str) -> str:
    """Turn a notebook name into a URL-safe slug fragment.

    Parameters
    ----------
    name : str
        Notebook name.

    Returns
    -------
    str
        Lowercase, hyphenated slug fragment (not guaranteed unique on its
        own -- see :func:`post_slug`).
    """
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "untitled"


def post_slug(post: PostInfo) -> str:
    """Build a unique, stable slug for a post's output filename.

    Includes a short UUID fragment so two notebooks with the same (or
    equivalent-once-slugified) name don't overwrite each other's output.

    Parameters
    ----------
    post : PostInfo
        The post to build a slug for.

    Returns
    -------
    str
        A unique slug, e.g. ``"my-notebook-a1b2c3d4"``.
    """
    return f"{slugify(post.name)}-{post.uuid[:8]}"


def is_about_page(post: PostInfo) -> bool:
    """Check whether a post should render as the site's About/intro section.

    A notebook named "About" or "Hi" (case-insensitive) is treated as the
    site's intro: shown inline at the top of the index page instead of in
    the chronological post list.

    Parameters
    ----------
    post : PostInfo
        The post to check.

    Returns
    -------
    bool
        True if this post is the About/intro page.
    """
    return post.name.strip().lower() in ABOUT_NAMES


def _format_date(created_time_ms: str) -> str:
    try:
        dt = datetime.fromtimestamp(int(created_time_ms) / 1000, tz=timezone.utc)
        return dt.strftime("%B %-d, %Y")
    except (ValueError, OSError):
        return ""


def _render_link(link: LinkRegion) -> str:
    """Render one link as HTML, styled according to its ``kind``.

    Parameters
    ----------
    link : LinkRegion
        The link to render.

    Returns
    -------
    str
        HTML for this link (one or two elements, depending on kind).
    """
    x0, y0, x1, y1 = (v * 100 for v in link.bbox)
    href = html.escape(link.url)
    title = html.escape(link.text)

    if link.kind == "icon":
        # Full-size invisible hitbox (generous click target) plus a thin
        # underline beneath it, so the artwork itself isn't recolored.
        return (
            f'<a class="link-icon-hitbox" href="{href}" title="{title}" '
            f'style="left:{x0:.2f}%;top:{y0:.2f}%;width:{x1 - x0:.2f}%;'
            f'height:{y1 - y0:.2f}%"></a>\n'
            f'<div class="link-icon-underline" '
            f'style="left:{x0:.2f}%;top:{y1:.2f}%;width:{x1 - x0:.2f}%"></div>'
        )

    return (
        f'<a class="link-overlay" href="{href}" title="{title}" '
        f'style="left:{x0:.2f}%;top:{y0:.2f}%;width:{x1 - x0:.2f}%;'
        f'height:{y1 - y0:.2f}%"></a>'
    )


def _render_page(image_path: str, page_num: int, links: list[LinkRegion]) -> str:
    """Render one page's image wrapped with any detected link overlays.

    Parameters
    ----------
    image_path : str
        Relative path (from the HTML file referencing it) to this page's PNG.
    page_num : int
        1-indexed page number, used for the image's alt text.
    links : list of LinkRegion
        Handwritten links detected on this page (bounding boxes are
        approximate -- a vision model's estimate, not pixel-exact).

    Returns
    -------
    str
        HTML for this page, including any clickable overlays.
    """
    overlays = "\n".join(_render_link(link) for link in links)
    return (
        f'<div class="page-wrap">\n'
        f'<img class="page-image" src="{image_path}" alt="Page {page_num}">\n'
        f"{overlays}\n</div>"
    )


def _render_pages(
    page_image_paths: list[str], page_links: list[list[LinkRegion]]
) -> str:
    return "\n".join(
        _render_page(path, i + 1, links)
        for i, (path, links) in enumerate(zip(page_image_paths, page_links))
    )


def render_post(
    post: PostInfo,
    page_image_paths: list[str],
    page_links: list[list[LinkRegion]] | None = None,
) -> str:
    """Render a single post's HTML page.

    Parameters
    ----------
    post : PostInfo
        The notebook being rendered.
    page_image_paths : list of str
        Relative (from the post HTML file) paths to each page's PNG image.
    page_links : list of (list of LinkRegion), optional
        Detected handwritten links per page, same order as
        ``page_image_paths``. Pages with no detected links can use an empty
        list. Defaults to no links on any page.

    Returns
    -------
    str
        Full HTML document for this post.
    """
    title = html.escape(post.name)
    if page_links is None:
        page_links = [[] for _ in page_image_paths]
    images_html = _render_pages(page_image_paths, page_links)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{title}</title>
<style>{STYLE}</style>
</head>
<body>
<p><a href="../index.html">&larr; back</a></p>
<h1>{title}</h1>
<p class="post-date">{_format_date(post.created_time)}</p>
{images_html}
<footer>Written by hand, published from a reMarkable.</footer>
</body>
</html>
"""


def render_index(posts: list[PostInfo], about_pages_html: str = "") -> str:
    """Render the site index listing all posts, newest first.

    Parameters
    ----------
    posts : list of PostInfo
        All published posts (excluding the About/intro page, if any).
    about_pages_html : str, optional
        Pre-rendered HTML for the About/intro page's images, shown above
        the post list. Empty string if there is no About page.

    Returns
    -------
    str
        Full HTML document for the index page.
    """
    ordered = sorted(posts, key=lambda p: p.created_time, reverse=True)
    items = "\n".join(
        f'<li><a href="posts/{post_slug(p)}.html">{html.escape(p.name)}</a> '
        f'<span class="post-date">{_format_date(p.created_time)}</span></li>'
        for p in ordered
    )
    about_section = (
        f'<div class="about-section">{about_pages_html}</div>'
        if about_pages_html
        else ""
    )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Marginalia</title>
<style>{STYLE}</style>
</head>
<body>
{about_section}
<ul class="post-list">
{items}
</ul>
<footer>Written by hand, published from a reMarkable.</footer>
</body>
</html>
"""


def write_site(
    posts_with_pages: list[tuple[PostInfo, list[Path], list[list[LinkRegion]]]],
    docs_dir: Path,
) -> None:
    """Write the full static site (index + posts + images) into a docs directory.

    A post named "About" or "Hi" (see :func:`is_about_page`) is rendered
    inline at the top of the index page instead of as a regular
    chronological post entry.

    Replaces the entire ``posts/`` and ``images/`` subdirectories on each
    run, so notebooks removed from the Blog folder don't linger as stale
    published pages.

    Parameters
    ----------
    posts_with_pages : list of (PostInfo, list of Path, list of list of LinkRegion)
        Each post paired with its ordered page PNG paths and per-page
        detected links.
    docs_dir : Path
        Output directory (e.g. a repo's ``docs/`` folder for GitHub Pages).
    """
    posts_dir = docs_dir / "posts"
    images_dir = docs_dir / "images"
    shutil.rmtree(posts_dir, ignore_errors=True)
    shutil.rmtree(images_dir, ignore_errors=True)
    posts_dir.mkdir(parents=True, exist_ok=True)
    images_dir.mkdir(parents=True, exist_ok=True)

    regular_posts = []
    about_html = ""

    for post, page_paths, page_links in posts_with_pages:
        slug = post_slug(post)
        post_image_dir = images_dir / slug
        post_image_dir.mkdir(parents=True, exist_ok=True)

        for i, src in enumerate(page_paths):
            dest = post_image_dir / f"page_{i:03d}.png"
            dest.write_bytes(src.read_bytes())

        if is_about_page(post):
            about_paths = [
                f"images/{slug}/page_{i:03d}.png" for i in range(len(page_paths))
            ]
            about_html = _render_pages(about_paths, page_links)
            continue

        relative_paths = [
            f"../images/{slug}/page_{i:03d}.png" for i in range(len(page_paths))
        ]
        (posts_dir / f"{slug}.html").write_text(
            render_post(post, relative_paths, page_links)
        )
        regular_posts.append(post)

    (docs_dir / "index.html").write_text(render_index(regular_posts, about_html))
