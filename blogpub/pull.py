"""Pull notebook data off a reMarkable tablet over SSH, filtered to a named folder."""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

REMOTE_XOCHITL = ".local/share/remarkable/xochitl"
_UUID_RE = re.compile(r"^[0-9a-fA-F-]{36}$")


@dataclass(frozen=True)
class PostInfo:
    """A single notebook destined to become a blog post.

    Parameters
    ----------
    uuid : str
        Document UUID.
    name : str
        Notebook name (``visibleName``), used as the post title.
    created_time : str
        Epoch milliseconds (as a string) the notebook was created.
    """

    uuid: str
    name: str
    created_time: str


def pull_metadata(ssh_host: str, cache_dir: Path) -> None:
    """Pull metadata/content/tombstone files for every document (cheap).

    Parameters
    ----------
    ssh_host : str
        SSH host alias for the tablet.
    cache_dir : Path
        Local directory to pull metadata files into.
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "rsync",
            "-az",
            "--include=*.metadata",
            "--include=*.content",
            "--include=*.tombstone",
            "--exclude=*",
            f"{ssh_host}:{REMOTE_XOCHITL}/",
            f"{cache_dir}/",
        ],
        check=True,
    )


def pull_notebook_pages(ssh_host: str, uuid: str, cache_dir: Path) -> None:
    """Pull one notebook's page data (``.rm`` files).

    Parameters
    ----------
    ssh_host : str
        SSH host alias for the tablet.
    uuid : str
        UUID of the notebook to pull page data for.
    cache_dir : Path
        Local cache directory.

    Raises
    ------
    ValueError
        If ``uuid`` doesn't look like a reMarkable document UUID (defense
        in depth before it's interpolated into an rsync remote path).
    """
    if not _UUID_RE.match(uuid):
        raise ValueError(f"refusing to pull suspicious uuid: {uuid!r}")
    subprocess.run(
        [
            "rsync",
            "-az",
            f"{ssh_host}:{REMOTE_XOCHITL}/{uuid}/",
            f"{cache_dir}/{uuid}/",
        ],
        check=True,
    )


def pull_thumbnails(ssh_host: str, uuid: str, cache_dir: Path) -> bool:
    """Pull a notebook's device-rendered page thumbnails, if any.

    The device renders every pen type correctly (unlike rmc), so its
    thumbnails are a low-res-but-clean fallback for pages that rmc mangles.

    Parameters
    ----------
    ssh_host : str
        SSH host alias for the tablet.
    uuid : str
        UUID of the notebook.
    cache_dir : Path
        Local cache directory.

    Returns
    -------
    bool
        True if a thumbnails directory was pulled, False if none exists.
    """
    if not _UUID_RE.match(uuid):
        raise ValueError(f"refusing to pull suspicious uuid: {uuid!r}")
    result = subprocess.run(
        [
            "rsync",
            "-az",
            f"{ssh_host}:{REMOTE_XOCHITL}/{uuid}.thumbnails/",
            f"{cache_dir}/{uuid}.thumbnails/",
        ],
        capture_output=True,
    )
    return result.returncode == 0


def find_folder_uuid(cache_dir: Path, folder_name: str) -> str | None:
    """Find a CollectionType (folder) document by name, case-insensitively.

    Parameters
    ----------
    cache_dir : Path
        Local metadata cache directory.
    folder_name : str
        Folder name to search for, e.g. ``"Blog"``.

    Returns
    -------
    str or None
        The folder's UUID, or None if no matching (non-deleted) folder exists.
    """
    for metadata_path in cache_dir.glob("*.metadata"):
        uuid = metadata_path.stem
        if (cache_dir / f"{uuid}.tombstone").exists():
            continue
        metadata = json.loads(metadata_path.read_text())
        if (
            metadata.get("type") == "CollectionType"
            and metadata.get("visibleName", "").strip().lower()
            == folder_name.strip().lower()
        ):
            return uuid
    return None


def list_posts_in_folder(cache_dir: Path, folder_uuid: str) -> list[PostInfo]:
    """Enumerate notebook documents filed directly into a given folder.

    Skips deleted documents and anything that isn't a plain notebook
    (PDFs, EPUBs, or documents missing their ``.content`` descriptor).

    Parameters
    ----------
    cache_dir : Path
        Local metadata cache directory.
    folder_uuid : str
        UUID of the folder to filter by.

    Returns
    -------
    list of PostInfo
        One entry per live notebook filed into the folder.
    """
    posts = []
    for metadata_path in sorted(cache_dir.glob("*.metadata")):
        uuid = metadata_path.stem
        if (cache_dir / f"{uuid}.tombstone").exists():
            continue
        metadata = json.loads(metadata_path.read_text())
        if metadata.get("type") != "DocumentType":
            continue
        if metadata.get("parent") != folder_uuid:
            continue
        content_path = cache_dir / f"{uuid}.content"
        if not content_path.exists():
            continue
        content = json.loads(content_path.read_text())
        if content.get("fileType") != "notebook":
            continue
        posts.append(
            PostInfo(
                uuid=uuid,
                name=metadata.get("visibleName", uuid),
                created_time=metadata.get("createdTime", "0"),
            )
        )
    return posts
