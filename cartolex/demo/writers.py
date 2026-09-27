# SPDX-License-Identifier: MIT
"""Write a demo world to disk: the neutral ``cartolex-demo/1`` files and the corpus contract.

Every file is UTF-8 with ``\\n`` line endings and a fixed row and key order,
so writing the same world twice gives byte-identical files.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import shutil
from collections.abc import Iterable, Sequence
from pathlib import Path

from .model import FORMAT, GENERATOR_VERSION, DemoWorld, Work
from .vocabulary import Term, Theme

PEOPLE_COLUMNS = (
    "person_id",
    "last_name",
    "first_name",
    "group",
    "institution",
    "site",
    "career_stage",
    "orcid",
    "openalex_id",
    "idhal",
    "role",
)
GROUP_COLUMNS = ("group_id", "acronym", "name", "institution", "site", "lat", "lon")
WORK_COLUMNS = (
    "work_id",
    "title",
    "year",
    "doc_type",
    "language",
    "doi",
    "venue",
    "sources",
    "text_path",
)
AUTHORSHIP_COLUMNS = ("work_id", "person_id", "position")
INDEX_COLUMNS = ("last_name", "first_name", "unit", "txt_path", "doc_year", "doc_type")

# Files and folders a world owns inside its output directory.
WORLD_FILES = (
    "manifest.json",
    "people.csv",
    "groups.csv",
    "works.csv",
    "authorships.csv",
    "truth.json",
)
WORLD_DIRS = ("texts",)
CORPUS_FILES = ("manual_index.csv",)
CORPUS_DIRS = ("automatic_data/corpus_manual", "overlay")


def csv_bytes(columns: Sequence[str], rows: Iterable[Sequence[object]]) -> bytes:
    """Render a CSV table (header + rows) as UTF-8 bytes with ``\\n`` line endings."""
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(columns)
    for row in rows:
        writer.writerow(row)
    return buf.getvalue().encode("utf-8")


def json_bytes(obj: object) -> bytes:
    """Render JSON as UTF-8 bytes: two-space indent, non-ASCII kept, final newline."""
    return (json.dumps(obj, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def text_path(work: Work) -> str:
    """Path of a work's text file inside a world directory."""
    return f"texts/{work.work_id}.txt"


def _prepare(root: Path, files: Sequence[str], dirs: Sequence[str], overwrite: bool) -> None:
    existing = [n for n in (*files, *dirs) if (root / n).exists()]
    if existing and not overwrite:
        raise FileExistsError(
            f"{root} already holds {', '.join(existing)}; pass overwrite=True to replace them"
        )
    for name in files:
        (root / name).unlink(missing_ok=True)
    for name in dirs:
        shutil.rmtree(root / name, ignore_errors=True)
    root.mkdir(parents=True, exist_ok=True)


def _term_entry(term: Term, portuguese: bool) -> dict:
    entry = {"en": term.en, "fr": term.fr, "fr_article": term.article}
    if portuguese:
        entry.update(pt=term.pt, pt_article=term.pt_article)
    entry["technique"] = term.technique
    return entry


def _theme_entry(theme: Theme, portuguese: bool) -> dict:
    entry = {"id": theme.id, "name_en": theme.name_en, "name_fr": theme.name_fr}
    if portuguese:
        entry["name_pt"] = theme.name_pt
    entry["terms"] = [_term_entry(x, portuguese) for x in theme.terms]
    return entry


def truth(world: DemoWorld) -> dict:
    """The ground truth: themes and their terms, and who and what is about which theme.

    A world in another language set than the default one also records its
    languages, the Portuguese forms of the terms and themes, and the
    ``lexicon``: every phrase the texts are written with, by language, with
    whether it is a field term (see :func:`lexicon_truth`). A default world
    keeps the historical format, byte for byte.
    """
    portuguese = "pt" in world.languages
    doc: dict = {"themes": [_theme_entry(t, portuguese) for t in world.themes]}
    doc["groups"] = {g.group_id: g.themes for g in world.groups}
    doc["people"] = {p.person_id: p.themes for p in world.people}
    doc["works"] = {w.work_id: list(w.themes) for w in world.works}
    doc["coverage"] = {p.person_id: p.coverage for p in world.people}
    if world.trilingual:
        doc["languages"] = list(world.languages)
        doc["lexicon"] = lexicon_truth(world.languages)
    return doc


def _forms(term: Term, languages: Sequence[str]) -> list[tuple[str, str]]:
    """``(language, form without article)`` of a term in *languages*."""
    forms = {"en": term.en, "fr": term.fr, "pt": term.pt}
    return [(lang, forms[lang]) for lang in languages if forms.get(lang)]


def lexicon_truth(languages: Sequence[str]) -> list[dict]:
    """Every phrase the demo texts are written with, per language, and what it is.

    One record per phrase and language: ``text`` (a term without its article,
    or a setting phrase, or a literal piece of a sentence template), ``lang``,
    ``kind`` and ``field``:

    - ``theme``: a theme term (``canonical`` is its English form, ``themes``
      the themes it belongs to, ``technique`` whether it is a tool or
      approach) — a field term;
    - ``method``: a method shared by the themes (``scope`` natural, social or
      any) — a field term;
    - ``driver``: a driver of change named in the texts (``climate change``)
      — context, not a field term;
    - ``setting``: a study-setting phrase (``on sandy beaches``) — context,
      not a field term;
    - ``template``: a literal piece of a sentence template or lead-in (the
      text between two slots) — generic filler.

    Records are sorted by kind, language and text.
    """
    from .texts import LEADINS, TEMPLATES, slot_free_pieces
    from .vocabulary import DRIVERS, METHODS, SETTINGS, THEMES

    langs = [lang for lang in ("en", "fr", "pt") if lang in languages]
    themes_of: dict[str, list[str]] = {}
    terms: dict[str, Term] = {}
    for theme in THEMES:
        for term in theme.terms:
            themes_of.setdefault(term.en, []).append(theme.id)
            terms.setdefault(term.en, term)
    records: list[dict] = []
    for en, term in terms.items():
        for lang, form in _forms(term, langs):
            records.append(
                {
                    "text": form,
                    "lang": lang,
                    "kind": "theme",
                    "field": True,
                    "canonical": en,
                    "themes": themes_of[en],
                    "technique": term.technique,
                }
            )
    for method in METHODS:
        for lang, form in _forms(method.term, langs):
            records.append(
                {
                    "text": form,
                    "lang": lang,
                    "kind": "method",
                    "field": True,
                    "canonical": method.term.en,
                    "scope": method.kind,
                }
            )
    for driver in DRIVERS:
        for lang, form in _forms(driver, langs):
            records.append(
                {
                    "text": form,
                    "lang": lang,
                    "kind": "driver",
                    "field": False,
                    "canonical": driver.en,
                }
            )
    for setting in SETTINGS:
        phrases = {"en": setting.en, "fr": setting.fr, "pt": setting.pt}
        for lang in langs:
            records.append(
                {
                    "text": phrases[lang],
                    "lang": lang,
                    "kind": "setting",
                    "field": False,
                    "canonical": setting.en,
                }
            )
    pieces: set[tuple[str, str]] = set()
    for (lang, _kind), roles in TEMPLATES.items():
        if lang in langs:
            for templates in roles.values():
                pieces |= {(lang, p) for t in templates for p in slot_free_pieces(t)}
    for lang, roles in LEADINS.items():
        if lang in langs:
            pieces |= {(lang, p) for leads in roles.values() for p in leads}
    for lang, text in pieces:
        records.append({"text": text, "lang": lang, "kind": "template", "field": False})
    order = {"theme": 0, "method": 1, "driver": 2, "setting": 3, "template": 4}
    records.sort(key=lambda r: (order[r["kind"]], r["lang"], r["text"], r.get("canonical", "")))
    return records


def world_files(world: DemoWorld) -> dict[str, bytes]:
    """Every file of the neutral format except the manifest, by relative path."""
    files: dict[str, bytes] = {}
    files["people.csv"] = csv_bytes(
        PEOPLE_COLUMNS,
        (
            (
                p.person_id,
                p.last_name,
                p.first_name,
                p.group,
                p.institution,
                p.site,
                p.career_stage,
                p.orcid,
                p.openalex_id,
                p.idhal,
                p.role,
            )
            for p in world.people
        ),
    )
    files["groups.csv"] = csv_bytes(
        GROUP_COLUMNS,
        (
            (g.group_id, g.acronym, g.name, g.institution, g.site, f"{g.lat:.4f}", f"{g.lon:.4f}")
            for g in world.groups
        ),
    )
    files["works.csv"] = csv_bytes(
        WORK_COLUMNS,
        (
            (
                w.work_id,
                w.title,
                w.year,
                w.doc_type,
                w.language,
                w.doi,
                w.venue,
                ";".join(w.sources),
                text_path(w),
            )
            for w in world.works
        ),
    )
    files["authorships.csv"] = csv_bytes(
        AUTHORSHIP_COLUMNS,
        ((w.work_id, pid, pos) for w in world.works for pos, pid in enumerate(w.authors, 1)),
    )
    files["truth.json"] = json_bytes(truth(world))
    for w in world.works:
        files[text_path(w)] = w.text.encode("utf-8")
    return files


def write_world(world: DemoWorld, out_dir: Path, *, overwrite: bool = False) -> dict:
    """Write the neutral ``cartolex-demo/1`` files into *out_dir* and return the manifest."""
    _prepare(out_dir, WORLD_FILES, WORLD_DIRS, overwrite)
    (out_dir / "texts").mkdir()
    files = world_files(world)
    for rel, data in files.items():
        (out_dir / rel).write_bytes(data)
    manifest: dict = {"format": FORMAT, "size": world.size, "seed": world.seed}
    if world.trilingual:
        manifest["languages"] = list(world.languages)
    manifest["generator"] = f"cartolex.demo {GENERATOR_VERSION}"
    manifest["counts"] = world.counts()
    manifest["files"] = {rel: hashlib.sha256(files[rel]).hexdigest() for rel in sorted(files)}
    (out_dir / "manifest.json").write_bytes(json_bytes(manifest))
    return manifest


def write_corpus(world: DemoWorld, workspace: Path, *, overwrite: bool = False) -> dict:
    """Write the engine's corpus contract into *workspace* and return a summary.

    One index row per authorship: the fitted cohort's rows go to
    ``manual_index.csv`` with texts in ``automatic_data/corpus_manual/``; the
    rows of each projected set go to ``overlay/<set>/index.csv`` with texts in
    ``overlay/<set>/texts/``. Paths in an index are relative to its folder.
    A person without works has no row. Projected sets never reach the manual
    slot.
    """
    _prepare(workspace, CORPUS_FILES, CORPUS_DIRS, overwrite)
    groups = {g.group_id: g for g in world.groups}
    works = {w.work_id: w for w in world.works}
    by_person: dict[str, list[Work]] = {}
    for w in world.works:
        for pid in w.authors:
            by_person.setdefault(pid, []).append(w)

    def rows_for(people, folder: str) -> tuple[list[tuple], set[str]]:
        rows, used = [], set()
        for p in people:
            for w in sorted(by_person.get(p.person_id, []), key=lambda w: (w.year, w.work_id)):
                rows.append(
                    (
                        p.last_name,
                        p.first_name,
                        groups[p.group].acronym,
                        f"{folder}/{w.work_id}.txt",
                        w.year,
                        w.doc_type,
                    )
                )
                used.add(w.work_id)
        return rows, used

    summary: dict[str, dict] = {}
    rows, used = rows_for(world.cohort, "automatic_data/corpus_manual")
    corpus_dir = workspace / "automatic_data" / "corpus_manual"
    corpus_dir.mkdir(parents=True)
    for wid in sorted(used):
        (corpus_dir / f"{wid}.txt").write_bytes(works[wid].text.encode("utf-8"))
    (workspace / "manual_index.csv").write_bytes(csv_bytes(INDEX_COLUMNS, rows))
    summary["manual"] = {
        "index": "manual_index.csv",
        "rows": len(rows),
        "texts": len(used),
        "people": len({(r[0], r[1], r[2]) for r in rows}),
    }
    for name, members in world.overlay_sets.items():
        set_dir = workspace / "overlay" / name
        (set_dir / "texts").mkdir(parents=True)
        rows, used = rows_for(members, "texts")
        for wid in sorted(used):
            (set_dir / "texts" / f"{wid}.txt").write_bytes(works[wid].text.encode("utf-8"))
        (set_dir / "index.csv").write_bytes(csv_bytes(INDEX_COLUMNS, rows))
        summary[f"overlay:{name}"] = {
            "index": f"overlay/{name}/index.csv",
            "rows": len(rows),
            "texts": len(used),
            "people": len({(r[0], r[1], r[2]) for r in rows}),
        }
    return summary
