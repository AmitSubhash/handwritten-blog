"""Render posts and an index into a static site for GitHub Pages."""

from __future__ import annotations

import html
import re
import shutil
import struct
from datetime import datetime, timezone
from pathlib import Path

from .links import LinkRegion
from .pull import PostInfo

ABOUT_NAMES = {"about", "hi"}
WORDMARK_NAMES = {"wordmark", "title"}
SUN_NAMES = {"sun"}
MOON_NAMES = {"moon"}
SITE_URL = "https://amitsubhash.github.io/marginalia"
SITE_DESCRIPTION = (
    "A blog written by hand on a reMarkable, published straight from the ink."
)
FAVICON = (
    "data:image/svg+xml,"
    "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'>"
    "<text y='.9em' font-size='90'>%E2%9C%8E</text></svg>"
)

STYLE = """
/* Theme is driven by CSS variables so a hand-drawn sun/moon toggle can
   override the system preference. Light values are the defaults; dark values
   apply either when the OS asks for dark (and the reader hasn't chosen light)
   or when the reader explicitly picks dark via the toggle. */
:root {
    --bg: #fff;
    --fg: #111;
    --fg-muted: #aaa;
    --heading: #666;
    --link-hover: #35c;
    --page-filter: none;
}
@media (prefers-color-scheme: dark) {
    :root:not([data-theme="light"]) {
        --bg: #000;
        --fg: #ddd;
        --fg-muted: #888;
        --heading: #999;
        --link-hover: #7aa5f0;
        --page-filter: invert(1) hue-rotate(180deg);
    }
}
:root[data-theme="dark"] {
    --bg: #000;
    --fg: #ddd;
    --fg-muted: #888;
    --heading: #999;
    --link-hover: #7aa5f0;
    --page-filter: invert(1) hue-rotate(180deg);
}
* { box-sizing: border-box; }
body {
    max-width: 1100px;
    margin: 3rem auto;
    padding: 0 1.5rem;
    font-family: Georgia, "Times New Roman", serif;
    color: var(--fg);
    background: var(--bg);
    line-height: 1.5;
}
h1, h2 { font-weight: normal; }
h1 { font-style: italic; text-transform: lowercase; }
a { color: var(--fg); text-decoration-color: #35c; text-decoration-thickness: 1.5px; text-underline-offset: 3px; }
a:hover { color: var(--link-hover); }
.site-title { text-align: center; margin: 1.5rem 0 3rem; }
.site-title a {
    font-style: italic;
    font-size: 1.6rem;
    letter-spacing: 0.02em;
    text-decoration: none;
    color: var(--fg);
    display: inline-block;
}
/* Size the handwritten wordmark by height (a single word); clamp so it never
   becomes the biggest thing on a phone as the ink pages scale down. */
.wordmark {
    height: clamp(2.6rem, 7vw, 4rem);
    width: auto;
    display: block;
    margin: 0 auto;
    filter: var(--page-filter);
}
.site-title a:hover .wordmark { opacity: 0.55; }
/* Dates are the only typeset text left; keep them quiet, like the printed
   page numbers in a real notebook rather than body copy. */
.post-date {
    color: var(--fg-muted);
    font-size: 0.72rem;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    font-variant-numeric: tabular-nums;
}
.about-section { margin-bottom: 3rem; }
/* The handwriting sits directly on the page: the scanned paper is pure white,
   the page is pure white, and there is no border or shadow -- so the ink
   reads as part of the page itself rather than a photo of a separate sheet. */
.page-wrap {
    position: relative;
    margin: 0.5rem 0;
    width: 100%;
    filter: var(--page-filter);
}
.page-image {
    width: 100%;
    height: auto;
    display: block;
}
.link-overlay {
    position: absolute;
    display: block;
    background-color: #35c;
    mix-blend-mode: screen;
    transition: opacity 0.12s;
}
.link-overlay:hover { opacity: 0.55; }
.link-overlay:focus-visible { outline: 2px solid #35c; outline-offset: 2px; }
h2.posts-heading {
    font-style: italic;
    font-weight: normal;
    color: var(--heading);
    margin-top: 2rem;
}
ul.post-list { list-style: none; padding: 0; }
ul.post-list li {
    display: flex;
    gap: 1rem;
    align-items: baseline;
    margin-bottom: 0.8rem;
}
ul.post-list .post-date { min-width: 7.5rem; }

/* Hand-drawn light/dark toggle: a quiet corner button holding both the sun and
   the moon. The icon shows the *current* mode -- the sun in light mode, the
   moon in dark mode -- and clicking it switches. It inverts with the page so
   the ink reads correctly against either background. */
.theme-toggle {
    position: fixed;
    top: 1rem;
    right: 1rem;
    z-index: 10;
    background: none;
    border: none;
    padding: 0.3rem;
    margin: 0;
    cursor: pointer;
    line-height: 0;
}
.theme-toggle img {
    /* Big enough that the hand-drawn detail (sun rays, moon craters) reads at a
       glance and the button is a comfortable tap target on mobile. */
    height: 2.7rem;
    width: auto;
    display: block;
    filter: var(--page-filter);
}
.theme-toggle .icon-moon { display: none; }
.theme-toggle .icon-sun { display: block; }
@media (prefers-color-scheme: dark) {
    :root:not([data-theme="light"]) .theme-toggle .icon-sun { display: none; }
    :root:not([data-theme="light"]) .theme-toggle .icon-moon { display: block; }
}
:root[data-theme="dark"] .theme-toggle .icon-sun { display: none; }
:root[data-theme="dark"] .theme-toggle .icon-moon { display: block; }
:root[data-theme="light"] .theme-toggle .icon-sun { display: block; }
:root[data-theme="light"] .theme-toggle .icon-moon { display: none; }
"""

# Runs in <head> before the body paints, so an explicit light/dark choice is
# applied with no flash of the wrong theme. Kept dependency-free and tiny.
THEME_BOOT_SCRIPT = """<script>
(function () {
  var root = document.documentElement;
  try {
    var saved = localStorage.getItem('theme');
    if (saved === 'light' || saved === 'dark') root.setAttribute('data-theme', saved);
  } catch (e) {}
  window.__toggleTheme = function () {
    var systemDark = window.matchMedia
      && window.matchMedia('(prefers-color-scheme: dark)').matches;
    var current = root.getAttribute('data-theme') || (systemDark ? 'dark' : 'light');
    var next = current === 'dark' ? 'light' : 'dark';
    root.setAttribute('data-theme', next);
    try { localStorage.setItem('theme', next); } catch (e) {}
  };
})();
</script>"""


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


def is_wordmark(post: PostInfo) -> bool:
    """Check whether a notebook is the handwritten site wordmark.

    A notebook named "wordmark" or "title" (case-insensitive) is used as
    the site's header image (Amit's own handwriting of "marginalia")
    instead of the typeset fallback, and is not published as a post.

    Parameters
    ----------
    post : PostInfo
        The post to check.

    Returns
    -------
    bool
        True if this notebook is the site wordmark.
    """
    return post.name.strip().lower() in WORDMARK_NAMES


def is_sun(post: PostInfo) -> bool:
    """Check whether a notebook is the hand-drawn "sun" light/dark toggle icon.

    A notebook named "sun" (case-insensitive) supplies the light-mode half of
    the theme toggle and is not published as a post.

    Parameters
    ----------
    post : PostInfo
        The post to check.

    Returns
    -------
    bool
        True if this notebook is the sun toggle icon.
    """
    return post.name.strip().lower() in SUN_NAMES


def is_moon(post: PostInfo) -> bool:
    """Check whether a notebook is the hand-drawn "moon" light/dark toggle icon.

    A notebook named "moon" (case-insensitive) supplies the dark-mode half of
    the theme toggle and is not published as a post.

    Parameters
    ----------
    post : PostInfo
        The post to check.

    Returns
    -------
    bool
        True if this notebook is the moon toggle icon.
    """
    return post.name.strip().lower() in MOON_NAMES


def _theme_toggle_html(sun_src: str | None, moon_src: str | None) -> str:
    """Render the hand-drawn light/dark toggle button, if both icons exist.

    The button holds both icons; CSS shows only the one for the mode the
    reader would switch to (moon in light, sun in dark). Returns an empty
    string when either icon is missing, so the site falls back to following
    the system preference with no toggle and no JavaScript.

    Parameters
    ----------
    sun_src : str or None
        Relative path to the sun icon from the page being rendered.
    moon_src : str or None
        Relative path to the moon icon from the page being rendered.

    Returns
    -------
    str
        The toggle ``<button>`` HTML, or an empty string.
    """
    if not (sun_src and moon_src):
        return ""
    return (
        '<button class="theme-toggle" type="button" onclick="__toggleTheme()" '
        'aria-label="Switch between light and dark mode" '
        'title="Switch between light and dark mode">'
        f'<img class="icon-sun" src="{sun_src}" alt="Switch to dark mode">'
        f'<img class="icon-moon" src="{moon_src}" alt="Switch to light mode">'
        "</button>"
    )


def _site_header(home_href: str, wordmark_src: str | None) -> str:
    """Render the site header -- the handwritten wordmark image if available,
    otherwise the typeset "marginalia" fallback.

    Parameters
    ----------
    home_href : str
        Relative link to the index page from the page being rendered.
    wordmark_src : str or None
        Relative path to the wordmark image, or None to use the typeset name.

    Returns
    -------
    str
        The ``<header>`` HTML.
    """
    if wordmark_src:
        inner = f'<img class="wordmark" src="{wordmark_src}" alt="marginalia">'
    else:
        inner = "marginalia"
    return f'<header class="site-title"><a href="{home_href}">{inner}</a></header>'


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
        return dt.strftime("%-d %b %Y")
    except (ValueError, OSError):
        return ""


def _png_dimensions(path: Path) -> tuple[int, int]:
    """Read a PNG's width/height straight from its IHDR chunk, no dependency needed.

    Parameters
    ----------
    path : Path
        Path to a PNG file.

    Returns
    -------
    tuple of int
        ``(width, height)``.
    """
    with open(path, "rb") as f:
        f.seek(16)
        width, height = struct.unpack(">II", f.read(8))
    return width, height


def _render_page(
    image_path: str,
    dims: tuple[int, int],
    alt_text: str,
    links: list[LinkRegion],
    eager: bool,
) -> str:
    """Render one page's image wrapped with any detected link overlays.

    Parameters
    ----------
    image_path : str
        Relative path (from the HTML file referencing it) to this page's PNG.
    dims : tuple of int
        ``(width, height)`` of the image, to prevent layout shift while it loads.
    alt_text : str
        Vision-model-generated description of the page, for accessibility/SEO.
    links : list of LinkRegion
        Handwritten links detected on this page (bounding boxes are
        approximate -- a vision model's estimate, not pixel-exact).
    eager : bool
        Load this image eagerly (first page of a post) rather than lazily.

    Returns
    -------
    str
        HTML for this page, including any clickable overlays.
    """
    overlays = "\n".join(
        f'<a class="link-overlay" href="{html.escape(link.url)}" '
        f'title="{html.escape(link.text)}" '
        f'style="left:{link.bbox[0] * 100:.2f}%;top:{link.bbox[1] * 100:.2f}%;'
        f"width:{(link.bbox[2] - link.bbox[0]) * 100:.2f}%;"
        f'height:{(link.bbox[3] - link.bbox[1]) * 100:.2f}%"></a>'
        for link in links
    )
    loading = "eager" if eager else "lazy"
    return (
        f'<div class="page-wrap">\n'
        f'<img class="page-image" src="{image_path}" alt="{html.escape(alt_text)}" '
        f'width="{dims[0]}" height="{dims[1]}" loading="{loading}" decoding="async">\n'
        f"{overlays}\n</div>"
    )


def _render_pages(
    page_image_paths: list[str],
    page_dims: list[tuple[int, int]],
    page_alt_text: list[str],
    page_links: list[list[LinkRegion]],
) -> str:
    return "\n".join(
        _render_page(path, dims, alt_text, links, eager=(i == 0))
        for i, (path, dims, alt_text, links) in enumerate(
            zip(page_image_paths, page_dims, page_alt_text, page_links)
        )
    )


def _head(
    title: str, description: str, og_image: str | None, head_extra: str = ""
) -> str:
    og_image_tag = (
        f'<meta property="og:image" content="{html.escape(og_image)}">'
        if og_image
        else ""
    )
    return f"""<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="icon" href="{FAVICON}">
<title>{html.escape(title)}</title>
<meta name="description" content="{html.escape(description)}">
<meta property="og:title" content="{html.escape(title)}">
<meta property="og:description" content="{html.escape(description)}">
<meta property="og:type" content="article">
{og_image_tag}
<meta name="twitter:card" content="summary_large_image">
<style>{STYLE}</style>{head_extra}"""


def render_post(
    post: PostInfo,
    page_image_paths: list[str],
    page_dims: list[tuple[int, int]],
    page_alt_text: list[str],
    page_links: list[list[LinkRegion]] | None = None,
    wordmark_src: str | None = None,
    sun_src: str | None = None,
    moon_src: str | None = None,
) -> str:
    """Render a single post's HTML page.

    Parameters
    ----------
    post : PostInfo
        The notebook being rendered.
    page_image_paths : list of str
        Relative (from the post HTML file) paths to each page's PNG image.
    page_dims : list of (int, int)
        Pixel dimensions of each page image, same order as ``page_image_paths``.
    page_alt_text : list of str
        Vision-model alt text for each page, same order as ``page_image_paths``.
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
    images_html = _render_pages(page_image_paths, page_dims, page_alt_text, page_links)
    og_image = (
        f"{SITE_URL}/{page_image_paths[0].removeprefix('../')}"
        if page_image_paths
        else None
    )
    toggle = _theme_toggle_html(sun_src, moon_src)
    head_extra = THEME_BOOT_SCRIPT if toggle else ""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
{_head(post.name, page_alt_text[0] if page_alt_text else SITE_DESCRIPTION, og_image, head_extra)}
</head>
<body>
{toggle}
{_site_header("../index.html", wordmark_src)}
<h1>{title}</h1>
<p class="post-date">{_format_date(post.created_time)}</p>
{images_html}
</body>
</html>
"""


def render_index(
    posts: list[PostInfo],
    about_pages_html: str = "",
    about_og_image: str | None = None,
    wordmark_src: str | None = None,
    sun_src: str | None = None,
    moon_src: str | None = None,
) -> str:
    """Render the site index listing all posts, newest first.

    Parameters
    ----------
    posts : list of PostInfo
        All published posts (excluding the About/intro page, if any).
    about_pages_html : str, optional
        Pre-rendered HTML for the About/intro page's images, shown above
        the post list. Empty string if there is no About page.
    about_og_image : str, optional
        Absolute URL to the About page's first image, used as the site's
        social-share preview image if present.

    Returns
    -------
    str
        Full HTML document for the index page.
    """
    ordered = sorted(posts, key=lambda p: p.created_time, reverse=True)
    if ordered:
        items = "\n".join(
            f'<li><span class="post-date">{_format_date(p.created_time)}</span>'
            f'<a href="posts/{post_slug(p)}.html">{html.escape(p.name)}</a></li>'
            for p in ordered
        )
        list_section = f'<h2 class="posts-heading">posts</h2>\n<ul class="post-list">\n{items}\n</ul>'
    else:
        list_section = ""

    about_section = (
        f'<div class="about-section">{about_pages_html}</div>'
        if about_pages_html
        else ""
    )
    # The main page carries no wordmark header of its own -- it's the intro.
    # A wordmark, if present, is only shown on individual post pages.
    header = _site_header("index.html", wordmark_src) if wordmark_src else ""
    toggle = _theme_toggle_html(sun_src, moon_src)
    head_extra = THEME_BOOT_SCRIPT if toggle else ""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
{_head("marginalia", SITE_DESCRIPTION, about_og_image, head_extra)}
</head>
<body>
{toggle}
{header}
{about_section}
{list_section}
</body>
</html>
"""


def write_site(
    posts_with_pages: list[
        tuple[PostInfo, list[Path], list[str], list[list[LinkRegion]]]
    ],
    docs_dir: Path,
    wordmark_image: Path | None = None,
    sun_image: Path | None = None,
    moon_image: Path | None = None,
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
    posts_with_pages : list of (PostInfo, list of Path, list of str, list of list of LinkRegion)
        Each post paired with its ordered page PNG paths, per-page alt
        text, and per-page detected links.
    docs_dir : Path
        Output directory (e.g. a repo's ``docs/`` folder for GitHub Pages).
    wordmark_image : Path, optional
        A handwritten "marginalia" wordmark image to use as the site header
        instead of the typeset fallback.
    sun_image, moon_image : Path, optional
        Hand-drawn sun and moon icons. When *both* are given, a light/dark
        toggle button is rendered on every page; otherwise the site follows
        the system preference with no toggle.
    """
    posts_dir = docs_dir / "posts"
    images_dir = docs_dir / "images"
    shutil.rmtree(posts_dir, ignore_errors=True)
    shutil.rmtree(images_dir, ignore_errors=True)
    posts_dir.mkdir(parents=True, exist_ok=True)
    images_dir.mkdir(parents=True, exist_ok=True)

    wordmark_post_src = None
    (docs_dir / "wordmark.png").unlink(missing_ok=True)
    if wordmark_image is not None:
        (docs_dir / "wordmark.png").write_bytes(wordmark_image.read_bytes())
        wordmark_post_src = "../wordmark.png"

    # The toggle needs both icons; place them at the docs root and reference
    # them relatively from the index (sun.png) and from posts (../sun.png).
    sun_index_src = moon_index_src = None
    sun_post_src = moon_post_src = None
    (docs_dir / "sun.png").unlink(missing_ok=True)
    (docs_dir / "moon.png").unlink(missing_ok=True)
    if sun_image is not None and moon_image is not None:
        (docs_dir / "sun.png").write_bytes(sun_image.read_bytes())
        (docs_dir / "moon.png").write_bytes(moon_image.read_bytes())
        sun_index_src, moon_index_src = "sun.png", "moon.png"
        sun_post_src, moon_post_src = "../sun.png", "../moon.png"

    regular_posts = []
    about_html = ""
    about_og_image = None

    for post, page_paths, page_alt_text, page_links in posts_with_pages:
        slug = post_slug(post)
        post_image_dir = images_dir / slug
        post_image_dir.mkdir(parents=True, exist_ok=True)

        dests = []
        for i, src in enumerate(page_paths):
            dest = post_image_dir / f"page_{i:03d}.png"
            dest.write_bytes(src.read_bytes())
            dests.append(dest)
        page_dims = [_png_dimensions(d) for d in dests]

        if is_about_page(post):
            about_paths = [
                f"images/{slug}/page_{i:03d}.png" for i in range(len(page_paths))
            ]
            about_html = _render_pages(
                about_paths, page_dims, page_alt_text, page_links
            )
            if about_paths:
                about_og_image = f"{SITE_URL}/{about_paths[0]}"
            continue

        relative_paths = [
            f"../images/{slug}/page_{i:03d}.png" for i in range(len(page_paths))
        ]
        (posts_dir / f"{slug}.html").write_text(
            render_post(
                post,
                relative_paths,
                page_dims,
                page_alt_text,
                page_links,
                wordmark_post_src,
                sun_post_src,
                moon_post_src,
            )
        )
        regular_posts.append(post)

    # The main page is the handwritten intro itself, so it doesn't repeat the
    # "amit" wordmark up top (that'd duplicate the "I'm Amit" in the intro).
    # The wordmark stays as the header / home link on individual post pages.
    (docs_dir / "index.html").write_text(
        render_index(
            regular_posts,
            about_html,
            about_og_image,
            wordmark_src=None,
            sun_src=sun_index_src,
            moon_src=moon_index_src,
        )
    )
