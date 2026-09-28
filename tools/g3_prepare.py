# SPDX-License-Identifier: MIT
"""Prepare the demo project of a usability session of the theme editor.

Usage::

    python tools/g3_prepare.py ~/usability/g3-demo
    python tools/g3_prepare.py ~/usability/g3-demo --reset ~/usability/p1

The first form writes the S demo world (seed 0) as a project at depth 2
(themes and topics) in the folder, builds it and saves the grouping's
proposal as the curated tree. It then fills the « To check » queue as a
rebase would: eight keywords marked « to check », four of them on the node
their theme holds, four on another one. It prints what the session's tasks
name, from the demo world's known themes (as ``tools/themes_handoff_lab.py``
computes them): a theme to rename, a misplaced keyword to move, two themes to
merge, one to split.

``--reset TARGET`` copies the prepared project to TARGET (replaced): one fresh
copy per participant, so every session starts from the same state.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

#: Keywords the « To check » queue holds, half of them on a wrong node.
QUEUE = 8


def _name(tree, node_id: str) -> str:
    node = next(n for n in tree.nodes if n.id == node_id)
    return node.names.get("en") or next(iter(node.names.values()), node_id)


def _tasks(tree, theme_of: dict[str, str], usage: dict[str, list[float]]) -> dict:
    """What the tasks name: the nodes of each theme, a misplaced keyword, merges and a split."""
    parent = {n.id: n.parent for n in tree.nodes}

    def top(nid: str) -> str:
        while parent.get(nid) is not None:
            nid = parent[nid]  # type: ignore[assignment]
        return nid

    by_top: dict[str, Counter[str]] = {}
    for k, nid in tree.keywords.items():
        if k in theme_of:
            by_top.setdefault(top(nid), Counter())[theme_of[k]] += 1
    main = {t: c.most_common(1)[0][0] for t, c in by_top.items()}
    home: dict[str, str] = {}  # theme -> the top node holding most of it
    for theme in set(theme_of.values()):
        counts = {t: c[theme] for t, c in by_top.items()}
        home[theme] = max(counts, key=lambda t: (counts[t], t))
    misplaced = sorted(
        (k for k, nid in tree.keywords.items() if k in theme_of and top(nid) != home[theme_of[k]]),
        key=lambda k: (-usage.get(k, [0, 0])[1], k),
    )
    pairs = [
        (a, b)
        for a in sorted(main)
        for b in sorted(main)
        if a < b and main[a] == main[b] and home[main[a]] in (a, b)
    ]
    mixed = sorted(
        (
            t
            for t, c in by_top.items()
            if len(c) > 1 and c.most_common(2)[1][1] >= 0.3 * sum(c.values())
        ),
        key=lambda t: -sum(by_top[t].values()),
    )
    renamed = max(by_top, key=lambda t: sum(by_top[t].values()))
    return {
        "rename": {"node": renamed, "name": _name(tree, renamed), "theme": main[renamed]},
        "move": [
            {
                "keyword": k,
                "from": _name(tree, top(tree.keywords[k])),
                "to": _name(tree, home[theme_of[k]]),
            }
            for k in misplaced[:3]
        ],
        "merge": [
            {"a": _name(tree, a), "b": _name(tree, b), "theme": main[a]} for a, b in pairs[:3]
        ],
        "split": [
            {"node": _name(tree, t), "themes": [th for th, _ in by_top[t].most_common(2)]}
            for t in mixed[:2]
        ],
        "home": home,
        "misplaced": misplaced,
    }


def prepare(folder: Path) -> dict:
    """Write, build and curate the session's project; return what the tasks name."""
    from themes_handoff_lab import _usage, truth

    from cartolex.cli import main as cli
    from cartolex.demo import generate
    from cartolex.demo.project import write_project
    from cartolex.project import Project
    from cartolex.project.models import ThemesFile
    from cartolex.project.themes import move_keywords, set_review
    from cartolex.project.themes_versions import read_themes, save_themes

    if folder.exists():
        raise SystemExit(f"{folder} exists: choose a new folder")
    write_project(generate("S", 0), folder).close()
    assert cli(["params", str(folder), "--set", "pinned_year=2026", "themes.group.depth=2"]) == 0
    assert cli(["build", str(folder)]) == 0
    draft = folder / "derived" / "themes.group" / "themes_draft.json"
    tree = ThemesFile.model_validate(json.loads(draft.read_text(encoding="utf-8")))
    usage = _usage(folder)
    gold = truth([*tree.keywords, *tree.set_aside])
    facts = _tasks(tree, gold["theme"], usage)
    # The queue: keywords with a theme, spread over the tree, the misplaced ones kept for
    # the « move » task; half stay where their theme is, half go to another theme.
    task_keywords = {m["keyword"] for m in facts["move"]}
    candidates = sorted(
        (k for k in gold["theme"] if k in tree.keywords and k not in task_keywords),
        key=lambda k: (-usage.get(k, [0, 0])[1], k),
    )
    queue: list[str] = []
    seen_themes: set[str] = set()
    for k in candidates:
        if gold["theme"][k] not in seen_themes:
            queue.append(k)
            seen_themes.add(gold["theme"][k])
        if len(queue) == QUEUE:
            break
    tops = [n.id for n in tree.nodes if n.parent is None]
    wrong = queue[QUEUE // 2 :]
    for k in wrong:
        right = facts["home"][gold["theme"][k]]
        other = next(t for t in tops if t != right)
        leaf = next((n.id for n in tree.nodes if n.parent == other), other)
        tree = move_keywords(tree, [k], leaf).tree
    tree = set_review(tree, queue, "to_check").tree
    with Project.open(folder, write=True) as project:
        _, fp = read_themes(project)
        saved = save_themes(
            project, tree, expected=fp, action=f"prepare the To check queue ({QUEUE})"
        )
        assert saved.written
    return {**facts, "queue": queue, "wrong": wrong}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("folder", type=Path, help="the prepared project (created)")
    parser.add_argument("--reset", type=Path, default=None, help="copy it here (replaced)")
    args = parser.parse_args(argv)
    if args.reset is not None:
        if args.reset.exists():
            shutil.rmtree(args.reset)
        shutil.copytree(args.folder, args.reset, ignore=shutil.ignore_patterns(".lock"))
        print(f"fresh copy for a session: {args.reset}")
        return 0
    facts = prepare(args.folder)
    print(f"prepared: {args.folder}")
    print(
        f"task 1, rename: « {facts['rename']['name']} » (its keywords are of {facts['rename']['theme']})"
    )
    for m in facts["move"]:
        print(f"task 2, move: « {m['keyword']} » from « {m['from']} » to « {m['to']} »")
    for m in facts["merge"]:
        print(f"task 3, merge: « {m['a']} » and « {m['b']} » (both {m['theme']})")
    for m in facts["split"]:
        print(f"task 4, split: « {m['node']} » (mixes {', '.join(m['themes'])})")
    print(
        f"task 5, the To check queue: {len(facts['queue'])} keywords, on a wrong node: "
        + ", ".join(f"« {k} »" for k in facts["wrong"])
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
