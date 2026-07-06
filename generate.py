"""CLI entrypoint: publish reMarkable notebooks in a "Blog" folder as a static site."""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
from pathlib import Path

from blogpub.convert import (
    convert_post_pages,
    first_page_ids,
    normalize_icon_pair,
    thumbnail_to_wordmark,
    wordmark_render_is_clean,
)
from blogpub.links import (
    analyze_page_cached,
    load_manual_links,
    load_vision_cache,
    save_vision_cache,
)
from blogpub.pull import (
    PostInfo,
    find_folder_uuid,
    list_posts_in_folder,
    pull_metadata,
    pull_notebook_pages,
    pull_thumbnails,
)
from blogpub.site import is_moon, is_sun, is_wordmark, write_site

FALLBACK_ALT_TEXT = "A handwritten notebook page."


def _build_icon(
    post: PostInfo,
    rmc_render: Path,
    cache_dir: Path,
    pages_dir: Path,
    ssh_host: str,
    out_name: str,
    role: str,
) -> Path | None:
    """Choose the best small ink image for a chrome notebook (wordmark, sun, moon).

    Prefers rmc's high-resolution render, but if that comes out as a filled
    blob (thick / Calligraphy pen, or a filled sun disk), falls back to the
    device's own thumbnail render, which handles every pen correctly at lower
    resolution -- fine for a small header or icon. Returns None if neither is
    usable.

    Parameters
    ----------
    post : PostInfo
        The chrome notebook (wordmark / sun / moon).
    rmc_render : Path
        The already-converted first page (trimmed, bordered, centered).
    cache_dir : Path
        Local metadata / thumbnail cache directory.
    pages_dir : Path
        Where a thumbnail-derived icon is written, if needed.
    ssh_host : str
        SSH alias for pulling thumbnails on the blob fallback.
    out_name : str
        Filename for the thumbnail-derived icon (e.g. ``"sun.png"``).
    role : str
        Human-readable role for log messages (e.g. ``"sun toggle icon"``).

    Returns
    -------
    Path or None
        Path to the chosen icon image, or None if none is usable.
    """
    if wordmark_render_is_clean(rmc_render):
        print(f"  -> using as handwritten {role}")
        return rmc_render

    print(
        "  -> rmc render looks like a filled blob (thick/Calligraphy pen); "
        "trying the device thumbnail instead",
        file=sys.stderr,
    )
    if pull_thumbnails(ssh_host, post.uuid, cache_dir):
        page_ids = first_page_ids(post, cache_dir)
        if page_ids:
            thumb = cache_dir / f"{post.uuid}.thumbnails" / f"{page_ids[0]}.png"
            if thumb.exists():
                out = pages_dir / out_name
                thumbnail_to_wordmark(thumb, out)
                print(f"  -> using device thumbnail as handwritten {role}")
                return out

    print(f"  -> no usable {role} render; skipping", file=sys.stderr)
    return None


def main() -> None:
    """Parse CLI args and generate the static site from the tablet's Blog folder."""
    parser = argparse.ArgumentParser(
        description='Publish notebooks filed into a "Blog" folder on a reMarkable as a static site.'
    )
    parser.add_argument("--ssh-host", default="rm2", help="SSH alias for the tablet")
    parser.add_argument(
        "--folder", default="Blog", help='Folder name to publish from (default: "Blog")'
    )
    parser.add_argument("--docs-dir", type=Path, default=Path(__file__).parent / "docs")
    parser.add_argument("--cache-dir", type=Path, default=None)
    parser.add_argument(
        "--manual-links",
        type=Path,
        default=Path(__file__).parent / "manual_links.json",
        help="Path to manually-specified links (e.g. for hand-drawn icons)",
    )
    parser.add_argument(
        "--no-vision",
        action="store_true",
        help="Skip the vision-model pass (alt text + handwritten-link detection) "
        "-- faster, but pages get generic alt text and no auto-detected links",
    )
    args = parser.parse_args()

    cache_dir = args.cache_dir or Path(tempfile.mkdtemp(prefix="blogpub-"))
    cleanup = args.cache_dir is None

    try:
        print(f"Pulling notebook list from {args.ssh_host}...")
        pull_metadata(args.ssh_host, cache_dir)

        folder_uuid = find_folder_uuid(cache_dir, args.folder)
        if folder_uuid is None:
            print(
                f'No folder named "{args.folder}" found on the tablet.\n'
                f'Create one (New Folder -> "{args.folder}") and file notebooks into it to publish them.',
                file=sys.stderr,
            )
            sys.exit(1)

        # Chrome notebooks (wordmark, sun, moon) may live either directly in the
        # Blog folder or, to keep the folder tidy, in a subfolder named "extras".
        # Only top-level notebooks become posts; "extras" holds site chrome only.
        extras_uuid = find_folder_uuid(cache_dir, "extras", parent=folder_uuid)
        top_level = list_posts_in_folder(cache_dir, folder_uuid)
        extras_notebooks = (
            list_posts_in_folder(cache_dir, extras_uuid) if extras_uuid else []
        )
        if not top_level and not extras_notebooks:
            print(f'No notebooks found in the "{args.folder}" folder yet.')
            return

        manual_links = load_manual_links(args.manual_links)
        vision_cache_path = Path(__file__).parent / ".cache" / "vision.json"
        vision_cache = {} if args.no_vision else load_vision_cache(vision_cache_path)
        # Converted page PNGs live in a project-local dir (not the ephemeral
        # temp cache) so the `claude -p` vision subprocess is allowed to read
        # them -- it cannot read arbitrary /var/folders temp paths.
        pages_dir = Path(__file__).parent / ".cache" / "pages"
        shutil.rmtree(pages_dir, ignore_errors=True)
        posts_with_pages = []
        chrome: dict[str, Path | None] = {"wordmark": None, "sun": None, "moon": None}

        def _convert(notebook: PostInfo) -> list[Path]:
            print(f"Pulling and converting {notebook.name!r}...")
            pull_notebook_pages(args.ssh_host, notebook.uuid, cache_dir)
            return convert_post_pages(notebook, cache_dir, pages_dir)

        def _maybe_chrome(notebook: PostInfo, png_paths: list[Path]) -> bool:
            """Handle a wordmark/sun/moon notebook; return True if it was one."""
            if is_wordmark(notebook):
                chrome["wordmark"] = _build_icon(
                    notebook, png_paths[0], cache_dir, pages_dir,
                    args.ssh_host, "wordmark.png", "site wordmark",
                )
                return True
            if is_sun(notebook):
                chrome["sun"] = _build_icon(
                    notebook, png_paths[0], cache_dir, pages_dir,
                    args.ssh_host, "sun.png", "sun toggle icon",
                )
                return True
            if is_moon(notebook):
                chrome["moon"] = _build_icon(
                    notebook, png_paths[0], cache_dir, pages_dir,
                    args.ssh_host, "moon.png", "moon toggle icon",
                )
                return True
            return False

        # "extras" is chrome only: anything there that isn't wordmark/sun/moon
        # is ignored (not published as a post).
        for notebook in extras_notebooks:
            png_paths = _convert(notebook)
            if not png_paths:
                print(f"  (no pages, skipping {notebook.name!r})")
                continue
            if not _maybe_chrome(notebook, png_paths):
                print(f"  (in extras but not wordmark/sun/moon; skipping {notebook.name!r})")

        # Top level: posts and the About/intro page, plus wordmark/sun/moon if
        # filed here directly rather than in "extras".
        for post in top_level:
            png_paths = _convert(post)
            if not png_paths:
                print(f"  (no pages, skipping {post.name!r})")
                continue
            if _maybe_chrome(post, png_paths):
                continue

            if args.no_vision:
                page_alt_text = [FALLBACK_ALT_TEXT for _ in png_paths]
                page_links = [[] for _ in png_paths]
            else:
                print(
                    f"  Analyzing {len(png_paths)} page(s) (cached where unchanged)..."
                )
                analyses = [analyze_page_cached(p, vision_cache) for p in png_paths]
                page_alt_text = [a.alt_text for a in analyses]
                page_links = [list(a.links) for a in analyses]

            post_manual_links = manual_links.get(post.uuid, {})
            for i, extra in post_manual_links.items():
                if i < len(page_links):
                    page_links[i] = page_links[i] + extra

            posts_with_pages.append((post, png_paths, page_alt_text, page_links))

        if not args.no_vision:
            save_vision_cache(vision_cache_path, vision_cache)

        # Match the sun and moon to the same size so the toggle button looks
        # consistent in either state, however differently they were drawn.
        if chrome["sun"] is not None and chrome["moon"] is not None:
            normalize_icon_pair([chrome["sun"], chrome["moon"]])

        write_site(
            posts_with_pages,
            args.docs_dir,
            chrome["wordmark"],
            chrome["sun"],
            chrome["moon"],
        )
        print(f"Wrote {len(posts_with_pages)} post(s) to {args.docs_dir}")
    finally:
        if cleanup:
            shutil.rmtree(cache_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
