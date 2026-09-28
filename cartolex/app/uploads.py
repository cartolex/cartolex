# SPDX-License-Identifier: MIT
"""Receiving files: written only inside their target folder, sizes capped, archives checked.

:func:`save_upload` streams an uploaded file into a target folder under a
clean name, and stops past a size limit. :func:`extract_archive` unpacks a
zip archive into a target folder after checking every member: no absolute
path, no ``..``, no link, no device, no hidden name, no file already there, at
most so many members and so many bytes once unpacked (a small archive can hold
a huge file). Any refused member refuses the whole archive, and nothing is
written. Nothing uploaded ever replaces a file.
"""

from __future__ import annotations

import re
import stat
import zipfile
from collections.abc import AsyncIterator
from pathlib import Path, PurePosixPath

from .errors import ApiError

__all__ = ["clean_name", "extract_archive", "save_upload"]

_UNSAFE = re.compile(r"[^A-Za-z0-9._ -]+")


def clean_name(name: str, *, default: str = "upload") -> str:
    """A file name safe in any folder: its last part, plain characters, no leading dot."""
    last = PurePosixPath(str(name).replace("\\", "/")).name
    cleaned = _UNSAFE.sub("_", last).strip(" .")
    return cleaned[:120] or default


def _inside(target: Path, path: Path) -> bool:
    try:
        path.resolve().relative_to(target.resolve())
    except ValueError:
        return False
    return True


async def save_upload(
    chunks: AsyncIterator[bytes], target: Path, name: str, *, max_bytes: int
) -> Path:
    """Write *chunks* to ``target/<clean name>``; refuse (413) past *max_bytes*."""
    target.mkdir(parents=True, exist_ok=True)
    path = target / clean_name(name)
    if not _inside(target, path):
        raise ApiError(422, "unsafe_path", f"the name {name!r} leaves its folder")
    if path.exists():
        raise ApiError(
            409,
            "exists",
            f"{path.name} is already there; nothing is replaced (rename the file to add it)",
            next_action="fix-input",
        )
    size = 0
    tmp = path.with_name(path.name + ".part")
    try:
        with open(tmp, "wb") as fh:
            async for chunk in chunks:
                size += len(chunk)
                if size > max_bytes:
                    raise ApiError(
                        413,
                        "too_large",
                        f"the file is larger than the limit ({max_bytes // (1024 * 1024)} MB)",
                        next_action="fix-input",
                    )
                fh.write(chunk)
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)
    return path


def _member_problem(info: zipfile.ZipInfo) -> str | None:
    name = info.filename
    if "\x00" in name or "\\" in name:
        return "a name with a backslash or a null character"
    pure = PurePosixPath(name)
    if pure.is_absolute() or re.match(r"^[A-Za-z]:", name):
        return "an absolute path"
    if ".." in pure.parts:
        return "a path that climbs out of the folder (..)"
    if any(p.startswith(".") for p in pure.parts):
        return "a hidden name"
    kind = stat.S_IFMT(info.external_attr >> 16)
    if kind and kind not in (stat.S_IFREG, stat.S_IFDIR):
        return "a link or a special file"
    return None


def extract_archive(archive: Path, target: Path, *, max_members: int, max_bytes: int) -> list[Path]:
    """Unpack the zip *archive* into *target* after checking every member; returns the files."""
    try:
        zf = zipfile.ZipFile(archive)
    except zipfile.BadZipFile as exc:
        raise ApiError(422, "bad_archive", "the file is not a zip archive") from exc
    with zf:
        members = zf.infolist()
        if len(members) > max_members:
            raise ApiError(413, "too_large", f"the archive holds more than {max_members} files")
        total = 0
        for info in members:
            problem = _member_problem(info)
            if problem is not None:
                raise ApiError(
                    422,
                    "unsafe_path",
                    f"the archive was refused: a member has {problem}; nothing was written",
                    next_action="fix-input",
                )
            if not _inside(target, target / info.filename):
                raise ApiError(422, "unsafe_path", "a member leaves the folder; nothing written")
            if not info.is_dir() and (target / info.filename).exists():
                raise ApiError(
                    409,
                    "exists",
                    f"the archive would replace {info.filename}; nothing was written",
                    next_action="fix-input",
                )
            total += info.file_size
        if total > max_bytes:
            raise ApiError(
                413,
                "too_large",
                f"the archive unpacks to more than {max_bytes // (1024 * 1024)} MB",
                next_action="fix-input",
            )
        written: list[Path] = []
        for info in members:
            if info.is_dir():
                continue
            dest = target / info.filename
            dest.parent.mkdir(parents=True, exist_ok=True)
            size = 0
            with zf.open(info) as src, open(dest, "wb") as out:
                while block := src.read(1 << 20):
                    size += len(block)
                    if size > info.file_size:  # a member that lies about its size
                        out.close()
                        dest.unlink(missing_ok=True)
                        raise ApiError(422, "bad_archive", "a member is larger than it says")
                    out.write(block)
            written.append(dest)
    return written
