# SPDX-License-Identifier: MIT
"""Measures for combing a theme tree, suggesting structure changes and naming nodes.

Usage (on a demo project built up to ``themes.group``, see ``--build``)::

    python tools/theme_comb_study.py build FOLDER --size S [--two-subjects 0.3] [--space text]
    python tools/theme_comb_study.py comb FOLDER --sizes 3,12,25
    python tools/theme_comb_study.py structure FOLDER --sizes 12,25
    python tools/theme_comb_study.py names FOLDER --sizes 3,12,25

The demo world's truth says what each keyword is: a term of one theme
(*specific*), a term of several themes (*cross-theme*), a method of every kind
of work, of the natural or social sciences, a driver of change or a study
setting (*broad*). Nothing here changes a default.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

YEAR = 2026


def build(size: str, root: Path, *, two_subjects: float = 0.0, space: str | None = None) -> None:
    import shutil

    from cartolex.build import build as run_build
    from cartolex.demo import generate
    from cartolex.demo.project import write_project

    if root.exists():
        shutil.rmtree(root)
    project = write_project(generate(size, 0, two_subjects=two_subjects), root)
    try:
        params, fp = project.read_params()
        stages: dict = {"corpus.assemble": {"recency_years": 0}}
        if space is not None:
            stages["themes.space"] = {"space_unit": space}
        project.save_params(
            params.model_copy(update={"stages": stages}), expected=fp, action="study"
        )
        result = run_build(project, ["themes.group"], year=YEAR, budget_mb=1e12)
        print(result.summary())
    finally:
        project.close()


class Space:
    """The keywords of a built demo project, their vectors, texts and truth."""

    def __init__(self, root: Path, two_subjects: float = 0.0) -> None:
        from cartolex.atlas.model_files import load_embeddings, load_lexical_data, load_vectorizer
        from cartolex.demo.writers import lexicon_truth
        from cartolex.lexicon.theme_comb import document_keywords

        derived = root / "derived"
        data = load_lexical_data(derived / "themes.space" / "models" / "lexical_data.json")
        emb = load_embeddings(derived / "themes.space" / "models" / "embeddings.json")
        self.terms = [str(t) for t in data.terms]
        self.Z = emb.Z_terms
        self.scores = np.asarray(data.X.sum(axis=0)).ravel()
        vec = load_vectorizer(derived / "keywords.build" / "models" / "tfidf_restricted.json")
        al = pd.read_csv(
            derived / "keywords.build" / "models" / "term_aliases.csv",
            dtype=str,
            keep_default_na=False,
        )
        alias = {
            a.strip().lower(): c.strip().lower()
            for a, c in zip(al["alias"], al["canonical"], strict=True)
        }
        manual = derived / "corpus.assemble" / "manual"
        index = pd.read_csv(manual / "index.csv")
        files = sorted(set(index["txt_path"]))  # a text written by several people, once
        texts = [(manual / p).read_text(encoding="utf-8") for p in files]
        self.D = document_keywords(texts, vectorizer=vec, alias_to_canon=alias, terms=self.terms)
        project = json.loads((root / "project.json").read_text(encoding="utf-8"))
        langs = project["languages"]["corpus"]
        size = project["name"].split()[2].rstrip(",")
        from cartolex.demo import generate

        world = generate(size, 0, languages=",".join(langs), two_subjects=two_subjects)
        theme_of_work = {w.work_id: w.themes[0] for w in world.works}
        self.doc_theme = [theme_of_work[Path(p).stem] for p in files]
        language_of_work = {w.work_id: w.language for w in world.works}
        doc_lang = np.array([language_of_work[Path(p).stem] for p in files])
        # each keyword's language: that of most of its texts
        Dc = self.D.tocsc()
        self.language = np.array(
            [
                Counter(doc_lang[Dc.indices[Dc.indptr[j] : Dc.indptr[j + 1]]]).most_common(1)[0][0]
                if Dc.indptr[j + 1] > Dc.indptr[j]
                else ""
                for j in range(Dc.shape[1])
            ]
        )
        #: the keywords the measures judge (``--language``: those of one language)
        self.judged = np.ones(len(self.terms), dtype=bool)
        self.purity, self.main_theme = self._purity()
        self.pairs = derived / "keywords.build" / "keywords_global_refined_pairs.csv"
        truth: dict[str, dict] = {}
        for r in lexicon_truth(langs):
            truth.setdefault(r["text"].lower(), r)
            if r.get("canonical"):
                truth.setdefault(r["canonical"].lower(), r)
        self.truth = {}
        for t in self.terms:
            r = truth.get(t.lower())
            if r is None:
                kind = "other"
            elif r["kind"] == "theme":
                kind = "specific" if len(r["themes"]) == 1 else "cross-theme"
            elif r["kind"] == "method":
                kind = f"method-{r['scope']}"
            else:
                kind = r["kind"]
            self.truth[t] = (kind, tuple(r.get("themes", ())) if r else ())

    def _purity(self) -> tuple[np.ndarray, list[str]]:
        """Per keyword: the share of its texts in its most frequent (primary) theme, and that theme."""
        themes = sorted(set(self.doc_theme))
        col = {t: i for i, t in enumerate(themes)}
        T = np.zeros((len(self.doc_theme), len(themes)))
        T[np.arange(len(self.doc_theme)), [col[t] for t in self.doc_theme]] = 1
        M = np.asarray(self.D.T @ T)
        tot = M.sum(axis=1)
        purity = np.divide(M.max(axis=1), tot, out=np.zeros(len(tot)), where=tot > 0)
        return purity, [themes[j] for j in M.argmax(axis=1)]

    def tree(self, sizes: list[int]):
        """The engine's proposal groups at *sizes* (from the top) and the finest labels."""
        from cartolex.atlas.clustering import fit_agglomerative_labels, prepare_cluster_embeddings
        from cartolex.atlas.hierarchy import level_groups

        labels = fit_agglomerative_labels(
            prepare_cluster_embeddings(self.Z, 50), n_clusters=sizes[-1]
        )
        levels = level_groups(self.Z, labels, sizes)
        finest = np.full(len(self.terms), -1)
        for p, rows in enumerate(levels[-1].rows):
            finest[np.asarray(rows)] = p
        return levels, finest


def _maps(levels, finest_n: int) -> list[np.ndarray]:
    from cartolex.lexicon.theme_comb import level_maps

    return level_maps(levels)


BROAD = {"cross-theme", "method-any", "method-natural", "method-social", "driver", "setting"}


def comb_report(
    space: Space,
    sizes: list[int],
    thetas,
    min_texts: int,
    label: str,
    *,
    relative: bool = True,
    sideways: bool = True,
) -> dict:
    from cartolex.lexicon.theme_comb import comb, keyword_spread

    levels, finest = space.tree(sizes)
    maps = _maps(levels, len(levels[-1].rows))
    P, n = keyword_spread(space.D, finest, len(levels[-1].rows))
    c = comb(P, n, finest, maps, thetas, min_texts=min_texts, relative=relative, sideways=sideways)
    return summarise(space, sizes, c, maps, finest, label)


def theme_of_node(space: Space, finest: np.ndarray, maps, lv: int) -> dict[int, Counter]:
    out: dict[int, Counter] = {}
    for i, t in enumerate(space.terms):
        kind, themes = space.truth[t]
        if kind != "specific" or finest[i] < 0:
            continue
        out.setdefault(int(maps[lv - 1][finest[i]]), Counter())[themes[0]] += 1
    return out


def summarise(space, sizes, c, maps, finest, label) -> dict:
    """Per-level counts and the measures against the texts' themes (and the kinds of the truth).

    From the texts: a keyword is *specific* when at least 80 % of its texts
    (5 or more) share their main theme, *broad* below 50 %. A broad keyword
    should leave the finest level (moved up, or too broad); a specific one
    should stay on a node whose keywords are mostly of its theme.
    """
    depth = len(sizes)
    counts = c.counts(depth)
    per_node = {lv: counts[lv] / sizes[lv - 1] for lv in range(1, depth + 1)}
    n_texts = np.asarray(space.D.sum(axis=0)).ravel()
    enough = (n_texts >= 5) & space.judged
    spec = enough & (space.purity >= 0.8)
    broad = enough & (space.purity < 0.5)
    # the level of the themes: the one whose size is closest to the texts' number of themes
    n_themes = len(set(space.doc_theme))
    theme_level = 1 + int(np.argmin([abs(k - n_themes) for k in sizes]))
    up = (c.level >= 0) & (c.level < theme_level)  # above the themes, or too broad
    judged = spec | broad
    tp = int((up & broad).sum())
    prec = tp / max(int((up & judged).sum()), 1)
    rec = tp / max(int(broad.sum()), 1)
    # each node's theme: the main theme of most of the specific keywords the grouping put under it
    majority: dict[tuple[int, int], str] = {}
    for lv in range(1, depth + 1):
        votes: dict[int, Counter] = {}
        for i in np.flatnonzero(spec & (finest >= 0)):
            votes.setdefault(int(maps[lv - 1][finest[i]]), Counter())[space.main_theme[i]] += 1
        for node, v in votes.items():
            majority[(lv, node)] = v.most_common(1)[0][0]
    in_theme = [
        c.level[i] > 0 and majority.get((int(c.level[i]), int(c.node[i]))) == space.main_theme[i]
        for i in np.flatnonzero(spec)
    ]
    was_in_theme = [
        majority.get((depth, int(finest[i]))) == space.main_theme[i] for i in np.flatnonzero(spec)
    ]
    kinds = np.array([space.truth[t][0] for t in space.terms])
    by_kind = {}
    for k in sorted(set(kinds)):
        sel = kinds == k
        by_kind[k] = {
            "n": int(sel.sum()),
            **{f"L{lv}": int(((c.level == lv) & sel).sum()) for lv in range(0, depth + 1)},
        }
    return {
        "label": label,
        "sizes": sizes,
        "counts": counts,
        "per_node": per_node,
        "precision": prec,
        "recall": rec,
        "n_broad": int(broad.sum()),
        "n_specific": int(spec.sum()),
        "specific_stay": float((c.level[spec] == depth).mean()),
        "specific_up": float((up & spec).sum() / max(spec.sum(), 1)),
        "specific_top_or_aside": float((c.level[spec] <= 1).mean())
        if depth > 1
        else float((c.level[spec] == 0).mean()),
        "in_theme": float(np.mean(in_theme)),
        "was_in_theme": float(np.mean(was_in_theme)),
        "by_kind": by_kind,
    }


def print_comb(r: dict) -> None:
    depth = len(r["sizes"])
    pn = " · ".join(f"{r['per_node'][lv]:.1f}" for lv in range(1, depth + 1))
    cn = " · ".join(str(r["counts"][lv]) for lv in range(1, depth + 1))
    print(
        f"{r['label']:<28} too broad {r['counts'][0]:>4} | per level {cn} | per node {pn} | "
        f"broad({r['n_broad']}) P {r['precision']:.2f} R {r['recall']:.2f} | specific({r['n_specific']}) "
        f"finest {r['specific_stay']:.2f} above themes {r['specific_up']:.2f} "
        f"in theme {r['in_theme']:.2f} (was {r['was_in_theme']:.2f})"
    )


# ── structure suggestions ───────────────────────────────────────────────────


def node_theme(space: Space, rows) -> tuple[str | None, float, int]:
    """A node's main theme from its keywords of one theme (texts ≥ 5, purity ≥ 0.8):
    the theme, the share of the second theme, and how many such keywords."""
    n_texts = np.asarray(space.D.sum(axis=0)).ravel()
    votes = Counter(space.main_theme[i] for i in rows if n_texts[i] >= 5 and space.purity[i] >= 0.8)
    if not votes:
        return None, 0.0, 0
    common = votes.most_common(2)
    total = sum(votes.values())
    second = common[1][1] / total if len(common) > 1 else 0.0
    return common[0][0], second, total


def average_precision(scores, truth) -> float:
    order = np.argsort(-np.asarray(scores), kind="stable")
    t = np.asarray(truth, bool)[order]
    if not t.any():
        return float("nan")
    hits = np.cumsum(t)
    return float((hits[t] / (np.flatnonzero(t) + 1)).mean())


def pr_at(scores, truth, cut) -> tuple[float, float, int]:
    s, t = np.asarray(scores), np.asarray(truth, bool)
    flagged = s >= cut
    tp = int((flagged & t).sum())
    return tp / max(int(flagged.sum()), 1), tp / max(int(t.sum()), 1), int(flagged.sum())


def structure_report(space: Space, k: int, seeds: int = 20) -> dict:
    from theme_structure import merge_measures, split_measures

    levels, finest = space.tree([k])
    groups = [np.asarray(r) for r in levels[0].rows]
    merges = merge_measures(space.Z, space.D, groups)
    splits = split_measures(space.Z, space.D, groups, seeds=seeds)
    themes = [node_theme(space, g) for g in groups]
    for m in merges:
        a, b = themes[m["a"]], themes[m["b"]]
        m["truth"] = a[0] is not None and a[0] == b[0]
    for sp, th in zip(splits, themes, strict=True):
        sp["truth"] = th[1] >= 0.3 and th[2] >= 4
        sp["theme"] = th
    return {"k": k, "merges": merges, "splits": splits}


def print_structure(r: dict) -> None:
    merges, splits = r["merges"], r["splits"]
    mt = [m["truth"] for m in merges]
    st = [s["truth"] for s in splits]
    print(
        f"k={r['k']}: {sum(mt)} true merges of {len(mt)} pairs, {sum(st)} true splits of {len(st)} nodes"
    )
    for key in ("closeness", "overlap", "mixing", "small", "score"):
        print(f"   merge by {key:<10} AP {average_precision([m[key] for m in merges], mt):.2f}")
    for key in ("gain_z", "texts_apart", "cut", "score"):
        print(f"   split by {key:<10} AP {average_precision([s[key] for s in splits], st):.2f}")
    for key, cuts in (("score", (0.15, 0.2, 0.25, 0.3)), ("overlap", (0.3, 0.35, 0.4, 0.5))):
        for cut in cuts:
            p, rc, n = pr_at([m[key] for m in merges], mt, cut)
            print(f"   merge {key} ≥ {cut}: {n} flagged, P {p:.2f} R {rc:.2f}")
    for cut in (2.0, 3.0, 4.0):
        p, rc, n = pr_at([s["score"] for s in splits], st, cut)
        print(f"   split score ≥ {cut}: {n} flagged, P {p:.2f} R {rc:.2f}")


# ── names ───────────────────────────────────────────────────────────────────


def old_names(space: Space, levels, forms, langs) -> list[list[dict[str, str]]]:
    """The names before: each node after its subtree's most used keyword with a form."""
    from cartolex.lexicon.labels import distinct_names, node_names

    out = []
    for group in levels:
        mine = [node_names(r, space.terms, space.scores, forms, langs, "en") for r in group.rows]
        sib: dict[int, list[int]] = {}
        for p in range(len(group.rows)):
            sib.setdefault(-1 if group.parent is None else int(group.parent[p]), []).append(p)
        for members in sib.values():
            tops = [
                [space.terms[i] for i in sorted(group.rows[p], key=lambda i: -space.scores[i])[:15]]
                for p in members
            ]
            distinct_names([mine[p] for p in members], tops)
        out.append(mine)
    return out


def names_report(space: Space, sizes: list[int]) -> None:
    from cartolex.lexicon.labels import keyword_forms
    from cartolex.lexicon.theme_comb import calibrate, keyword_spread
    from cartolex.lexicon.theme_tree import propose_tree

    langs = ["en", "fr"]
    forms = keyword_forms(space.pairs, space.terms, langs, "en")
    levels, finest = space.tree(sizes)
    maps = _maps(levels, len(levels[-1].rows))
    P, n = keyword_spread(space.D, finest, len(levels[-1].rows))
    c = calibrate(P, n, finest, maps)
    theta = c.theta
    new = propose_tree(
        levels,
        space.terms,
        space.scores,
        forms=forms,
        placement=(c.level, c.node),
        spread=P,
        own_floor=0.5,
    )
    plain = propose_tree(levels, space.terms, space.scores, forms=forms)
    most_used = propose_tree(
        levels,
        space.terms,
        space.scores,
        forms=forms,
        placement=(c.level, c.node),
        spread=P,
        own_floor=0.0,
    )
    before = old_names(space, levels, forms, langs)
    # the old document's nodes, by id
    prefix = ["s", *[f"m{lv}-" for lv in range(2, len(sizes))], "c"] if len(sizes) > 1 else ["s"]
    old_by_id = {
        f"{prefix[lv]}{p}": before[lv][p]
        for lv in range(len(sizes))
        for p in range(len(before[lv]))
    }
    term_row = {t: i for i, t in enumerate(space.terms)}
    by_form: dict[str, int] = {}
    for lang in langs:
        for t, f in forms.get(lang, {}).items():
            by_form.setdefault(f.casefold(), term_row[t])
    for t, i in term_row.items():
        by_form.setdefault(t.casefold(), i)
    n_texts = np.asarray(space.D.sum(axis=0)).ravel()
    n_themes = len(set(space.doc_theme))
    theme_level = 1 + int(np.argmin([abs(k - n_themes) for k in sizes]))

    def measure(label: str, doc: dict, names_of) -> None:
        parent = {nd["id"]: nd["parent"] for nd in doc["nodes"]}
        level = {}
        for nd in doc["nodes"]:
            level[nd["id"]] = 1 if nd["parent"] is None else level[nd["parent"]] + 1
        repeats = broad = judged = fit = fit_n = bracket = 0
        for nd in doc["nodes"]:
            nm = names_of(nd)
            for lang in langs:
                name = nm.get(lang, "")
                bracket += "(" in name
                up = parent[nd["id"]]
                while up is not None:
                    if names_of_id(up, names_of, doc).get(lang, "").casefold() == name.casefold():
                        repeats += 1
                        break
                    up = parent[up]
            row = by_form.get(re_base(nm.get("en", "")).casefold())
            if row is None or n_texts[row] < 5:
                continue
            judged += 1
            broad += space.purity[row] < 0.5
            if level[nd["id"]] == theme_level:
                rows = [
                    term_row[k] for k, v in doc["keywords"].items() if under(v, nd["id"], parent)
                ]
                votes = Counter(
                    space.main_theme[r] for r in rows if n_texts[r] >= 5 and space.purity[r] >= 0.8
                )
                if votes:
                    fit_n += 1
                    fit += votes.most_common(1)[0][0] == space.main_theme[row]
        print(
            f"{label:<34} nodes {len(doc['nodes'])} | names repeating an ancestor's {repeats} | "
            f"bracketed {bracket} | named after a broad keyword {broad}/{judged} | "
            f"theme-level names of the node's theme {fit}/{fit_n}"
        )

    measure(
        "before (subtree's most used)",
        new if False else _with_names(plain, old_by_id),
        lambda nd: nd["names"],
    )
    measure(f"combed, own most used (θ {theta})", most_used, lambda nd: nd["names"])
    measure("combed, own most used, ≥ half theirs", new, lambda nd: nd["names"])
    shown = 0
    for nd in most_used["nodes"]:
        if nd["parent"] is None and shown < 4:
            kids = [k for k in most_used["nodes"] if k["parent"] == nd["id"]][:3]
            print(
                "   most used:",
                nd["names"].get("en"),
                "›",
                " | ".join(k["names"].get("en", "") for k in kids),
            )
            shown += 1
    shown = 0
    for nd in new["nodes"]:
        if nd["parent"] is None and shown < 6:
            kids = [k for k in new["nodes"] if k["parent"] == nd["id"]][:3]
            print(
                "   ",
                nd["names"].get("en"),
                "›",
                " | ".join(k["names"].get("en", "") for k in kids),
                "   (before:",
                old_by_id[nd["id"]].get("en"),
                "›",
                " | ".join(old_by_id[k["id"]].get("en", "") for k in kids) + ")",
            )
            shown += 1


def _with_names(doc: dict, names: dict) -> dict:
    import copy

    out = copy.deepcopy(doc)
    for nd in out["nodes"]:
        nd["names"] = names[nd["id"]]
    return out


def names_of_id(nid, names_of, doc):
    return next(names_of(nd) for nd in doc["nodes"] if nd["id"] == nid)


def re_base(name: str) -> str:
    return name.split(" (")[0]


def under(node: str, ancestor: str, parent: dict) -> bool:
    while node is not None:
        if node == ancestor:
            return True
        node = parent[node]
    return False


def q(values) -> str:
    """The 10th, 50th and 90th percentiles of *values*."""
    return " ".join(f"{x:.2f}" for x in np.percentile(values, [10, 50, 90])) if values else "-"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("what", choices=["build", "comb", "grid", "relative", "structure", "names"])
    ap.add_argument("folder", type=Path)
    ap.add_argument("--size", default="S")
    ap.add_argument("--sizes", default="12,25")
    ap.add_argument("--min-texts", type=int, default=5)
    ap.add_argument("--two-subjects", type=float, default=0.0, help="the world's two-subject share")
    ap.add_argument("--grid", help="comb: the calibration's θ values (default: the engine's)")
    ap.add_argument("--one-level", type=float, help="comb: θ at depth 1 (default: the engine's)")
    ap.add_argument(
        "--language",
        help="judge only the keywords whose texts are mostly in this language (a text space "
        "groups each language apart: its broad keywords stay on a node of their language)",
    )
    ap.add_argument(
        "--up-only",
        action="store_true",
        help="comb, relative: read each keyword's own node on every level, not the one with "
        "the most use (the keyword only moves up)",
    )
    ap.add_argument("--space", choices=["person", "text"], help="build: the space's unit")
    ap.add_argument("--kinds", action="store_true", help="print the placements by kind of keyword")
    args = ap.parse_args(argv)
    if args.what == "build":
        build(args.size, args.folder, two_subjects=args.two_subjects, space=args.space)
        return 0
    space = Space(args.folder, args.two_subjects)
    if args.language:
        space.judged = space.language == args.language
    sizes = [int(x) for x in args.sizes.split(",")]
    kinds = Counter(k for k, _ in space.truth.values())
    print(f"{len(space.terms)} keywords, {space.D.shape[0]} texts; truth: {dict(kinds)}")
    if args.what == "comb":
        from cartolex.lexicon.theme_comb import calibrate, keyword_spread

        for theta in (0.2, 0.3, 0.4, 0.5):
            r = comb_report(space, sizes, theta, args.min_texts, f"fixed θ {theta}", relative=False)
            print_comb(r)
            if args.kinds and theta == 0.3:
                for k, v in r["by_kind"].items():
                    print("   ", k, v)
        levels, finest = space.tree(sizes)
        maps = _maps(levels, len(levels[-1].rows))
        P, n = keyword_spread(space.D, finest, len(levels[-1].rows))
        grid = [float(x) for x in args.grid.split(",")] if args.grid else None
        c = calibrate(
            P,
            n,
            finest,
            maps,
            min_texts=args.min_texts,
            sideways=not args.up_only,
            **({"grid": grid} if grid else {}),
            **({"default_theta": args.one_level} if args.one_level else {}),
        )
        r = summarise(space, sizes, c, maps, finest, f"calibrated relative θ {c.theta}")
        print_comb(r)
        if args.kinds:
            for k, v in r["by_kind"].items():
                print("   ", k, v)
    if args.what == "relative":
        from cartolex.lexicon.theme_comb import comb, keyword_spread

        levels, finest = space.tree(sizes)
        maps = _maps(levels, len(levels[-1].rows))
        P, n = keyword_spread(space.D, finest, len(levels[-1].rows))
        for th in (0.1, 0.15, 0.2, 0.25, 0.3, 0.35, 0.4, 0.45, 0.5, 0.55, 0.6, 0.7):
            c = comb(
                P,
                n,
                finest,
                maps,
                th,
                min_texts=args.min_texts,
                relative=True,
                sideways=not args.up_only,
            )
            print_comb(summarise(space, sizes, c, maps, finest, f"relative θ {th}"))
    if args.what == "grid":
        from cartolex.lexicon.theme_comb import comb, keyword_spread

        levels, finest = space.tree(sizes)
        maps = _maps(levels, len(levels[-1].rows))
        P, n = keyword_spread(space.D, finest, len(levels[-1].rows))
        import itertools

        grid = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8]
        for th in itertools.product(grid, repeat=len(sizes)):
            c = comb(P, n, finest, maps, list(th), min_texts=args.min_texts, relative=False)
            print_comb(summarise(space, sizes, c, maps, finest, "θ " + "/".join(map(str, th))))
    if args.what == "names":
        names_report(space, sizes)
    if args.what == "structure":
        reports = [structure_report(space, k) for k in sizes]
        for r in reports:
            print_structure(r)
        pooled = {
            "k": "pooled",
            "merges": [m for r in reports for m in r["merges"]],
            "splits": [x for r in reports for x in r["splits"]],
        }
        print_structure(pooled)
        for key in ("overlap", "closeness", "mixing"):
            for r in reports:
                t = [m[key] for m in r["merges"] if m["truth"]]
                f = [m[key] for m in r["merges"] if not m["truth"]]
                print(f"   k={r['k']} {key}: true merges {q(t)} | others {q(f)}")
        for key in ("cut", "gain_z", "texts_apart"):
            for r in reports:
                t = [x[key] for x in r["splits"] if x["truth"]]
                f = [x[key] for x in r["splits"] if not x["truth"]]
                print(f"   k={r['k']} {key}: true splits {q(t)} | others {q(f)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
