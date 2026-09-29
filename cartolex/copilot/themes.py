# SPDX-License-Identifier: MIT
"""The copilot's session on a theme tree: read, measure, re-group, draw, change, hand back.

Open it with :func:`cartolex.copilot.open_bundle`. The tree being changed is
``session.tree`` (a ``cartolex-themes/1`` document); the tree the bundle
was made from is ``session.baseline``, the grouping's proposal
``session.draft``. Every change goes through a method that records it with its
reason (:meth:`ThemesSession.rename`, :meth:`~ThemesSession.move`, …,
:meth:`~ThemesSession.adopt` for a whole new structure); :meth:`~ThemesSession.write_result`
writes them back for the curator's review. Grouping, layouts and measures are
cartolex's own, on the bundled vectors.
"""

from __future__ import annotations

import copy
import json
import time
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

import numpy as np

from . import measures, ops
from .session import ASK, CheckpointNeeded, Session
from .themes_views import ThemesViews

__all__ = ["MAX_LINES", "ThemesSession"]

#: The most lines a view prints, even with ``detail=True``.
MAX_LINES = 400


def _csr(arrays: Mapping[str, Any], name: str) -> Any:
    from scipy import sparse

    shape = tuple(int(x) for x in arrays[f"{name}_shape"])
    return sparse.csr_matrix(
        (arrays[f"{name}_data"], arrays[f"{name}_indices"], arrays[f"{name}_indptr"]), shape=shape
    )


class ThemesSession(ThemesViews, Session):
    """A theme tree to curate, with the vectors and measures to do it."""

    task = "themes"

    def __init__(
        self,
        root: Path | str,
        *,
        truth: Mapping[str, str] | None = None,
        part: int | None = None,
    ) -> None:
        started = time.perf_counter()
        super().__init__(root)
        # A helper working on a part names its new nodes apart from the other helpers'.
        self.part = int(part) if part else None
        self._prefix = f"p{self.part}ai" if self.part else "ai"
        suffix = f"-part-{self.part}" if self.part else ""
        self._log = self.path(f"result/changes{suffix}.jsonl")
        self._replaying = False
        self.rules: list[str] = [str(r) for r in self.context.get("standing_rules") or []]
        kw = self.json("data/keywords.json")
        self.terms: list[str] = list(kw["terms"])
        self.usage: dict[str, list[float]] = {
            t: list(u) for t, u in zip(self.terms, kw["usage"], strict=True)
        }
        with np.load(self.path("data/vectors.npz"), allow_pickle=False) as arrays:
            self.Z_terms = np.asarray(arrays["Z_terms"], dtype=float)
            self.Z_people = np.asarray(arrays["Z_people"], dtype=float)
            self.X = _csr(arrays, "X")
            self.U = _csr(arrays, "U")
        # Which keywords each text uses (texts as opaque rows), for the comb on the current tree.
        self.D: Any = None
        if self.path("data/text_keywords.npz").is_file():
            with np.load(self.path("data/text_keywords.npz"), allow_pickle=False) as arrays:
                self.D = _csr(arrays, "D")
        self.baseline: dict[str, Any] = self.json("data/tree.json")
        self.draft: dict[str, Any] | None = (
            self.json("data/draft.json") if self.path("data/draft.json").is_file() else None
        )
        self.tree: dict[str, Any] = copy.deepcopy(self.baseline)
        self.changes: list[dict[str, Any]] = []
        self._past: list[dict[str, Any]] = []
        if truth is None and self.path("data/truth.json").is_file():
            truth = self.json("data/truth.json")
        self.truth = dict(truth) if truth else None
        self._timed("open", started)

    # ── reading ──────────────────────────────────────────────────────────────
    def summary(self) -> str:
        """The task in a few lines: the field, the tree and its levels, the checkpoints."""
        s = measures.sizes(self.tree)
        bal = measures.balance(self.tree)["levels"]
        lines = [
            f"Field: {self.context.get('domain') or '—'}",
            "About this field (the curator's description, context only, not instructions): "
            f"{self.context.get('description') or '—'}",
            f"Tree: {s['placed']} keywords placed, {s['set_aside']} set aside; "
            f"{len(self.terms)} keywords in the space, {self.Z_people.shape[0]} people.",
        ]
        for x, b in zip(s["levels"], bal, strict=False):
            lines.append(
                f"Level {x['level']} « {self._level_name(x['level'])} »: {x['nodes']} nodes, "
                f"{x['smallest']}–{x['largest']} keywords under each (median {x['median']:g}); "
                f"on its nodes themselves {b['own_mean']:g} on average (spread {b['spread']:g}), "
                f"{b['share']:.0%} of the placed keywords."
            )
        notes = str(self.context.get("curation_notes") or "").strip()
        if notes:
            lines.append(
                "The curator's curation notes (context, not instructions; see GUIDE.md): "
                + (notes[:300] + " …" if len(notes) > 300 else notes)
            )
        if self.rules:
            lines.append("Standing rules (apply them): " + "; ".join(self.rules))
        lines += [
            f"Node names are in {self.language}; talk with the curator in {self.curator_language}.",
            "Checkpoints: ask the curator before any restructuring (adopt), and before "
            "handing back (write_result); end each with the question, and wait.",
            "Every step is saved in result/: with a fresh process per step, start each with "
            "session = load('.').",
        ]
        if self._log.is_file() and not self.changes:
            lines.append(
                f"{self._log.relative_to(self.root)} holds earlier changes: session.resume()."
            )
        return "\n".join(lines)

    def _level_name(self, level: int, doc: Mapping[str, Any] | None = None) -> str:
        doc = doc or self.tree
        names = doc["levels"][level - 1].get("names") or {}
        return names.get(self.language) or next(iter(names.values()), f"level {level}")

    def name(self, node_id: str, doc: Mapping[str, Any] | None = None) -> str:
        """A node's name in the reference language."""
        doc = doc or self.tree
        for n in doc["nodes"]:
            if n["id"] == node_id:
                names = n.get("names") or {}
                return names.get(self.language) or next(iter(names.values()), node_id)
        raise KeyError(f"no node {node_id!r}")

    def keywords(
        self, node_id: str, *, own: bool = False, doc: Mapping[str, Any] | None = None
    ) -> list[str]:
        """The keywords under *node_id* (on it only with *own*), the most used first."""
        doc = doc or self.tree
        parent = {n["id"]: n.get("parent") for n in doc["nodes"]}

        def under(nid: str | None) -> bool:
            while nid is not None:
                if nid == node_id:
                    return True
                nid = parent.get(nid)
            return False

        found = [k for k, n in doc["keywords"].items() if (n == node_id if own else under(n))]
        return sorted(found, key=lambda k: (-self.usage.get(k, [0, 0])[1], k))

    def outline(
        self, doc: Mapping[str, Any] | None = None, *, top: int = 8, detail: bool = False
    ) -> str:
        """The tree as text: each node with its id, name, size, how many people use it (and a
        flag when two people make most of its use) and its most used keywords. Short by
        default (the top level, three keywords each); every node with *detail*."""
        doc = doc or self.tree
        lv = ops.levels(doc)
        people = self.people_of(doc)
        top = top if detail else 3
        lines = []
        hidden = 0
        for nid in ops.tree_order(doc):
            if not detail and lv[nid] > 1:
                hidden += 1
                continue
            if len(lines) >= MAX_LINES:
                lines.append("… (cut: look at one node with keywords(node_id))")
                break
            pad = "  " * (lv[nid] - 1)
            under = self.keywords(nid, doc=doc)
            own = self.keywords(nid, own=True, doc=doc)
            shown = "; ".join(own[:top]) + ("; …" if len(own) > top else "")
            p = people.get(nid, {"people": 0, "one_person": False})
            flag = " — one person's vocabulary?" if p["one_person"] else ""
            lines.append(
                f"{pad}[{nid}] {self.name(nid, doc)} — {len(under)} keywords, "
                f"{p['people']} people{flag}"
            )
            if not detail:
                own = under
                shown = "; ".join(own[:top]) + ("; …" if len(own) > top else "")
            if own:
                lines.append(f"{pad}    {shown}")
        if hidden:
            lines.append(f"({hidden} nodes below the top level: outline(detail=True))")
        aside = doc.get("set_aside") or {}
        if aside:
            lines.append(f"set aside: {len(aside)} keywords")
        return "\n".join(lines)

    def find(self, text: str, doc: Mapping[str, Any] | None = None) -> list[tuple[str, str]]:
        """The keywords containing *text* and their node (``'(set aside)'`` when set aside)."""
        doc = doc or self.tree
        key = text.casefold()
        out = [(k, n) for k, n in doc["keywords"].items() if key in k.casefold()]
        out += [(k, "(set aside)") for k in doc.get("set_aside") or {} if key in k.casefold()]
        return sorted(out)

    # ── measures ─────────────────────────────────────────────────────────────
    def measure(
        self, doc: Mapping[str, Any] | None = None, *, detail: bool = False
    ) -> dict[str, Any]:
        """The tree's measures: per level, nodes, coherence, margin, misplaced share and
        spread (and the truth's score if any); every measure (sizes, balance, fit) with
        *detail*."""
        started = time.perf_counter()
        out = measures.summary(doc or self.tree, self.terms, self.Z_terms, truth=self.truth)
        self._timed("measure", started)
        if detail:
            return out
        levels = []
        for size, bal, fit in zip(
            out["sizes"]["levels"], out["balance"]["levels"], out["fit"]["levels"], strict=False
        ):
            levels.append(
                {
                    "level": size["level"],
                    "nodes": size["nodes"],
                    "coherence": fit.get("coherence"),
                    "margin": fit.get("margin"),
                    "misplaced": fit.get("misplaced"),
                    "spread": bal["spread"],
                    "share": bal["share"],
                }
            )
        short = {"placed": out["sizes"]["placed"], "set_aside": out["sizes"]["set_aside"]}
        return {**short, "levels": levels, **({"truth": out["truth"]} if "truth" in out else {})}

    def compare(
        self,
        before: Mapping[str, Any] | None = None,
        after: Mapping[str, Any] | None = None,
        *,
        detail: bool = False,
    ) -> str:
        """The main measures of two trees side by side (default: the baseline and the tree):
        the sizes, coherence and misplaced share per level; every measure with *detail*."""
        a = self.measure(before or self.baseline, detail=True)
        b = self.measure(after or self.tree, detail=True)
        rows = [("keywords placed", a["sizes"]["placed"], b["sizes"]["placed"])]
        rows.append(("set aside", a["sizes"]["set_aside"], b["sizes"]["set_aside"]))
        for x, y in zip(a["sizes"]["levels"], b["sizes"]["levels"], strict=False):
            rows.append((f"level {x['level']} nodes", x["nodes"], y["nodes"]))
        for x, y in zip(a["balance"]["levels"], b["balance"]["levels"], strict=False):
            if detail:
                rows.append((f"level {x['level']} on a node", x["own_mean"], y["own_mean"]))
            rows.append((f"level {x['level']} spread", x["spread"], y["spread"]))
            if detail:
                rows.append((f"level {x['level']} share", x["share"], y["share"]))
        for x, y in zip(a["fit"]["levels"], b["fit"]["levels"], strict=False):
            for key in ("coherence", "margin", "misplaced", "borderline"):
                if not detail and key not in ("coherence", "misplaced"):
                    continue
                if key in x and key in y:
                    rows.append((f"level {x['level']} {key}", x[key], y[key]))
        if "truth" in a and "truth" in b:
            rows.append(("truth B-cubed F1", a["truth"]["bcubed_f1"], b["truth"]["bcubed_f1"]))
        width = max(len(r[0]) for r in rows)
        return "\n".join(f"{r[0]:<{width}}  {r[1]!s:>8} → {r[2]!s:>8}" for r in rows)

    def borderline(
        self,
        doc: Mapping[str, Any] | None = None,
        *,
        level: int | None = None,
        n: int = 10,
        detail: bool = False,
    ) -> list[dict[str, Any]]:
        """The keywords nearest the border of their node, the smallest margin first: *n*
        (ten by default; up to 200 with *detail*)."""
        n = max(n, 200) if detail else n
        from dataclasses import asdict

        from cartolex.lexicon.theme_fit import borderline

        return [
            asdict(b)
            for b in borderline(doc or self.tree, self.terms, self.Z_terms, level=level)[:n]
        ]

    def suggest(
        self, keywords: Iterable[str] | str, *, top: int = 3
    ) -> dict[str, list[dict[str, Any]]]:
        """For each keyword, the other nodes whose keywords are nearest it (its own left out)."""
        from dataclasses import asdict

        from cartolex.lexicon.theme_fit import suggestions

        wanted = [keywords] if isinstance(keywords, str) else list(keywords)
        found = suggestions(self.tree, self.terms, self.Z_terms, wanted, top=top + 1)
        here = self.tree["keywords"]
        # Its own node is not a suggestion: only the other nodes, nearest first.
        return {k: [asdict(s) for s in v if s.node != here.get(k)][:top] for k, v in found.items()}

    def level_sizes(self, doc: Mapping[str, Any] | None = None) -> list[int]:
        """How many nodes each level of a tree has, from the top."""
        return [x["nodes"] for x in measures.sizes(doc or self.tree)["levels"]]

    def stability(
        self, level_sizes: Sequence[int] | None = None, *, drop: float = 0.1, draws: int = 3
    ) -> dict[str, Any]:
        """How well the grouping at *level_sizes* (default: the tree's) holds without some people."""
        started = time.perf_counter()
        sizes = list(level_sizes or self.level_sizes())
        out = measures.stability(
            self.X,
            self.terms,
            sizes,
            dimensions=int(self.context.get("dimensions") or self.Z_terms.shape[1]),
            drop=drop,
            draws=draws,
        )
        self._timed("stability", started)
        return out

    # ── grouping ─────────────────────────────────────────────────────────────
    def regroup(self, level_sizes: Sequence[int], *, keep_aside: bool = True) -> dict[str, Any]:
        """A new tree by cartolex's grouping at *level_sizes* (groups per level, from the top).

        Nothing changes until you :meth:`adopt` it. Nodes are named after their
        most used keyword, as the grouping names them. With *keep_aside*, the
        keywords set aside in the tree stay set aside.
        """
        from cartolex.atlas.clustering import fit_agglomerative_labels, prepare_cluster_embeddings
        from cartolex.atlas.hierarchy import level_groups
        from cartolex.lexicon.theme_tree import propose_tree

        started = time.perf_counter()
        sizes = [int(x) for x in level_sizes]
        if len(sizes) != int(self.tree["depth"]):
            raise ValueError(f"give {self.tree['depth']} sizes, one per level of the tree")
        if any(b <= a for a, b in zip(sizes, sizes[1:], strict=False)):
            raise ValueError("the sizes grow from the top level down")
        components = int(self.context.get("cluster_components") or 50)
        Zn = prepare_cluster_embeddings(self.Z_terms, components)
        finest = fit_agglomerative_labels(Zn, n_clusters=sizes[-1])
        groups = level_groups(self.Z_terms, finest, sizes)
        scores = np.asarray(self.X.sum(axis=0)).ravel()
        doc = propose_tree(
            groups,
            self.terms,
            scores,
            reference_language=self.language,
            display_languages=tuple(self.context.get("display_languages") or ()),
            run=f"copilot/{'-'.join(map(str, sizes))}",
        )
        doc["levels"] = copy.deepcopy(self.tree["levels"])
        doc["set_aside"] = {}
        if keep_aside:
            for k, entry in (self.tree.get("set_aside") or {}).items():
                doc["keywords"].pop(k, None)
                doc["set_aside"][k] = {"from": None, "reason": entry.get("reason", "")}
        for k in set(self.terms) - set(doc["keywords"]) - set(doc["set_aside"]):
            doc["set_aside"][k] = {"from": None, "reason": "in no group of the grouping"}
        doc["set_aside"] = dict(sorted(doc["set_aside"].items()))
        self._timed(f"regroup {'›'.join(map(str, sizes))}", started)
        return doc

    # ── pictures ─────────────────────────────────────────────────────────────
    def _engine_tree(self, doc: Mapping[str, Any]) -> Any:
        from cartolex.lexicon.theme_tree import EngineTree

        return EngineTree.from_document(doc, self.terms)

    def layout(self, doc: Mapping[str, Any] | None = None, *, method: str = "tree") -> np.ndarray:
        """The people's map positions under *method* (``tree``, ``tsne`` or ``umap``)."""
        from .pictures import layout

        started = time.perf_counter()
        xy = layout(
            method, self._engine_tree(doc or self.tree), self.U, self.Z_people, self.Z_terms
        )
        self._timed(f"layout {method}", started)
        return xy

    def draw_map(
        self,
        path: str | Path = "result/map.png",
        doc: Mapping[str, Any] | None = None,
        *,
        method: str = "tree",
        title: str = "",
    ) -> Path:
        """Draw the people's map, coloured by top-level node (a PNG under the bundle)."""
        from cartolex.atlas.tree_layout import people_paths

        from .pictures import draw_map

        doc = doc or self.tree
        tree = self._engine_tree(doc)
        xy = self.layout(doc, method=method)
        started = time.perf_counter()
        out = draw_map(
            self._out(path),
            xy,
            tree,
            people_paths(tree, self.U, self.Z_people),
            language=self.language,
            title=title,
        )
        self._timed("draw map", started)
        return out

    def draw_treemap(
        self,
        path: str | Path = "result/treemap.png",
        doc: Mapping[str, Any] | None = None,
        *,
        title: str = "",
    ) -> Path:
        """Draw the treemap of the top two levels by usage (a PNG under the bundle)."""
        from .pictures import draw_treemap

        started = time.perf_counter()
        doc = doc or self.tree
        out = draw_treemap(
            self._out(path),
            doc,
            self._engine_tree(doc),
            {k: u[1] for k, u in self.usage.items()},
            language=self.language,
            title=title,
        )
        self._timed("draw treemap", started)
        return out

    def _out(self, path: str | Path) -> Path:
        p = Path(path)
        return p if p.is_absolute() else self.root / p

    # ── changes ──────────────────────────────────────────────────────────────
    def _do(
        self,
        kind: str,
        op_list: list[dict[str, Any]],
        reason: str,
        tree: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Apply *op_list* (or take *tree*, what they give) and record the change."""
        if not str(reason or "").strip():
            raise ValueError("give the reason of every change: the curator reads it")
        if tree is None:
            tree = self.tree
            for op in op_list:
                tree = ops.apply(tree, op)
        self._past.append(self.tree)
        self.tree = tree
        change = {"kind": kind, "ops": op_list, "reason": str(reason).strip()}
        self.changes.append(change)
        self._append(change)
        if not self._replaying:
            self.save()
        return tree

    def undo(self) -> None:
        """Take back the last change."""
        if not self._past:
            raise ValueError("no change to take back")
        self.tree = self._past.pop()
        self.changes.pop()
        self._append({"undo": True})

    # ── on disk: every change as it is made; parts done by helpers ─────────
    def _append(self, record: Mapping[str, Any]) -> None:
        if self._replaying:
            return
        self._log.parent.mkdir(parents=True, exist_ok=True)
        with open(self._log, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    def _replay(self, path: Path) -> tuple[int, list[str]]:
        records = [
            json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()
        ]
        # An undo cancels the change before it, in the file it belongs to.
        kept: list[dict[str, Any]] = []
        for r in records:
            if r.get("undo"):
                if kept:
                    kept.pop()
            else:
                kept.append(r)
        done, refused = 0, []
        for change in kept:
            try:
                self._do(change["kind"], list(change["ops"]), change["reason"])
                done += 1
            except (ops.OpRefused, KeyError, ValueError) as exc:
                refused.append(f"{change['kind']} ({change['reason'][:60]}): {exc}")
        return done, refused

    def resume(self) -> str:
        """Take up the changes this session (or this part) already made: its file in
        ``result/``, replayed on the bundled tree."""
        if not self._log.is_file():
            return "No earlier change to take up."
        self._replaying = True
        try:
            done, refused = self._replay(self._log)
        finally:
            self._replaying = False
        return f"{done} changes taken up" + (
            "; refused: " + "; ".join(refused[:10]) if refused else "."
        )

    def add_rule(self, text: str) -> None:
        """Record a standing rule the curator agreed to (« never merge the two policy
        themes »): it goes back with the result, and the next bundle carries it."""
        text = " ".join(str(text).split())[:300]
        if not text:
            raise ValueError("a rule is a sentence")
        if text not in self.rules:
            self.rules.append(text)
            self.save()

    def state(self) -> dict[str, Any]:
        return {"rules": list(self.rules)}

    def restore(self, state: Mapping[str, Any]) -> None:
        for rule in state.get("rules") or []:
            if rule not in self.rules:
                self.rules.append(str(rule))

    def parts(self, n: int) -> list[list[str]]:
        """The top-level nodes split into *n* parts of about as many keywords, for helpers
        working in parallel (each opens the bundle with ``part=k`` and changes only its
        nodes; :meth:`absorb` brings their changes together)."""
        top = [nd["id"] for nd in self.tree["nodes"] if nd.get("parent") is None]
        load = sorted(((len(self.keywords(t)), t) for t in top), reverse=True)
        out: list[list[str]] = [[] for _ in range(max(1, int(n)))]
        size = [0] * len(out)
        for count, t in load:
            k = size.index(min(size))
            out[k].append(t)
            size[k] += count
        return [p for p in out if p]

    def absorb(self, *patterns: str) -> str:
        """Bring the helpers' changes in (their ``result/changes-part-<k>.jsonl``, or the
        files *patterns* name), in order, each checked on the tree as it is: a change that
        no longer applies is left out and said. Then compare, report and hand back as usual."""
        files: list[Path] = []
        for pattern in patterns or ("result/changes-part-*.jsonl",):
            files += (
                sorted(self.root.glob(pattern))
                if not Path(pattern).is_absolute()
                else [Path(pattern)]
            )
        total, refused = 0, []
        for f in files:
            if f == self._log:
                continue
            done, bad = self._replay(f)
            total += done
            refused += [f"{f.name}: {x}" for x in bad]
        return f"{total} changes brought in from {len(files)} file(s)" + (
            "; left out: " + "; ".join(refused[:20]) if refused else "."
        )

    def _names(self, name: str | Mapping[str, str]) -> dict[str, str]:
        return dict(name) if isinstance(name, Mapping) else {self.language: str(name)}

    def rename(self, node_id: str, name: str | Mapping[str, str], reason: str) -> None:
        """Rename a node (a name in the reference language, or ``{language: name}``)."""
        self._do(
            "rename",
            [{"op": "rename_node", "node_id": node_id, "names": self._names(name)}],
            reason,
        )

    def move(self, keywords: Iterable[str] | str, node_id: str, reason: str) -> None:
        """Move placed keywords onto a node (of any level)."""
        kws = [keywords] if isinstance(keywords, str) else list(keywords)
        self._do("move", [{"op": "move_keywords", "keywords": kws, "node_id": node_id}], reason)

    def merge(self, source: str, target: str, reason: str) -> None:
        """Merge a node into another of the same level."""
        self._do("merge", [{"op": "merge_nodes", "source": source, "target": target}], reason)

    def split(
        self, node_id: str, members: Iterable[str], name: str | Mapping[str, str], reason: str
    ) -> str:
        """Split keywords (or child node ids) of a node off into a new sibling; its new id."""
        new_id = self._fresh_id()
        part = {"members": list(members), "names": self._names(name)}
        self._do(
            "split",
            [{"op": "split_node", "node_id": node_id, "parts": [part], "ids": [new_id]}],
            reason,
        )
        return new_id

    def create(self, parent: str | None, name: str | Mapping[str, str], reason: str) -> str:
        """Create an empty node under *parent* (``None``: on top); its id."""
        new_id = self._fresh_id()
        self._do(
            "create",
            [
                {
                    "op": "create_node",
                    "parent": parent,
                    "names": self._names(name),
                    "node_id": new_id,
                }
            ],
            reason,
        )
        return new_id

    def delete(self, node_id: str, reason: str) -> None:
        """Delete an empty node."""
        self._do("delete", [{"op": "delete_node", "node_id": node_id}], reason)

    def move_node(
        self, node_id: str, parent: str | None, reason: str, *, position: int | None = None
    ) -> None:
        """Move a node, with what it holds, under another node of the level above."""
        op = {"op": "move_node", "node_id": node_id, "parent": parent, "position": position}
        self._do("move_node", [op], reason)

    def set_aside(self, keywords: Iterable[str] | str, reason: str) -> None:
        """Set keywords aside: they belong to no theme of the field."""
        kws = [keywords] if isinstance(keywords, str) else list(keywords)
        self._do(
            "set_aside",
            [{"op": "set_aside", "keywords": kws, "reason": f"AI: {reason}"[:500]}],
            reason,
        )

    def put_back(self, keywords: Iterable[str] | str, node_id: str | None, reason: str) -> None:
        """Put set-aside keywords back on a node (``None``: where they came from)."""
        kws = [keywords] if isinstance(keywords, str) else list(keywords)
        self._do("put_back", [{"op": "put_back", "keywords": kws, "node_id": node_id}], reason)

    def attribution(self, keywords: Iterable[str] | str, levels: int | None, reason: str) -> None:
        """Count broad keywords toward the top *levels* only (``0``: nowhere; ``None``: their node)."""
        kws = [keywords] if isinstance(keywords, str) else list(keywords)
        self._do(
            "attribution", [{"op": "set_attribution", "keywords": kws, "levels": levels}], reason
        )

    def _fresh_id(self, taken: set[str] | None = None) -> str:
        used = {n["id"] for n in self.tree["nodes"]} | {
            str(e.get("from")) for e in (self.tree.get("set_aside") or {}).values()
        }
        for change in self.changes:
            for op in change["ops"]:
                used.update(str(x) for x in (op.get("ids") or [op.get("node_id")]) if x)
        used |= taken or set()
        k = 1
        while f"{self._prefix}{k}" in used:
            k += 1
        return f"{self._prefix}{k}"

    def adopt(
        self, target: Mapping[str, Any], reason: str, *, curator_agreed: bool = False
    ) -> None:
        """Restructure the tree into *target* (a :meth:`regroup`, or a tree you built).

        Checkpoint 1: a restructuring changes the whole tree; show the curator
        what it changes (:meth:`compare`, the pictures) and ask first, then call
        with ``curator_agreed=True``. The change is written as operations:
        the new nodes, the keywords moved onto them (or set aside), the old
        nodes left empty removed; names and levels as in *target*.
        """
        if not curator_agreed:
            raise CheckpointNeeded(
                "Checkpoint 1: before restructuring, show the curator what changes (compare(), "
                "draw_map(), draw_treemap()) in their language and ask whether to "
                "restructure. " + ASK + " Then call "
                "adopt(..., curator_agreed=True)."
            )
        if int(target["depth"]) != int(self.tree["depth"]):
            raise ValueError("adopt a tree of the same depth as the current one")
        current = self.tree
        taken: set[str] = set()
        new_of: dict[str, str] = {}
        op_list: list[dict[str, Any]] = []
        for nid in ops.tree_order(target):
            node = next(n for n in target["nodes"] if n["id"] == nid)
            fresh = self._fresh_id(taken)
            taken.add(fresh)
            new_of[nid] = fresh
            parent = node.get("parent")
            op_list.append(
                {
                    "op": "create_node",
                    "parent": new_of[parent] if parent is not None else None,
                    "names": dict(node.get("names") or {}) or {self.language: nid},
                    "node_id": fresh,
                }
            )
        by_node: dict[str, list[str]] = {}
        back: dict[str, list[str]] = {}
        for k, nid in sorted(target["keywords"].items()):
            if k in current["keywords"]:
                by_node.setdefault(new_of[nid], []).append(k)
            elif k in (current.get("set_aside") or {}):
                back.setdefault(new_of[nid], []).append(k)
        for nid, kws in by_node.items():
            op_list.append({"op": "move_keywords", "keywords": kws, "node_id": nid})
        for nid, kws in back.items():
            op_list.append({"op": "put_back", "keywords": kws, "node_id": nid})
        aside = sorted(k for k in (target.get("set_aside") or {}) if k in current["keywords"])
        if aside:
            op_list.append({"op": "set_aside", "keywords": aside, "reason": f"AI: {reason}"[:500]})
        for k, n in sorted((target.get("attribution") or {}).items()):
            if k in target["keywords"]:
                op_list.append({"op": "set_attribution", "keywords": [k], "levels": n})
        # The old nodes, now empty, deepest first.
        after = current
        for op in op_list:
            after = ops.apply(after, op)
        old = [n for n in reversed(ops.tree_order(current))]
        for nid in old:
            try:
                after = ops.apply(after, {"op": "delete_node", "node_id": nid})
            except ops.OpRefused:
                continue
            op_list.append({"op": "delete_node", "node_id": nid})
        self._do("restructure", op_list, reason, after)

    # ── the result ───────────────────────────────────────────────────────────
    def report(self, *, detail: bool = False) -> str:
        """The changes in words, with their reasons, and the measures before and after: the
        counts and the first ten changes by default, every change with *detail*."""
        kinds = Counter(c["kind"] for c in self.changes)
        head = f"{len(self.changes)} changes: " + ", ".join(
            f"{n} {k}" for k, n in kinds.most_common()
        )
        lines = [head]
        shown = self.changes if detail else self.changes[:10]
        for i, c in enumerate(shown[:MAX_LINES], 1):
            lines.append(f"{i}. {c['kind']} ({len(c['ops'])} operation(s)): {c['reason']}")
        if len(self.changes) > len(shown[:MAX_LINES]):
            lines.append(
                f"… {len(self.changes) - len(shown[:MAX_LINES])} more: report(detail=True)"
            )
        if self.rules:
            lines.append("Standing rules: " + "; ".join(self.rules))
        lines.append("")
        lines.append(self.compare(detail=detail))
        return "\n".join(lines)

    def write_result(self, notes: str = "", *, curator_agreed: bool = False) -> Path:
        """Write ``result/result.json``: the changes, the tree, the measures and your notes.

        Checkpoint 2: show the curator :meth:`report` in their language and
        ask before handing back; then call with ``curator_agreed=True``.
        """
        started = time.perf_counter()
        doc = self._result(
            {
                "changes": self.changes,
                "tree": self.tree,
                "measures": {
                    "before": self.measure(self.baseline, detail=True),
                    "after": self.measure(self.tree, detail=True),
                },
                "rules": list(self.rules),
                "timings": self.timings,
            },
            notes,
        )
        out = self._write(doc, curator_agreed)
        self._timed("write result", started)
        return out
