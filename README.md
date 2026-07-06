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

## Notebook naming conventions (what to name a notebook to make it do something)

Everything the site does is driven by what you name a notebook inside the
**Blog** folder — no config to edit:

| Notebook name | What it becomes |
|---|---|
| `Blog` (folder) | The one folder that gets published. Nothing outside it is ever touched. |
| `Hi` or `About` | The intro shown at the top of the main page (not listed as a post). |
| `wordmark` or `title` | The handwritten site header / home link (your "amit"). Renders on post pages; the main page shows no header. |
| `sun` **and** `moon` | The hand-drawn light/dark toggle. Draw both, and a corner button appears that shows the sun in light mode and the moon in dark mode (click it to flip); it remembers the reader's choice. The two are auto-scaled to a matched size, so it's fine if you drew them differently. Draw only one (or neither) and the site just follows the reader's system preference with no toggle. |
| anything else | A normal blog post, listed newest-first. |

**Adding a new post:** make a new notebook, file it in `Blog`, done. Its name
becomes the post title and its pages become the post body. Editing an existing
notebook re-publishes it (only changed pages are re-processed). Removing a
notebook from `Blog` unpublishes it on the next build.

**Keeping `Blog` tidy (optional):** the chrome notebooks (`wordmark`, `sun`,
`moon`) can live either directly in `Blog` or in a subfolder named `extras`
inside `Blog`. Put them in `extras` so the top level of `Blog` holds only your
actual posts. Anything in `extras` that isn't `wordmark`/`sun`/`moon` is
ignored (never published).

Publishing is automatic: file a notebook into `Blog`, and it goes live within
~2 hours (or instantly when you triple-tap the power button). Run
`python generate.py` yourself for an immediate manual build.

Two things happen to every published page automatically: it's cropped to the
ink and each content block is horizontally centered (see
`~/.claude/skills/handwriting-block-center`). Write a URL by hand and it
becomes a clickable link (below).

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
