# SPDX-License-Identifier: MIT
"""Measures for the unit the keyword space is fitted on: people or texts.

Usage (on a demo project built up to ``themes.space``; the world is generated
again from its size and the ``two_subjects`` share it was made with)::

    python tools/space_unit_study.py FOLDER --size S [--two-subjects 0.3] [--sizes 12,25]
    python tools/space_unit_study.py FOLDER --size S --map          # after map.layout

For each space: the keywords' groups against the demo's true themes (ARI and
purity of the specific keywords, the terms of one theme), their nearest
neighbours, the groups mixing two unrelated themes, stability when a tenth
of the units is left out, the fitting cost, and the people's neighbours
(``--map``: on the drawn map). Nothing here changes a default.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import tracemalloc
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


class World:
    """A built demo project, its keywords, texts, people and truth."""

    def __init__(self, root: Path, size: str, two_subjects: float) -> None:
        from cartolex.atlas.model_files import load_embeddings, load_lexical_data, load_vectorizer
        from cartolex.demo import generate
        from cartolex.demo.vocabulary import THEME_BY_ID
        from cartolex.demo.writers import lexicon_truth
        from cartolex.lexicon.theme_comb import document_keywords
        from cartolex.lexicon.utils import make_researcher_id

        derived = root / "derived"
        space = derived / "themes.space" / "models"
        self.data = load_lexical_data(space / "lexical_data.json")
        self.emb = load_embeddings(space / "embeddings.json")
        self.dims = self.emb.Z_terms.shape[1]
        self.terms = [str(t) for t in self.data.terms]
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
        from cartolex.lexicon.corpus_store import load_corpus

        manual = derived / "corpus.assemble" / "manual"
        found = load_corpus([("manual", manual / "index.csv", None)], doc_types=False)
        read = dict(found.texts())
        files = sorted(read)
        texts = [read[t] for t in files]
        self.D = document_keywords(texts, vectorizer=vec, alias_to_canon=alias, terms=self.terms)
        row_of = {f: i for i, f in enumerate(files)}
        person_row = {r: i for i, r in enumerate(self.data.individuals)}
        pairs = set()
        for i, t in zip(found.person.tolist(), found.text.tolist(), strict=True):
            who = found.people[i]
            rid = make_researcher_id(who.last_name, who.first_name, who.raw_unit)
            if rid in person_row and t in row_of:
                pairs.add((person_row[rid], row_of[t]))
        from scipy import sparse

        r, c = zip(*pairs, strict=True)
        self.A = sparse.csr_matrix(
            (np.ones(len(r)), (r, c)), shape=(len(person_row), len(files))
        )  # people × texts
        project = json.loads((root / "project.json").read_text(encoding="utf-8"))
        langs = project["languages"]["corpus"]
        world = generate(size, 0, languages=",".join(langs), two_subjects=two_subjects)
        self.world = world
        self.neighbours = {t.id: set(t.neighbours) for t in world.themes}
        truth: dict[str, dict] = {}
        for row in lexicon_truth(langs):
            truth.setdefault(row["text"].lower(), row)
            if row.get("canonical"):
                truth.setdefault(row["canonical"].lower(), row)
        self.theme = np.array(
            [
                (truth[t.lower()]["themes"][0])
                if t.lower() in truth
                and truth[t.lower()]["kind"] == "theme"
                and len(truth[t.lower()]["themes"]) == 1
                else ""
                for t in self.terms
            ],
            dtype=object,
        )
        self.specific = self.theme != ""
        self.lang = np.array(
            [truth[t.lower()]["lang"] if t.lower() in truth else "" for t in self.terms],
            dtype=object,
        )
        # people: the researcher id of each cohort person
        pid = {}
        from cartolex.build.engine import person_ids

        for rid, p in person_ids(derived / "corpus.assemble", ["manual"]).items():
            pid[rid] = p
        people = {p.person_id: p for p in world.people}
        self.person_theme = np.array(
            [
                next(iter(people[pid[rid]].themes)) if rid in pid else ""
                for rid in self.data.individuals
            ],
            dtype=object,
        )
        self.person_mix = [
            people[pid[rid]].themes if rid in pid else {} for rid in self.data.individuals
        ]
        self.theme_ids = sorted(THEME_BY_ID)
        self.canonical = {
            t: truth[t.lower()]["canonical"].lower()
            for t in self.terms
            if t.lower() in truth
            and truth[t.lower()]["lang"] != "en"
            and truth[t.lower()].get("canonical")
        }

    def merged(self) -> World:
        """The same world with every keyword of another language folded onto its English
        keyword when the vocabulary has it (as a perfect merge of translations would)."""
        from scipy import sparse

        col = {t: j for j, t in enumerate(self.terms)}
        target = np.array([col.get(self.canonical.get(t, ""), j) for j, t in enumerate(self.terms)])
        keep = np.flatnonzero(target == np.arange(len(self.terms)))
        new = {j: i for i, j in enumerate(keep)}
        F = sparse.csr_matrix(
            (np.ones(len(target)), (np.arange(len(target)), [new[j] for j in target])),
            shape=(len(target), len(keep)),
        )
        out = World.__new__(World)
        out.__dict__.update(self.__dict__)
        D = (self.D @ F).tocsr()
        D.data[:] = 1.0
        out.D = D
        out.data = type(self.data)(
            X=(self.data.X @ F).tocsr(),
            terms=[self.terms[j] for j in keep],
            individuals=self.data.individuals,
            meta_ind=self.data.meta_ind,
        )
        out.terms = out.data.terms
        for name in ("theme", "specific", "lang"):
            setattr(out, name, getattr(self, name)[keep])
        return out

    def unrelated(self, a: str, b: str) -> bool:
        return a != b and b not in self.neighbours[a] and a not in self.neighbours[b]


# ── the spaces ───────────────────────────────────────────────────────────────


def person_space(w: World, rows=None, *, dims: int) -> tuple[np.ndarray, np.ndarray]:
    """``(keywords, people)`` of the space fitted on the people (the engine's today)."""
    from sklearn.decomposition import TruncatedSVD
    from sklearn.preprocessing import normalize

    X = normalize(w.data.X if rows is None else w.data.X[rows], norm="l2", axis=1)
    svd = TruncatedSVD(n_components=min(dims, *X.shape), random_state=42)
    Z_ind = svd.fit_transform(X)
    return svd.components_.T * svd.singular_values_, Z_ind


def text_space(
    w: World, rows=None, *, dims: int
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """``(keywords, people projected, people as their texts' mean, texts)`` of the text space."""
    import tempfile

    from sklearn.preprocessing import normalize

    from cartolex.atlas.model_files import load_svd
    from cartolex.atlas.reducers import compute_text_svd_embeddings, text_tfidf

    D = w.D if rows is None else w.D[rows]
    with tempfile.TemporaryDirectory() as tmp:
        model = Path(tmp) / "svd.json"
        emb = compute_text_svd_embeddings(w.data, D, n_components=dims, model_path=model)
        svd = load_svd(model)
    Z_texts = svd.transform(text_tfidf(w.D))
    A = normalize(w.A, norm="l1", axis=1)
    return emb.Z_terms, emb.Z_ind, np.asarray(A @ Z_texts), Z_texts


def mixed_space(w: World, rows=None, *, dims: int, balance: bool) -> np.ndarray:
    """The keywords of a space fitted on the texts and the people together (a person row weighs
    one text, or, *balanced*, the people weigh as much as the texts together)."""
    from scipy import sparse
    from sklearn.decomposition import TruncatedSVD
    from sklearn.preprocessing import normalize

    from cartolex.atlas.reducers import text_tfidf

    T = text_tfidf(w.D if rows is None else w.D[rows])
    P = normalize(w.data.X, norm="l2", axis=1)
    if balance:
        P = P * np.sqrt(T.shape[0] / P.shape[0])
    M = sparse.vstack([T, P]).tocsr()
    svd = TruncatedSVD(n_components=min(dims, *M.shape), random_state=42)
    svd.fit(M)
    return svd.components_.T * svd.singular_values_


# ── measures ─────────────────────────────────────────────────────────────────


def cut(Z: np.ndarray, k: int) -> np.ndarray:
    from cartolex.atlas.clustering import fit_agglomerative_labels, prepare_cluster_embeddings

    return fit_agglomerative_labels(prepare_cluster_embeddings(Z, 50), n_clusters=k)


def unit(Z: np.ndarray) -> np.ndarray:
    from cartolex.atlas.clustering import prepare_cluster_embeddings

    return prepare_cluster_embeddings(Z, 50)


def groups(w: World, labels: np.ndarray) -> dict:
    """ARI and purity of the specific keywords, and the groups mixing unrelated themes."""
    from sklearn.metrics import adjusted_rand_score

    s = w.specific
    ari = adjusted_rand_score(w.theme[s], labels[s])
    pure = mixed = mixed_kw = 0
    for g in np.unique(labels):
        th = Counter(w.theme[s & (labels == g)])
        if not th:
            continue
        pure += th.most_common(1)[0][1]
        n = sum(th.values())
        big = [t for t, c in th.items() if c >= max(0.2 * n, 2)]
        if any(w.unrelated(a, b) for a in big for b in big):
            mixed += 1
            mixed_kw += n
    # the language: the share of each group's keywords (of a known language) in its main one
    known = w.lang != ""
    main_lang = 0
    for g in np.unique(labels[known]):
        main_lang += Counter(w.lang[known & (labels == g)]).most_common(1)[0][1]
    return {
        "one-language share": main_lang / max(int(known.sum()), 1),
        "ARI": ari,
        "purity": pure / max(int(s.sum()), 1),
        "mixed groups": mixed,
        "groups": len(np.unique(labels)),
    }


def neighbours(w: World, Z: np.ndarray, k: int = 10) -> dict:
    """Among each specific keyword's k nearest specific keywords: the share of its theme, and of
    an unrelated theme; and the unrelated pairs at cosine ≥ 0.95."""
    U = unit(Z)[w.specific]
    th = w.theme[w.specific]
    S = U @ U.T
    np.fill_diagonal(S, -np.inf)
    nn = np.argsort(-S, axis=1)[:, :k]
    hit = th[nn] == th[:, None]
    same = hit.mean()
    rare = np.asarray(w.D.sum(axis=0)).ravel()[w.specific] < 5
    unrel = np.mean([[w.unrelated(a, b) for b in th[row]] for a, row in zip(th, nn, strict=True)])
    lang = w.lang[w.specific]
    same_lang = float((lang[nn] == lang[:, None]).mean())
    iu = np.triu_indices(len(th), 1)
    rel = np.array([w.unrelated(th[i], th[j]) for i, j in zip(*iu, strict=True)])
    close = S[iu] >= 0.95
    return {
        "nn same theme": same,
        "nn same theme, < 5 texts": float(hit[rare].mean()) if rare.any() else float("nan"),
        "nn same theme, ≥ 5 texts": float(hit[~rare].mean()),
        "nn unrelated theme": unrel,
        "unrelated pairs ≥ 0.95": float((close & rel).sum() / max(rel.sum(), 1)),
        "nn same language": same_lang,
    }


def people_nn(w: World, Z: np.ndarray, k: int = 5, *, metric: str = "cosine") -> dict:
    """The people's k nearest: the share with the same main theme, and the Spearman correlation
    of the people's closeness with the closeness of their true theme mixes."""
    from scipy.stats import spearmanr
    from sklearn.metrics import pairwise_distances

    ok = w.person_theme != ""
    Zp = Z[ok]
    th = w.person_theme[ok]
    Dm = pairwise_distances(Zp, metric=metric)
    np.fill_diagonal(Dm, np.inf)
    nn = np.argsort(Dm, axis=1)[:, :k]
    mixes = [w.person_mix[i] for i in np.flatnonzero(ok)]
    M = np.array([[m.get(t, 0.0) for t in w.theme_ids] for m in mixes])
    M = M / np.linalg.norm(M, axis=1, keepdims=True)
    iu = np.triu_indices(len(th), 1)
    rho = spearmanr(-Dm[iu], (M @ M.T)[iu]).statistic
    return {"people nn same theme": float((th[nn] == th[:, None]).mean()), "people ρ": float(rho)}


def draft(w: World, root: Path) -> dict:
    """The proposal tree the build wrote: its top level and finest level against the truth."""
    doc = json.loads(
        (root / "derived" / "themes.group" / "themes_draft.json").read_text(encoding="utf-8")
    )
    parent = {n["id"]: n.get("parent") for n in doc["nodes"]}

    def top(nid: str) -> str:
        while parent[nid] is not None:
            nid = parent[nid]
        return nid

    node = doc["keywords"]
    finest = {n for n in parent} - {p for p in parent.values() if p}
    out = {}
    for label, of in (("top", top), ("finest", lambda n: n)):
        placed = np.array([t in node and (label == "top" or node[t] in finest) for t in w.terms])
        keep = placed & w.specific
        labels = np.array([of(node[t]) if k else "" for t, k in zip(w.terms, keep, strict=True)])
        codes = {v: i for i, v in enumerate(sorted(set(labels[keep])))}
        lab = np.full(len(w.terms), -1)
        lab[keep] = [codes[v] for v in labels[keep]]
        sub = World.__new__(World)
        sub.__dict__.update(w.__dict__)
        sub.specific = keep
        g = groups(sub, lab)
        out.update({f"draft {label} {k}": v for k, v in g.items()})
    aside = doc.get("set_aside") or {}
    out["draft specific set aside"] = int(
        sum(1 for t in aside if t in set(np.array(w.terms)[w.specific]))
    )
    out["draft set aside"] = len(aside)
    return out


def stability(w: World, fit, n_units: int, k: int, draws: int = 2) -> float:
    """ARI of the cut at k between the full space and the space without a tenth of the units."""
    from sklearn.metrics import adjusted_rand_score

    full = cut(fit(None), k)
    out = []
    for seed in range(draws):
        rng = np.random.default_rng(seed)
        rows = np.sort(rng.choice(n_units, int(0.9 * n_units), replace=False))
        out.append(adjusted_rand_score(full, cut(fit(rows), k)))
    return float(np.mean(out))


def cost(fn) -> tuple[float, float]:
    tracemalloc.start()
    t = time.perf_counter()
    fn()
    sec = time.perf_counter() - t
    peak = tracemalloc.get_traced_memory()[1] / 2**20
    tracemalloc.stop()
    return sec, peak


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("folder", type=Path)
    ap.add_argument("--size", required=True)
    ap.add_argument("--two-subjects", type=float, default=0.0)
    ap.add_argument("--sizes", default="12,25", help="theme and topic counts")
    ap.add_argument("--map", action="store_true", help="measure the people on the drawn map")
    ap.add_argument("--draft", action="store_true", help="measure the grouping's proposal tree")
    ap.add_argument(
        "--merged", action="store_true", help="fold the translations first (a perfect merge)"
    )
    args = ap.parse_args(argv)
    w = World(args.folder, args.size, args.two_subjects)
    if args.merged:
        w = w.merged()
    k_themes, k_topics = (int(x) for x in args.sizes.split(","))
    dims = w.dims
    if args.map or args.draft:
        out = {}
        if args.map:
            from cartolex.atlas.model_files import load_embeddings

            drawn = load_embeddings(
                args.folder / "derived" / "map.layout" / "models" / "embeddings.json"
            )
            out.update(people_nn(w, drawn.umap_ind, metric="euclidean"))
        if args.draft:
            out.update(draft(w, args.folder))
        print(json.dumps(out, indent=1))
        return 0
    rows = []
    Zp, Zp_ind = person_space(w, dims=dims)
    Zt, Zt_ind, Zt_mean, _ = text_space(w, dims=dims)
    for name, Z, fit, n in (
        ("person", Zp, lambda r: person_space(w, r, dims=dims)[0], w.data.X.shape[0]),
        ("text", Zt, lambda r: text_space(w, r, dims=dims)[0], w.D.shape[0]),
        (
            "texts + people",
            mixed_space(w, dims=dims, balance=False),
            lambda r: mixed_space(w, r, dims=dims, balance=False),
            w.D.shape[0],
        ),
        (
            "texts + people, balanced",
            mixed_space(w, dims=dims, balance=True),
            lambda r: mixed_space(w, r, dims=dims, balance=True),
            w.D.shape[0],
        ),
    ):
        r = {"space": name}
        for label, k in (("themes", k_themes), ("topics", k_topics)):
            g = groups(w, cut(Z, k))
            r.update({f"{label} {key}": v for key, v in g.items() if key != "groups"})
        r.update(neighbours(w, Z))
        r["stability ARI"] = stability(w, fit, n, k_themes)
        sec, peak = cost(lambda fit=fit: fit(None))
        r["seconds"], r["peak MB"] = sec, peak
        rows.append(r)
    table = pd.DataFrame(rows).set_index("space").T
    known = w.lang[w.lang != ""]
    base = Counter(known).most_common(1)[0][1] / len(known)
    print(
        f"# {args.folder.name}: {len(w.terms)} keywords ({int(w.specific.sum())} specific), "
        f"{w.D.shape[0]} texts, {w.data.X.shape[0]} people, {dims} dimensions; "
        f"{base:.3f} of the keywords of a known language in the main one"
    )
    print(table.to_string(float_format=lambda v: f"{v:.3f}"))
    ppl = pd.DataFrame(
        {
            "person space": people_nn(w, Zp_ind),
            "text space, projected": people_nn(w, Zt_ind),
            "text space, texts' mean": people_nn(w, Zt_mean),
        }
    )
    print(ppl.to_string(float_format=lambda v: f"{v:.3f}"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
