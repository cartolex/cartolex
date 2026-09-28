# SPDX-License-Identifier: MIT
"""The theme handoff's lab check: a bundle of the demo world for a blind judge, and its score.

Usage::

    python tools/themes_handoff_lab.py bundle ~/cartolex-work/themes-handoff-test
    python tools/themes_handoff_lab.py score ~/cartolex-work/themes-handoff-test

``bundle`` writes the S demo world (seed 0, at its rule's depth) as a project
in ``<folder>/project``, builds it, and writes the theme handoff of the
grouping's proposal in ``<folder>/bundle/``: ``prompt.txt`` and ``tree.txt``
(what a judge reads: nothing else), ``expected-answer.txt`` and
``bundle.json``. A judge's answer goes next to them as ``answer.txt``.

``score`` reads the answer back (:func:`cartolex.project.themes_handoff.parse_answer`)
and scores every proposed operation, applied alone to the tree that was
sent, against the demo world's known themes:

- **the truth.** A keyword's theme is the primary theme of the world's works
  that use it, when at least 60 % of them (and at least two works) share it;
  other keywords are spread over themes (generic words, methods of every
  theme) or unseen;
- **move, merge, split.** The B-cubed F1 of the top-level nodes against the
  themes, over the keywords with a theme (a keyword set aside counts as a
  group of its own): an operation *improves* the tree when it raises it,
  *worsens* it when it lowers it, and is *neutral* otherwise;
- **rename.** The words a node's name shares with its main theme's names (in
  every language of the world) and its id: more words improve, fewer worsen;
- **set aside, attribution 0.** Improves for a spread keyword whose main theme
  holds under 40 % of its works, worsens for a keyword with a theme; any other
  attribution is neutral.

The report (``<folder>/score.md``) gives the counts per action, the share of
operations that improve the tree, and the F1 before and after applying every
applicable operation in order.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SIZE, SEED = "S", 0
#: A keyword's theme needs this share of the works that use it, and this many works.
THEME_SHARE, THEME_WORKS = 0.6, 2
#: A keyword whose main theme holds less than this share is spread over themes.
SPREAD_SHARE = 0.4
STOPWORDS = {
    "and", "of", "the", "in", "on", "a", "an", "for", "to", "et", "de", "des", "du", "la", "le",
    "les", "l", "d", "e", "do", "da", "dos", "das", "o", "os", "as", "em", "no", "na",
}  # fmt: skip


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKD", str(text).casefold())
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"[\s’'\-]+", " ", text).strip()


def _words(text: str) -> set[str]:
    return {w for w in re.split(r"[^a-z0-9]+", _norm(text)) if len(w) > 2 and w not in STOPWORDS}


# ── the bundle ───────────────────────────────────────────────────────────────


def _usage(project: Path) -> dict[str, list[float]]:
    """Each keyword's ``[people, weight]``, as ``GET /api/themes/usage`` gives it."""
    import numpy as np

    from cartolex.atlas.model_files import load_lexical_data

    data = load_lexical_data(project / "derived" / "themes.space" / "models" / "lexical_data.json")
    X = data.X_tf if data.X_tf is not None else data.X
    X = X.tocsr()
    totals = np.asarray(X.sum(axis=1)).ravel()
    scale = np.divide(1.0, totals, out=np.zeros_like(totals, dtype=float), where=totals > 0)
    weights = np.asarray(X.multiply(scale[:, None]).sum(axis=0)).ravel()
    people = np.asarray((X > 0).sum(axis=0)).ravel()
    return {
        str(t): [int(n), round(float(w), 4)]
        for t, n, w in zip(data.terms, people, weights, strict=True)
    }


def bundle(folder: Path) -> Path:
    """Build the demo world as a project and write the theme handoff of its proposal."""
    from cartolex.cli import main as cli
    from cartolex.demo import generate
    from cartolex.demo.project import DOMAIN_DESCRIPTION, DOMAIN_TITLE, write_project
    from cartolex.project.models import ThemesFile
    from cartolex.project.themes_handoff import bundle_parts, part_files

    project = folder / "project"
    if not (project / "project.json").exists():
        write_project(generate(SIZE, SEED), project).close()
        assert cli(["params", str(project), "--set", "pinned_year=2026"]) == 0
        assert cli(["build", str(project)]) == 0
    draft = project / "derived" / "themes.group" / "themes_draft.json"
    tree = ThemesFile.model_validate(json.loads(draft.read_text(encoding="utf-8")))
    [part] = bundle_parts(
        tree, _usage(project), domain=DOMAIN_TITLE, description=DOMAIN_DESCRIPTION
    )
    out = folder / "bundle"
    out.mkdir(parents=True, exist_ok=True)
    for name, text in part_files(part).items():
        (out / name).write_text(text, encoding="utf-8")
    (folder / "lab.json").write_text(
        json.dumps({"size": SIZE, "seed": SEED, "tokens": part["tokens"]}, indent=1) + "\n",
        encoding="utf-8",
    )
    return out


# ── the truth and the scores ─────────────────────────────────────────────────


def truth(keywords: list[str], size: str = SIZE, seed: int = SEED) -> dict[str, Any]:
    """Each keyword's theme (or none), its main share, and the themes' names."""
    from cartolex.demo import generate
    from cartolex.demo.vocabulary import THEMES

    world = generate(size, seed)
    texts = [(" " + _norm(f"{w.title} . {w.abstract}") + " ", w.themes[0]) for w in world.works]
    theme_of: dict[str, str] = {}
    share_of: dict[str, float] = {}
    for k in keywords:
        key = " " + _norm(k) + " "
        counts = Counter(th for text, th in texts if key in text)
        n = sum(counts.values())
        if not n:
            continue
        th, m = counts.most_common(1)[0]
        share_of[k] = m / n
        if m / n >= THEME_SHARE and n >= THEME_WORKS:
            theme_of[k] = th
    names = {
        th.id: _words(f"{th.name_en} {th.name_fr} {th.name_pt} {th.id.replace('-', ' ')}")
        for th in THEMES
    }
    return {"theme": theme_of, "share": share_of, "names": names}


def _tops(tree: Any) -> dict[str, str]:
    parent = {n.id: n.parent for n in tree.nodes}

    def top(nid: str) -> str:
        while parent.get(nid) is not None:
            nid = parent[nid]  # type: ignore[assignment]
        return nid

    return {k: top(n) for k, n in tree.keywords.items()}


def bcubed(tree: Any, theme_of: dict[str, str]) -> float:
    """B-cubed F1 of the tree's top-level nodes against the themes (keywords with a theme)."""
    place = _tops(tree)
    items = [k for k in theme_of if k in place or k in tree.set_aside]
    group = {k: place.get(k, f"aside:{k}") for k in items}
    by_group: dict[str, list[str]] = defaultdict(list)
    by_theme: dict[str, list[str]] = defaultdict(list)
    for k in items:
        by_group[group[k]].append(k)
        by_theme[theme_of[k]].append(k)
    if not items:
        return 0.0
    precision = recall = 0.0
    for k in items:
        same_group = by_group[group[k]]
        same_theme = by_theme[theme_of[k]]
        both = sum(1 for j in same_group if theme_of[j] == theme_of[k])
        precision += both / len(same_group)
        recall += both / len(same_theme)
    p, r = precision / len(items), recall / len(items)
    return 2 * p * r / (p + r) if p + r else 0.0


def _main_theme(tree: Any, node: str, theme_of: dict[str, str]) -> str | None:
    parent = {n.id: n.parent for n in tree.nodes}

    def under(nid: str | None) -> bool:
        while nid is not None:
            if nid == node:
                return True
            nid = parent.get(nid)
        return False

    counts = Counter(theme_of[k] for k, n in tree.keywords.items() if k in theme_of and under(n))
    return counts.most_common(1)[0][0] if counts else None


def _apply(tree: Any, op: dict[str, Any]) -> Any:
    from cartolex.project import themes as t

    kind = op["op"]
    if kind == "rename_node":
        return t.rename_node(tree, op["node_id"], op["names"]).tree
    if kind == "move_keywords":
        return t.move_keywords(tree, op["keywords"], op["node_id"]).tree
    if kind == "put_back":
        return t.put_back(tree, op["keywords"], op["node_id"]).tree
    if kind == "merge_nodes":
        return t.merge_nodes(tree, op["source"], op["target"]).tree
    if kind == "split_node":
        parts = [(p["members"], p["names"]) for p in op["parts"]]
        return t.split_node(tree, op["node_id"], parts).tree
    if kind == "set_aside":
        return t.set_aside(tree, op["keywords"], op["reason"]).tree
    return t.set_attribution(tree, op["keywords"], op["levels"]).tree


def judge(item: Any, tree: Any, gold: dict[str, Any]) -> str:
    """``improves``, ``neutral``, ``worsens`` or ``refused`` for one proposed operation."""
    if item.refused:
        return "refused"
    op = item.op
    theme_of, share_of = gold["theme"], gold["share"]
    if op["op"] == "rename_node":
        main = _main_theme(tree, op["node_id"], theme_of)
        if main is None:
            return "neutral"
        node = next(n for n in tree.nodes if n.id == op["node_id"])
        before = len(_words(" ".join(node.names.values())) & gold["names"][main])
        after = len(_words(" ".join(op["names"].values())) & gold["names"][main])
        return "improves" if after > before else "worsens" if after < before else "neutral"
    if op["op"] in ("set_aside", "set_attribution") and (
        op["op"] == "set_aside" or op.get("levels") == 0
    ):
        k = op["keywords"][0]
        if k in theme_of:
            return "worsens"
        return "improves" if share_of.get(k, 0.0) < SPREAD_SHARE else "neutral"
    if op["op"] == "set_attribution":
        return "neutral"
    before = bcubed(tree, theme_of)
    after = bcubed(_apply(tree, op), theme_of)
    return (
        "improves" if after > before + 1e-9 else "worsens" if after < before - 1e-9 else "neutral"
    )


def score(folder: Path, answer: Path | None = None) -> str:
    """The report of one answer (also written to ``<folder>/score.md``)."""
    from cartolex.project.themes_handoff import parse_answer, tree_of

    lab = json.loads((folder / "lab.json").read_text(encoding="utf-8"))
    record = json.loads((folder / "bundle" / "bundle.json").read_text(encoding="utf-8"))
    tree = tree_of(record)
    text = (answer or folder / "bundle" / "answer.txt").read_text(encoding="utf-8")
    parsed = parse_answer(text, tree, language=record.get("language") or "en")
    gold = truth([*tree.keywords, *tree.set_aside], lab["size"], lab["seed"])
    rows: dict[str, Counter[str]] = defaultdict(Counter)
    verdicts = []
    for item in parsed.items:
        v = judge(item, tree, gold)
        rows[item.verb][v] += 1
        verdicts.append((item, v))
    total = Counter(v for _, v in verdicts)
    applicable = [i for i in parsed.items if not i.refused]
    after = tree
    for item in applicable:
        try:
            after = _apply(after, item.op)
        except ValueError:
            continue
    judged = sum(total[v] for v in ("improves", "neutral", "worsens"))
    lines = [
        "# The theme handoff: a blind judge's answer, scored",
        "",
        f"World {lab['size']}, seed {lab['seed']}: {len(tree.nodes)} nodes, "
        f"{len(tree.keywords)} keywords, {len(gold['theme'])} with a known theme; "
        f"bundle of about {lab['tokens']} tokens.",
        "",
        f"Answer: {parsed.lines} lines read as operations, {len(parsed.items)} operations, "
        f"{len(parsed.unreadable)} unreadable, {parsed.ignored} other lines.",
        "",
        "| action | proposed | improves | neutral | worsens | refused |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for verb in sorted(rows):
        c = rows[verb]
        lines.append(
            f"| {verb} | {sum(c.values())} | {c['improves']} | {c['neutral']} | "
            f"{c['worsens']} | {c['refused']} |"
        )
    lines += [
        f"| all | {len(parsed.items)} | {total['improves']} | {total['neutral']} | "
        f"{total['worsens']} | {total['refused']} |",
        "",
        f"Improve the tree: {total['improves']} of {judged} applicable operations "
        f"({100 * total['improves'] / max(judged, 1):.0f} %); worsen it: {total['worsens']} "
        f"({100 * total['worsens'] / max(judged, 1):.0f} %).",
        "",
        f"B-cubed F1 of the top level against the themes: {bcubed(tree, gold['theme']):.3f} "
        f"before, {bcubed(after, gold['theme']):.3f} after every applicable operation, in order "
        f"({len(after.nodes)} nodes left).",
        "",
        "## Each operation",
        "",
    ]
    for item, v in verdicts:
        lines.append(
            f"- {v}: `{item.text}`" + (f" — refused: {item.refused}" if item.refused else "")
        )
    for u in parsed.unreadable:
        lines.append(f"- unreadable ({u.problem}): `{u.text}`")
    report = "\n".join(lines) + "\n"
    (folder / "score.md").write_text(report, encoding="utf-8")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    sub = parser.add_subparsers(dest="command", required=True)
    b = sub.add_parser("bundle", help="build the demo world and write the theme handoff")
    b.add_argument("folder", type=Path)
    s = sub.add_parser("score", help="score a judge's answer (bundle/answer.txt)")
    s.add_argument("folder", type=Path)
    s.add_argument("--answer", type=Path, default=None)
    args = parser.parse_args(argv)
    if args.command == "bundle":
        out = bundle(args.folder)
        print(f"bundle written to {out}: give a judge prompt.txt and tree.txt only")
    else:
        print(score(args.folder, args.answer))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
