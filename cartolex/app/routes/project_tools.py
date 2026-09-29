# SPDX-License-Identifier: MIT
"""The settings' project tools: stop words, the AI's prompts, backup and restore, reset.

- **Stop words** (``decisions/stopwords.json``): words added to or removed from
  the lists of words that are never keywords, per language.
- **Prompts**: the instructions the AI clean-up sends, packaged with cartolex;
  a project may replace one with its own text (``decisions/prompts/<name>.txt``),
  which uses no placeholder the packaged text lacks.
- **Backup**: a zip of ``project.json`` and ``decisions/`` (with their
  history): what people decided. Texts, caches and built results are left
  out: they are collected or built again.
- **Restore**: the decision files of a backup replace the project's, each one
  kept in its history first; nothing else changes.
- **Reset**: the built results are removed (the project needs a build); the
  decisions, texts and caches (AI answers already paid for) stay.

Every write sends ``If-Match`` with the version it read (none for a file not
written yet), and waits while a job runs on the project.
"""

from __future__ import annotations

import io
import json
import shutil
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Annotated, Any, Literal

from fastapi import Path as PathParam
from fastapi import Request, Response
from pydantic import BaseModel, Field

from ..deps import ProjectDep
from ..errors import ApiError
from ..etags import check_version, etag_of, expected_version, version_of
from ..routing import Routes, runtime_of

routes = Routes(tags=["settings"])

Language = Annotated[str, Field(pattern=r"^[a-z]{2}$")]
Word = Annotated[str, Field(min_length=1, max_length=100)]
Words = Annotated[
    dict[Language, Annotated[list[Word], Field(max_length=5_000)]], Field(max_length=16)
]

#: The prompts a project may replace: name → (what uses it, the file in ``decisions/prompts``).
PROMPTS = {
    "triage_typed_system": "the AI clean-up of the keywords (keywords.triage)",
}
PromptName = Annotated[str, PathParam(pattern=r"^[a-z_]{1,64}$")]
#: The format of a backup's manifest (``backup.json`` at the root of the zip).
BACKUP_FORMAT = "cartolex-backup/1"


def _busy(request: Request, ctx: Any) -> None:
    from .build import busy_error

    running = runtime_of(request).jobs.running(ctx.id)
    if running is not None:
        raise busy_error(running)


# ── stop words ───────────────────────────────────────────────────────────────


def _stopwords(ctx: Any) -> dict[str, Any]:
    from cartolex.project.files import fingerprint, read_model
    from cartolex.project.models import StopwordsFile

    path = ctx.layout.stopwords_json
    doc = read_model(path, StopwordsFile) if path.exists() else StopwordsFile()
    return {
        "add": doc.add,  # type: ignore[attr-defined]
        "remove": doc.remove,  # type: ignore[attr-defined]
        "languages": list(ctx.project.config.languages.corpus),
        "version": version_of(fingerprint(path)),
    }


@routes.get("/api/settings/stopwords", action="settings.read")
def get_stopwords(response: Response, ctx: ProjectDep) -> dict[str, Any]:
    """The words added to and removed from the stop-word lists, per language."""
    view = _stopwords(ctx)
    response.headers["ETag"] = etag_of(view["version"])
    return view


class StopwordsBody(BaseModel):
    add: Words = {}
    remove: Words = {}


@routes.put("/api/settings/stopwords", action="settings.write")
def put_stopwords(
    request: Request, response: Response, body: StopwordsBody, ctx: ProjectDep
) -> dict[str, Any]:
    """Replace the stop-word changes (send ``If-Match``); the keywords need a build after."""
    from cartolex.project.files import json_bytes, write_decision
    from cartolex.project.models import StopwordsFile

    def clean(words: dict[str, list[str]]) -> dict[str, list[str]]:
        out = {
            lang: sorted({w.strip().casefold() for w in ws if w.strip()})
            for lang, ws in words.items()
        }
        return {lang: ws for lang, ws in sorted(out.items()) if ws}

    expected = expected_version(request)
    doc = StopwordsFile(add=clean(body.add), remove=clean(body.remove))
    both = {w for lang, ws in doc.add.items() for w in ws if w in set(doc.remove.get(lang, []))}
    if both:
        raise ApiError.of("stopword_both", words=sorted(both)[:10])
    with ctx.handle.mutex:
        check_version(ctx.layout.stopwords_json, expected)
        write_decision(
            ctx.layout,
            ctx.layout.stopwords_json,
            json_bytes(doc.model_dump(mode="json")),
            expected=expected,
            action="change the stop words",
        )
    view = _stopwords(ctx)
    response.headers["ETag"] = etag_of(view["version"])
    return view


# ── prompts ──────────────────────────────────────────────────────────────────


def _placeholders(text: str) -> set[str]:
    import string

    try:
        return {n for _, n, _, _ in string.Formatter().parse(text) if n is not None}
    except ValueError as exc:
        raise ApiError.of("prompt_invalid", detail=str(exc)) from exc


def _packaged(name: str) -> str:
    from cartolex.lexicon.prompt_store import packaged_prompt_dir

    return (packaged_prompt_dir() / f"{name}.txt").read_text(encoding="utf-8")


def _prompt(ctx: Any, name: str) -> dict[str, Any]:
    from cartolex.project.files import fingerprint

    if name not in PROMPTS:
        raise ApiError.of("prompt_not_found", name=name)
    path = ctx.layout.prompts / f"{name}.txt"
    packaged = _packaged(name)
    own = path.read_text(encoding="utf-8") if path.exists() else None
    return {
        "name": name,
        "used_by": PROMPTS[name],
        "packaged": packaged,
        "own": own,
        "placeholders": sorted(_placeholders(packaged)),
        "version": version_of(fingerprint(path)),
    }


@routes.get("/api/settings/prompts", action="settings.read")
def list_prompts(ctx: ProjectDep) -> dict[str, Any]:
    """The prompts a project may replace: the packaged text, the project's own, the placeholders."""
    items = [_prompt(ctx, name) for name in PROMPTS]
    return {"items": items, "total": len(items)}


class PromptBody(BaseModel):
    """The project's own text of a prompt; ``null`` goes back to the packaged one."""

    text: Annotated[str | None, Field(min_length=1, max_length=50_000)] = None


@routes.put("/api/settings/prompts/{name}", action="settings.write")
def put_prompt(
    request: Request, response: Response, name: PromptName, body: PromptBody, ctx: ProjectDep
) -> dict[str, Any]:
    """Replace a prompt with the project's own text, or go back to the packaged one (``If-Match``).

    The text may use only the placeholders of the packaged one (``prompt_placeholder``).
    """
    from cartolex.project.files import StaleWrite, fingerprint, utc_stamp, write_decision

    current = _prompt(ctx, name)
    expected = expected_version(request)
    path = ctx.layout.prompts / f"{name}.txt"
    if body.text is not None:
        unknown = sorted(_placeholders(body.text) - set(current["placeholders"]))
        if unknown:
            raise ApiError.of(
                "prompt_placeholder", unknown=unknown, allowed=current["placeholders"]
            )
    with ctx.handle.mutex:
        check_version(path, expected)
        if body.text is not None:
            write_decision(
                ctx.layout,
                path,
                body.text.encode("utf-8"),
                expected=expected,
                action=f"change the prompt {name}",
            )
        elif path.exists():
            found = fingerprint(path)
            if found != expected:
                raise StaleWrite(path, expected, found)
            folder = ctx.layout.history_of(path)
            folder.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, folder / f"{utc_stamp()}-back-to-the-packaged-prompt.txt")
            path.unlink()
    view = _prompt(ctx, name)
    response.headers["ETag"] = etag_of(view["version"])
    return view


# ── backup and restore ───────────────────────────────────────────────────────


def _backup_members(ctx: Any) -> list[tuple[Path, str]]:
    root = ctx.layout.root
    files = [(ctx.layout.project_json, "project.json")]
    for path in sorted(ctx.layout.decisions.rglob("*")):
        if path.is_file() and not path.name.startswith("."):
            files.append((path, path.relative_to(root).as_posix()))
    return files


@routes.get("/api/settings/backup", action="settings.read")
def backup(ctx: ProjectDep) -> Response:
    """A zip of what people decided: ``project.json`` and ``decisions/`` with its history."""
    from cartolex.project.project import cartolex_version

    buffer = io.BytesIO()
    members = _backup_members(ctx)
    now = datetime.now(timezone.utc)
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for path, name in members:
            zf.write(path, name)
        zf.writestr(
            "backup.json",
            json.dumps(
                {
                    "format": BACKUP_FORMAT,
                    "made_at": now.isoformat(timespec="seconds"),
                    "cartolex": cartolex_version(),
                    "files": len(members),
                    "leaves_out": "texts, caches and built results",
                },
                indent=2,
            ),
        )
    stamp = now.strftime("%Y%m%d-%H%M")
    return Response(
        buffer.getvalue(),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="cartolex-backup-{ctx.id}-{stamp}.zip"'
        },
    )


def _restorable(name: str) -> bool:
    parts = PurePosixPath(name).parts
    return (
        len(parts) >= 2
        and parts[0] == "decisions"
        and parts[1] != "history"
        and not any(p.startswith(".") or p in ("..", "") for p in parts)
        and "\\" not in name
    )


@routes.post("/api/settings/restore", action="settings.write")
async def restore(request: Request, ctx: ProjectDep) -> dict[str, Any]:
    """Put back the decision files of a backup (a multipart ``file``): each current version is
    kept in its history first; ``project.json`` and everything else stay as they are."""
    from cartolex.project.files import fingerprint, write_decision

    settings = runtime_of(request).settings
    if not request.headers.get("content-type", "").startswith("multipart/form-data"):
        raise ApiError.of("file_missing")
    _busy(request, ctx)
    form = await request.form(max_files=1, max_fields=4)
    try:
        item = form.get("file")
        if item is None or isinstance(item, str):
            raise ApiError.of("file_missing")
        data = await item.read(int(settings.max_upload_mb * 1024 * 1024) + 1)
    finally:
        await form.close()
    if len(data) > settings.max_upload_mb * 1024 * 1024:
        raise ApiError.of("file_too_large", limit_mb=int(settings.max_upload_mb))
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
        manifest = json.loads(zf.read("backup.json"))
    except (zipfile.BadZipFile, KeyError, ValueError) as exc:
        raise ApiError.of("not_a_backup") from exc
    if not isinstance(manifest, dict) or manifest.get("format") != BACKUP_FORMAT:
        raise ApiError.of("not_a_backup")
    members = [i for i in zf.infolist() if not i.is_dir() and _restorable(i.filename)]
    if len(members) > settings.max_archive_members:
        raise ApiError.of("not_a_backup")
    restored: list[str] = []
    with ctx.handle.mutex:
        for info in members:
            target = ctx.layout.root / PurePosixPath(info.filename)
            content = zf.read(info)
            if target.exists() and target.read_bytes() == content:
                continue
            write_decision(
                ctx.layout, target, content, expected=fingerprint(target), action="restore a backup"
            )
            restored.append(info.filename)
    return {"restored": restored, "count": len(restored), "made_at": manifest.get("made_at")}


class ResetBody(BaseModel):
    """What to reset: ``built``, the built results (the decisions, texts and caches stay)."""

    what: Literal["built"]


@routes.post("/api/settings/reset", action="settings.write")
def reset(request: Request, body: ResetBody, ctx: ProjectDep) -> dict[str, Any]:
    """Remove the built results: every stage is then « never built »; nothing else changes."""
    _busy(request, ctx)
    derived = ctx.layout.derived
    removed = 0
    with ctx.handle.mutex:
        for child in sorted(derived.iterdir()) if derived.exists() else []:
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()
            removed += 1
    # The caches are keyed by the runs they were read from: gone runs are never read again.
    return {"reset": body.what, "removed": removed}
