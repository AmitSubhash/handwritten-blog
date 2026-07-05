# marginalia

A blog written entirely by hand on a reMarkable tablet, published as a plain
static site via GitHub Pages. Inspired by
[handwritten.danieljanus.pl](https://handwritten.danieljanus.pl/).

## How it works

1. Create a folder named **"Blog"** on your reMarkable (via the normal UI —
   New Folder).
2. File any notebook you want published into that folder.
3. Run `python generate.py`. It pulls those notebooks over SSH, converts
   each page to a PNG via [`rmc`](https://github.com/ricklupton/rmc) +
   `rsvg-convert`, and writes a static site into `docs/`.
4. Commit and push — GitHub Pages serves straight from `docs/` on `main`.

Only notebooks explicitly filed into the "Blog" folder are ever published.
Everything else on the tablet stays private.

## Hyperlinks in handwriting

If you write a URL by hand on a page (e.g. `example.com/paper`), it becomes
a real clickable link on the published post -- an invisible overlay
highlights on hover. Detection uses Claude's vision model to find any
handwritten web addresses and their approximate position, since there's no
way to derive link coordinates from the raw ink data directly.

This is a lower-effort alternative to
[danieljanus's implementation](https://handwritten.danieljanus.pl/2022-10-01-hyperlinks-in-handwriting.html),
which overlays HTML image-map regions at manually-identified pixel
coordinates. Automated bounding boxes from a vision model are approximate,
not pixel-exact -- treat the resulting overlay as a best-effort convenience.
Pass `--no-links` to `generate.py` to skip this (one fewer Claude call per
page) if you don't need it.

## Requirements

- SSH access to the tablet (see [rmscribe](https://github.com/AmitSubhash/rmscribe)
  for the same SSH setup)
- `rsync`, `rsvg-convert` (`brew install librsvg`)
- Python 3.10+

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python generate.py
```
