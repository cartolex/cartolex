# SPDX-License-Identifier: MIT
"""Measures for the unit the keyword space is fitted on, on a real project (no truth).

The project is built twice up to ``themes.group`` (``themes.space.space_unit``
``person`` then ``text``) and each build's ``derived/themes.space`` and
``derived/themes.group`` folders are copied aside; then::

    python tools/space_unit_project.py measure PROJECT --person DIR --text DIR --out OUT
    python tools/space_unit_project.py judge   PROJECT --person DIR --text DIR --out OUT
    python tools/space_unit_project.py score   OUT/judge_key.json RATINGS.csv

``measure`` writes ``OUT/measures.json`` (the numbers) and ``OUT/examples.json``
(the links made only by people, the unstable nodes: the project's own words,
keep them out of any repository) and prints the tables. The proxies:

1. of each keyword's 10 nearest keywords, the share no single text uses with it;
2. per theme and topic, the share of its keywords in its main language (a
   keyword's language: that of most of the one-language texts using it), and
   whether the French keywords' nodes also hold English ones;
3. the grouping refitted without a tenth of the people (and their texts), three
   draws: each level's adjusted Rand index, and each node's Jaccard index with
   the closest group of its level;
4. the keywords the comb set aside in each, and how many are the same;
5. the fitting's time and peak memory (from the build's run record).

``judge`` writes a blind bundle (``judge.md``: every theme's and topic's top 12
keywords, the two spaces shuffled and labelled A and B; ``judge_key.json``
says which is which); ``score`` reads a judge's ``id,rating,subjects`` CSV.
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SPACES = ("person", "text")
RATINGS = ("one", "two", "mix")


class Build:
    """One build's space and draft tree (a copy of ``themes.space`` and ``themes.group``)."""

    def __init__(self, folder: Path) -> None:
        from cartolex.atlas.model_files import load_embeddings, load_lexical_data

        models = folder / "themes.space" / "models"
        self.data = load_lexical_data(models / "lexical_data.json")
        self.Z = load_embeddings(models / "embeddings.json").Z_terms
        self.tree = json.loads(
            (folder / "themes.group" / "themes_draft.json").read_text(encoding="utf-8")
        )
        self.space_run = json.loads((folder / "themes.space" / "run.json").read_text("utf-8"))
        self.group_run = json.loads((folder / "themes.group" / "run.json").read_text("utf-8"))
        self.terms = [str(t) for t in self.data.terms]
        self.parent = {n["id"]: n.get("parent") for n in self.tree["nodes"]}
        self.name = {n["id"]: (n.get("names") or {}).get("en", n["id"]) for n in self.tree["nodes"]}

    def level(self, nid: str) -> int:
        lv = 1
        while self.parent[nid] is not None:
            nid, lv = self.parent[nid], lv + 1
        return lv

    def members(self) -> dict[str, list[int]]:
        """Each node's keyword rows (a theme holds its topics' keywords)."""
        row = {t: i for i, t in enumerate(self.terms)}
        out: dict[str, list[int]] = defaultdict(list)
        for k, nid in self.tree["keywords"].items():
            while nid is not None:
                out[nid].append(row[k])
                nid = self.parent[nid]
        return out

    def params(self) -> dict:
        return {k: v["value"] for k, v in self.group_run["parameters"].items()}

    def sizes(self) -> list[int]:
        c = Counter(self.level(n) for n in self.parent)
        return [c[lv] for lv in sorted(c)]


class Project:
    """The project's texts: keywords per text, each text's language and people."""

    def __init__(self, root: Path, terms: list[str]) -> None:
        from cartolex.lexicon.theme_comb import corpus_texts
        from cartolex.lexicon.utils import make_researcher_id

        derived = root / "derived"
        project = json.loads((root / "project.json").read_text(encoding="utf-8"))
        slots = [s["id"] for s in project["slots"] if s.get("fit", True)]
        indexes = [derived / "corpus.assemble" / s / "index.csv" for s in slots]
        build = derived / "keywords.build" / "models"
        self.D = corpus_texts(
            indexes,
            vectorizer_json=build / "tfidf_restricted.json",
            aliases_csv=build / "term_aliases.csv",
            terms=terms,
        ).tocsr()
        # the files in corpus_texts' order, and who signed each
        files: list[Path] = []
        row: dict[Path, int] = {}
        self.signers: list[set[str]] = []
        for index in indexes:
            df = pd.read_csv(index, dtype=str, keep_default_na=False)
            for last, first, unit, p in zip(
                df["last_name"], df["first_name"], df["unit"], df["txt_path"], strict=True
            ):
                if not p:
                    continue
                path = (index.parent / p).resolve()
                if path not in row:
                    row[path] = len(files)
                    files.append(path)
                    self.signers.append(set())
                self.signers[row[path]].add(make_researcher_id(last, first, unit))
        assert len(files) == self.D.shape[0]
        parts = pd.read_parquet(root / "sources" / "tables" / "text_parts.parquet")
        langs = parts.groupby("text_id")["language"].agg(lambda s: set(s))
        abstracts = parts[parts["part"] == "abstract"].groupby("text_id")["language"].agg(set)
        self.text_ids = [p.stem for p in files]
        self.lang = np.array(
            [_one_language(langs.get(t, set())) for t in self.text_ids], dtype=object
        )
        self.both_abstracts = sum(1 for s in abstracts if {"en", "fr"} <= s)
        self.n_texts_all = int(parts["text_id"].nunique())
        self.n_abstracts = len(abstracts)
        self.fr_any = sum(1 for s in langs if "fr" in s)

    def keyword_language(self) -> np.ndarray:
        """Each keyword's language: that of most of the one-language (en or fr) texts using it."""
        en = np.asarray(self.D[self.lang == "en"].sum(axis=0)).ravel()
        fr = np.asarray(self.D[self.lang == "fr"].sum(axis=0)).ravel()
        return np.where(en + fr == 0, "", np.where(fr > en, "fr", "en")).astype(object)


def _one_language(s: set) -> str:
    s = {x for x in s if x in ("en", "fr")}
    return next(iter(s)) if len(s) == 1 else ("both" if s else "")


# ── measures ─────────────────────────────────────────────────────────────────


def unit(Z: np.ndarray) -> np.ndarray:
    from cartolex.atlas.clustering import prepare_cluster_embeddings

    return prepare_cluster_embeddings(Z, 50)


def links(b: Build, co_text: np.ndarray, co_people: np.ndarray, k: int = 10) -> tuple[dict, list]:
    """Of each keyword's k nearest, the share no text uses together with it; the strongest such
    links (keywords used by at least one text only)."""
    used = np.diag(co_text) > 0
    U = unit(b.Z)[used]
    idx = np.flatnonzero(used)
    S = U @ U.T
    np.fill_diagonal(S, -np.inf)
    nn = np.argsort(-S, axis=1)[:, :k]
    shared = co_text[np.ix_(idx, idx)][np.arange(len(idx))[:, None], nn]
    by_people = co_people[np.ix_(idx, idx)][np.arange(len(idx))[:, None], nn]
    none = shared == 0
    rng = np.random.default_rng(0)
    a, c = rng.integers(0, len(idx), (2, 200_000))
    base = float((co_text[idx[a], idx[c]] == 0)[a != c].mean())
    pairs = {}
    for i, row in enumerate(nn):
        for j in row[none[i]]:
            key = (min(i, j), max(i, j))
            # ties (keywords of one person have one vector) go to the most used pair
            pairs[key] = (
                round(float(S[i, j]), 4),
                min(co_text[idx[i], idx[i]], co_text[idx[j], idx[j]]),
            )
    top = sorted(pairs.items(), key=lambda kv: (-kv[1][0], -kv[1][1]))[:30]
    examples = [
        {
            "a": b.terms[idx[i]],
            "b": b.terms[idx[j]],
            "cosine": round(s[0], 3),
            "texts": [int(co_text[idx[i], idx[i]]), int(co_text[idx[j], idx[j]])],
            "people both": int(co_people[idx[i], idx[j]]),
        }
        for (i, j), s in top
    ]
    return {
        "keywords in no text": int((~used).sum()),
        "nn without a shared text": float(none.mean()),
        "nn without a shared text, with a shared person": float((none & (by_people > 0)).mean()),
        "nn with ≥ 3 shared texts": float((shared >= 3).mean()),
        "nn at cosine ≥ 0.999": float((S[np.arange(len(idx))[:, None], nn] >= 0.999).mean()),
        "keywords with ≥ 1 such link": float(none.any(axis=1).mean()),
        "random pairs without a shared text": base,
    }, examples


def languages(b: Build, lang: np.ndarray) -> dict:
    members = b.members()
    out: dict = {}
    known = lang != ""
    for lv in (1, 2):
        shares, main_fr, n = [], 0, 0
        for nid, rows in members.items():
            if b.level(nid) != lv:
                continue
            ls = [lang[r] for r in rows if known[r]]
            if not ls:
                continue
            main, c = Counter(ls).most_common(1)[0]
            shares.append(c / len(ls))
            main_fr += main == "fr"
            n += 1
        out[f"level {lv} nodes"] = n
        out[f"level {lv} main-language share (mean)"] = float(np.mean(shares))
        out[f"level {lv} nodes ≥ 90 % one language"] = int(sum(s >= 0.9 for s in shares))
        out[f"level {lv} nodes mostly French"] = int(main_fr)
    # the French keywords: placed, set aside, and with English keywords in their node
    row = {t: i for i, t in enumerate(b.terms)}
    fr = [t for t in b.terms if lang[row[t]] == "fr"]
    placed = [t for t in fr if t in b.tree["keywords"]]
    out["French keywords"] = len(fr)
    out["French keywords set aside"] = sum(1 for t in fr if t in b.tree["set_aside"])
    for lv in (1, 2):
        mixed = 0
        seen = 0
        for t in placed:
            nid = b.tree["keywords"][t]
            while b.level(nid) > lv:
                nid = b.parent[nid]
            if b.level(nid) != lv:
                continue
            seen += 1
            mixed += any(lang[r] == "en" for r in members[nid])
        out[f"French keywords at level {lv}"] = seen
        out[f"… whose level-{lv} node holds English keywords"] = mixed
    return out


def stability(
    b: Build, proj: Project, unit_kind: str, draws: int = 3, drop: float = 0.1
) -> tuple[dict, list]:
    """The grouping refitted without a share *drop* of the people (and their texts)."""
    import tempfile

    from cartolex.atlas.reducers import compute_svd_embeddings, compute_text_svd_embeddings
    from cartolex.atlas.types import LexicalData
    from cartolex.build.engine import ward_options
    from cartolex.copilot.measures import _ari, group_levels

    params = b.params()
    ward = ward_options(params)
    components = int(params.get("cluster_dimensions") or 50)
    sizes = b.sizes()
    dims = b.Z.shape[1]
    people = [str(p) for p in b.data.individuals]
    X = b.data.X.tocsr()

    def grouping(rows: np.ndarray, folder: Path) -> list[np.ndarray]:
        data = LexicalData(
            X=X[rows],
            terms=b.terms,
            individuals=[people[i] for i in rows],
            meta_ind=pd.DataFrame(index=range(len(rows))),
        )
        model = folder / "svd.json"
        if unit_kind == "text":
            gone = set(people) - set(data.individuals)
            keep = [i for i, s in enumerate(proj.signers) if not (s & gone)]
            emb = compute_text_svd_embeddings(
                data, proj.D[keep], n_components=dims, model_path=model
            )
        else:
            emb = compute_svd_embeddings(data, n_components=dims, model_path=model)
        return group_levels(emb.Z_terms, sizes, components=components, ward=ward)

    members = {nid: np.asarray(r) for nid, r in b.members().items()}
    per_node: dict[str, list[float]] = {nid: [] for nid in members}
    rng = np.random.default_rng(0)
    n = len(people)
    keep_n = int(round(n * (1 - drop)))
    per_level: list[list[float]] = [[] for _ in sizes]
    with tempfile.TemporaryDirectory() as tmp:
        full = grouping(np.arange(n), Path(tmp))
        raw: list[dict[int, list[float]]] = [defaultdict(list) for _ in sizes]
        for _ in range(draws):
            rows = np.sort(rng.choice(n, size=keep_n, replace=False))
            again = grouping(rows, Path(tmp))
            for lv, (x, y) in enumerate(zip(full, again, strict=True)):
                per_level[lv].append(_ari(x, y))
            for nid, r in members.items():
                per_node[nid].append(_best_jaccard(r, again[b.level(nid) - 1]))
            for lv, labels in enumerate(full):
                for g, gl in enumerate(np.unique(labels[labels >= 0])):
                    raw[lv][g].append(_best_jaccard(np.flatnonzero(labels == gl), again[lv]))
    per_group = [[float(np.mean(v)) for v in r.values()] for r in raw]
    out = {f"level {lv + 1} ARI": float(np.mean(v)) for lv, v in enumerate(per_level)}
    for lv, v in enumerate(per_group):
        out[f"level {lv + 1} group Jaccard, uncombed (median)"] = float(np.median(v))
        out[f"level {lv + 1} groups Jaccard < 0.5, uncombed"] = int((np.asarray(v) < 0.5).sum())
    out.update({f"level {lv + 1} ARI lowest": float(np.min(v)) for lv, v in enumerate(per_level)})
    score = np.asarray(X.sum(axis=0)).ravel()
    nodes = []
    for nid, v in per_node.items():
        rows = members[nid]
        nodes.append(
            {
                "node": b.name[nid],
                "level": b.level(nid),
                "keywords": len(rows),
                "jaccard": round(float(np.mean(v)), 3),
                "top": [b.terms[r] for r in rows[np.argsort(-score[rows])][:8]],
            }
        )
    for lv in (1, 2):
        js = [x["jaccard"] for x in nodes if x["level"] == lv]
        out[f"level {lv} node Jaccard (median)"] = float(np.median(js))
        out[f"level {lv} nodes Jaccard < 0.5"] = int(sum(j < 0.5 for j in js))
    nodes.sort(key=lambda x: (x["level"], x["jaccard"]))
    return out, nodes


def _best_jaccard(rows: np.ndarray, labels: np.ndarray) -> float:
    sizes = Counter(int(x) for x in labels.tolist() if x >= 0)
    inside = Counter(int(x) for x in labels[rows].tolist() if x >= 0)
    return max((c / (len(rows) + sizes[g] - c) for g, c in inside.items()), default=0.0)


def lifted(b: Build) -> int:
    """Keywords placed on a theme rather than a topic (the comb's « move up »)."""
    return sum(1 for nid in b.tree["keywords"].values() if b.level(nid) == 1)


def cost(b: Build) -> dict:
    m = b.space_run["measures"]
    g = b.group_run["measures"]
    return {
        "themes.space seconds": m["seconds"],
        "themes.space peak MB": m["peak_memory_mb"],
        "themes.group seconds": g["seconds"],
        "themes.group peak MB": g["peak_memory_mb"],
    }


def measure(args: argparse.Namespace) -> int:
    builds = {"person": Build(args.person), "text": Build(args.text)}
    terms = builds["person"].terms
    assert builds["text"].terms == terms
    proj = Project(args.project, terms)
    lang = proj.keyword_language()
    B = (proj.D > 0).astype(np.int32)
    co_text = (B.T @ B).toarray()
    P = (builds["person"].data.X > 0).astype(np.int32)
    co_people = (P.T @ P).toarray()
    table: dict[str, dict] = {}
    examples: dict[str, dict] = {}
    for name, b in builds.items():
        r: dict = {}
        r.update(cost(b))
        lk, top = links(b, co_text, co_people)
        r.update(lk)
        r.update(languages(b, lang))
        st, nodes = stability(b, proj, name)
        r.update(st)
        aside = set(b.tree["set_aside"])
        r["set aside"] = len(aside)
        r["placed on a theme"] = lifted(b)
        table[name] = r
        examples[name] = {
            "links without a shared text": top,
            "least stable themes": [x for x in nodes if x["level"] == 1][:8],
            "least stable topics": [x for x in nodes if x["level"] == 2 and x["keywords"] >= 5][
                :15
            ],
        }
    a, c = (set(builds[s].tree["set_aside"]) for s in SPACES)
    both = {
        "set aside by both": len(a & c),
        "set aside, person only": len(a - c),
        "set aside, text only": len(c - a),
    }
    counts = Counter(proj.lang.tolist())
    corpus = {
        "texts read": int(proj.D.shape[0]),
        "texts (all)": proj.n_texts_all,
        "texts with an abstract": proj.n_abstracts,
        "texts with abstracts in English and French": proj.both_abstracts,
        "texts with a French part": proj.fr_any,
        "texts read: English only": counts["en"],
        "texts read: French only": counts["fr"],
        "texts read: both": counts["both"],
        "keywords per text, English only": float(proj.D[proj.lang == "en"].getnnz(axis=1).mean()),
        "keywords per text, French only": float(proj.D[proj.lang == "fr"].getnnz(axis=1).mean()),
        "French-only texts without a keyword": int(
            (proj.D[proj.lang == "fr"].getnnz(axis=1) == 0).sum()
        ),
        "keywords": len(terms),
        "keywords French": int((lang == "fr").sum()),
        "keywords English": int((lang == "en").sum()),
    }
    examples["set aside"] = {
        "both": sorted(a & c),
        "person only": sorted(a - c),
        "text only": sorted(c - a),
    }
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "measures.json").write_text(
        json.dumps({"corpus": corpus, "spaces": table, "comb": both}, indent=1, ensure_ascii=False),
        encoding="utf-8",
    )
    (args.out / "examples.json").write_text(
        json.dumps(examples, indent=1, ensure_ascii=False), encoding="utf-8"
    )
    print(json.dumps(corpus, indent=1, ensure_ascii=False))
    print(pd.DataFrame(table).to_string(float_format=lambda v: f"{v:.3f}"))
    print(json.dumps(both, indent=1))
    return 0


# ── the blind judge ──────────────────────────────────────────────────────────


def judge(args: argparse.Namespace) -> int:
    builds = {"person": Build(args.person), "text": Build(args.text)}
    rnd = random.Random(args.seed)
    letters = ["A", "B"]
    rnd.shuffle(letters)
    letter = dict(zip(SPACES, letters, strict=True))
    items = []
    for name, b in builds.items():
        score = np.asarray(b.data.X.sum(axis=0)).ravel()
        for nid, rows in b.members().items():
            rows = np.asarray(rows)
            top = [b.terms[r] for r in rows[np.argsort(-score[rows])][:12]]
            items.append({"space": name, "level": b.level(nid), "node": nid, "keywords": top})
    lines = [
        "# Themes to rate",
        "",
        "Each list below is the 12 most used keywords of one group of a research field's",
        "keywords. For each, say whether the keywords are about **one subject**, **two",
        "subjects** (two distinct ones side by side) or **a mix** (three or more, or no clear",
        "subject), and name the subject(s) in a few words. Judge the keywords only: the",
        "letters and the order carry no meaning.",
        "",
        "Answer as a CSV file with the header `id,rating,subjects`, one row per list;",
        "`rating` is `one`, `two` or `mix`; quote `subjects` if it holds a comma.",
        "",
    ]
    key: dict[str, dict] = {"spaces": letter, "items": {}}
    for lv, title in ((1, "Themes (broad groups)"), (2, "Topics (narrow groups)")):
        these = [x for x in items if x["level"] == lv]
        rnd.shuffle(these)
        count = Counter()
        lines += [f"## {title}", ""]
        for x in these:
            L = letter[x["space"]]
            count[L] += 1
            iid = f"{lv}{L}{count[L]:03d}"
            key["items"][iid] = {"space": x["space"], "level": lv, "node": x["node"]}
            lines.append(f"- **{iid}**: " + " · ".join(x["keywords"]))
        lines.append("")
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "judge.md").write_text("\n".join(lines), encoding="utf-8")
    (args.out / "judge_key.json").write_text(json.dumps(key, indent=1), encoding="utf-8")
    print(f"{len(items)} lists → {args.out / 'judge.md'}; key in {args.out / 'judge_key.json'}")
    return 0


def score(args: argparse.Namespace) -> int:
    key = json.loads(args.key.read_text(encoding="utf-8"))["items"]
    with args.ratings.open(encoding="utf-8", newline="") as f:
        rated = {r["id"].strip(): r["rating"].strip().lower() for r in csv.DictReader(f)}
    missing = sorted(set(key) - set(rated))
    unknown = sorted(set(rated) - set(key))
    bad = sorted(i for i, r in rated.items() if i in key and r not in RATINGS)
    tally: dict[tuple, Counter] = defaultdict(Counter)
    for iid, r in rated.items():
        if iid in key and r in RATINGS:
            tally[(key[iid]["space"], key[iid]["level"])][r] += 1
    rows = []
    for (space, lv), c in sorted(tally.items()):
        n = sum(c.values())
        rows.append(
            {"space": space, "level": lv, "rated": n} | {r: round(c[r] / n, 3) for r in RATINGS}
        )
    print(pd.DataFrame(rows).to_string(index=False))
    if missing or unknown or bad:
        print(f"missing {len(missing)}, unknown ids {len(unknown)}, bad ratings {len(bad)}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("measure", "judge"):
        p = sub.add_parser(name)
        p.add_argument("project", type=Path)
        p.add_argument("--person", type=Path, required=True, help="the person build's copy")
        p.add_argument("--text", type=Path, required=True, help="the text build's copy")
        p.add_argument("--out", type=Path, required=True)
        if name == "judge":
            p.add_argument("--seed", type=int, default=0)
    p = sub.add_parser("score")
    p.add_argument("key", type=Path)
    p.add_argument("ratings", type=Path)
    args = ap.parse_args(argv)
    return {"measure": measure, "judge": judge, "score": score}[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
