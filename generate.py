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
from blogpub.site import is_wordmark, write_site

FALLBACK_ALT_TEXT = "A handwritten notebook page."


def _build_wordmark(
    post: PostInfo,
    rmc_render: Path,
    cache_dir: Path,
    pages_dir: Path,
    ssh_host: str,
) -> Path | None:
    """Choose the best wordmark image for a "wordmark" notebook.

    Prefers rmc's high-resolution render, but if that comes out as a filled
    blob (thick / Calligraphy pen), falls back to the device's own thumbnail
    render, which handles every pen correctly at lower resolution -- fine for
    a small header. Returns None if neither is usable (typeset fallback).
    """
    if wordmark_render_is_clean(rmc_render):
        print("  -> using as handwritten site wordmark")
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
                out = pages_dir / "wordmark.png"
                thumbnail_to_wordmark(thumb, out)
                print("  -> using device thumbnail as handwritten site wordmark")
                return out

    print("  -> no usable wordmark render; keeping typeset fallback", file=sys.stderr)
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

        posts = list_posts_in_folder(cache_dir, folder_uuid)
        if not posts:
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
        wordmark_image = None
        for post in posts:
            print(f"Pulling and converting {post.name!r}...")
            pull_notebook_pages(args.ssh_host, post.uuid, cache_dir)
            png_paths = convert_post_pages(post, cache_dir, pages_dir)
            if not png_paths:
                print(f"  (no pages, skipping {post.name!r})")
                continue

            # A notebook named "wordmark"/"title" is the handwritten site
            # header, not a post -- use its first page and skip the rest.
            if is_wordmark(post):
                wordmark_image = _build_wordmark(
                    post, png_paths[0], cache_dir, pages_dir, args.ssh_host
                )
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

        write_site(posts_with_pages, args.docs_dir, wordmark_image)
        print(f"Wrote {len(posts_with_pages)} post(s) to {args.docs_dir}")
    finally:
        if cleanup:
            shutil.rmtree(cache_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
