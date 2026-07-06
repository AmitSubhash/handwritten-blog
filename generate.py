"""CLI entrypoint: publish reMarkable notebooks in a "Blog" folder as a static site."""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
from pathlib import Path

from blogpub.convert import convert_post_pages
from blogpub.links import analyze_page, load_manual_links
from blogpub.pull import (
    find_folder_uuid,
    list_posts_in_folder,
    pull_metadata,
    pull_notebook_pages,
)
from blogpub.site import write_site

FALLBACK_ALT_TEXT = "A handwritten notebook page."


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
        pages_dir = cache_dir / "_pages"
        posts_with_pages = []
        for post in posts:
            print(f"Pulling and converting {post.name!r}...")
            pull_notebook_pages(args.ssh_host, post.uuid, cache_dir)
            png_paths = convert_post_pages(post, cache_dir, pages_dir)
            if not png_paths:
                print(f"  (no pages, skipping {post.name!r})")
                continue

            if args.no_vision:
                page_alt_text = [FALLBACK_ALT_TEXT for _ in png_paths]
                page_links = [[] for _ in png_paths]
            else:
                print(f"  Analyzing {len(png_paths)} page(s)...")
                analyses = [analyze_page(p) for p in png_paths]
                page_alt_text = [a.alt_text for a in analyses]
                page_links = [a.links for a in analyses]

            post_manual_links = manual_links.get(post.uuid, {})
            for i, extra in post_manual_links.items():
                if i < len(page_links):
                    page_links[i] = page_links[i] + extra

            posts_with_pages.append((post, png_paths, page_alt_text, page_links))

        write_site(posts_with_pages, args.docs_dir)
        print(f"Wrote {len(posts_with_pages)} post(s) to {args.docs_dir}")
    finally:
        if cleanup:
            shutil.rmtree(cache_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
