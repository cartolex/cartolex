# SPDX-License-Identifier: MIT
"""The lab's corpora: demo worlds (with their truth) and public benchmarks (with their gold).

A :class:`LabCorpus` holds people, their texts split into parts (title,
abstract, body) in a known language, and what the lab scores against: the
field terms of the demo truth, or the reference keyphrases of a benchmark.
Benchmark files live in ``.cache/datasets/`` and are never committed; the
demo worlds are generated in memory.

Benchmarks (evaluation only):

====================  ========  =====================================================
name                  language  source
====================  ========  =====================================================
``inspec``            en        abstracts with indexer keyphrases (validation + test)
``termith``           fr        abstracts with indexer keyphrases (test)
``semeval``           en        full-text articles with author and reader keyphrases
``scielo``            pt        abstracts with author keywords (CC BY 4.0 records only)
====================  ========  =====================================================

A benchmark has no people: its texts are grouped into pseudo-people of
consecutive texts (5 abstracts, or 2 full-text articles), and pseudo-people
into pseudo-organisations of 5.
"""

from __future__ import annotations

import json
import re
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATASETS = ROOT / ".cache" / "datasets"

_HUB = "https://huggingface.co/datasets/{repo}/resolve/main/{file}"
#: Where each benchmark comes from: public copies on a dataset hub.
SOURCES = {
    "inspec": ("taln-ls2n/inspec", ["dev.jsonl", "test.jsonl", "README.md"]),
    "termith-eval": ("taln-ls2n/termith-eval", ["test.jsonl", "README.md"]),
    "semeval-2010-pre": ("taln-ls2n/semeval-2010-pre", ["train.jsonl", "test.jsonl", "README.md"]),
    "scielo-abstracts": (
        "eduagarcia/scielo_abstracts",
        ["data/train-00000-of-00008.parquet", "README.md"],
    ),
}


@dataclass
class LabText:
    """One text of one person: its identity, language and parts (``title``, ``abstract``, ``body``)."""

    person: int
    organisation: str
    text_id: str
    lang: str
    parts: dict[str, str]


@dataclass
class GoldTerm:
    """A term the lab scores against: its text in one language and whether it is a field term.

    ``canonical`` is the English form of a demo term (the key that merges its
    languages), ``themes`` the themes it belongs to.
    """

    text: str
    lang: str
    field: bool = True
    canonical: str = ""
    themes: tuple[str, ...] = ()
    kind: str = "keyphrase"


@dataclass
class LabCorpus:
    """People, their texts and the gold of one lab corpus."""

    name: str
    kind: str  # "demo" or "benchmark"
    languages: tuple[str, ...]
    people: list[str]
    texts: list[LabText]
    gold: list[GoldTerm]
    stemmed_gold: bool = False  # the gold is stemmed: match by stems
    has_bodies: bool = False
    world: object = None  # the demo world, for the pipeline measures
    person_mix: dict[int, dict[str, float]] = field(default_factory=dict)

    def texts_in(self, lang: str) -> list[LabText]:
        return [t for t in self.texts if t.lang == lang]


# ── demo worlds ─────────────────────────────────────────────────────────────


#: Every how many French texts one is read as English (see :func:`demo_corpus`).
MISDETECTED_EVERY = 10


def demo_corpus(
    size: str,
    seed: int = 0,
    *,
    languages: str = "en,fr",
    bodies: bool = False,
    misdetected: bool = False,
):
    """A demo world as a lab corpus: its cohort, its works' parts, its truth.

    With *misdetected*, one French text in :data:`MISDETECTED_EVERY` goes to
    the English stream, its title in capitals, as language detection sends a
    French paragraph or a shouted French title there; none of its terms is
    English gold.
    """
    from cartolex.demo import generate
    from cartolex.demo.writers import lexicon_truth

    world = generate(size=size, seed=seed, languages=languages, bodies=bodies)
    groups = {g.group_id: g for g in world.groups}
    by_person: dict[str, list] = {}
    for work in world.works:
        for pid in work.authors:
            by_person.setdefault(pid, []).append(work)
    people, texts, mix = [], [], {}
    french = sorted({w.work_id for w in world.works if w.language == "fr"})
    misread = set(french[::MISDETECTED_EVERY]) if misdetected else set()
    for person in world.cohort:
        works = sorted(by_person.get(person.person_id, []), key=lambda w: (w.year, w.work_id))
        if not works:
            continue
        index = len(people)
        people.append(person.person_id)
        mix[index] = dict(person.themes)
        for w in works:
            parts = {"title": w.title, "abstract": w.abstract}
            if w.body:
                parts["body"] = w.body
            lang = w.language
            if w.work_id in misread:
                lang, parts["title"] = "en", w.title.upper()
            texts.append(LabText(index, groups[person.group].acronym, w.work_id, lang, parts))
    gold = [
        GoldTerm(
            r["text"],
            r["lang"],
            r["field"],
            r.get("canonical", ""),
            tuple(r.get("themes", ())),
            r["kind"],
        )
        for r in lexicon_truth(world.languages, bodies=bodies)
        if r["kind"] != "template"
    ]
    name = f"demo {size}" + (" trilingual" if "pt" in world.languages else "")
    name += " bodies" if bodies else ""
    name += " misdetected" if misdetected else ""
    return LabCorpus(
        name=name,
        kind="demo",
        languages=tuple(world.languages),
        people=people,
        texts=texts,
        gold=gold,
        has_bodies=bodies,
        world=world,
        person_mix=mix,
    )


# ── benchmarks ──────────────────────────────────────────────────────────────


def fetch(name: str) -> Path:
    """Download a benchmark's files into ``.cache/datasets/<name>/`` (idempotent)."""
    repo, files = SOURCES[name]
    folder = DATASETS / name
    folder.mkdir(parents=True, exist_ok=True)
    for file in files:
        dest = folder / Path(file).name
        if dest.exists() and dest.stat().st_size > 0:
            continue
        with urllib.request.urlopen(_HUB.format(repo=repo, file=file), timeout=600) as resp:  # noqa: S310
            dest.write_bytes(resp.read())
    return folder


def _jsonl(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


_WS = re.compile(r"\s+")


def _clean(text: str) -> str:
    return _WS.sub(" ", text or "").strip()


def _benchmark(
    name: str,
    lang: str,
    rows: list[dict],
    *,
    per_person: int,
    stemmed: bool = False,
    body_key: str | None = None,
) -> LabCorpus:
    texts, gold_seen, gold = [], set(), []
    n_people = 0
    for i, row in enumerate(rows):
        person = i // per_person
        n_people = person + 1
        parts = {"title": _clean(row.get("title", "")), "abstract": _clean(row.get("abstract", ""))}
        if body_key:
            parts["body"] = _body(row, body_key)
        texts.append(LabText(person, f"o{person // 5:03d}", str(row["id"]), lang, parts))
        for phrase in row.get("keyphrases", []):
            phrase = _clean(str(phrase)).strip(" .;,")
            if phrase and phrase.lower() not in gold_seen:
                gold_seen.add(phrase.lower())
                gold.append(GoldTerm(phrase, lang))
    return LabCorpus(
        name=name,
        kind="benchmark",
        languages=(lang,),
        people=[f"p{i:04d}" for i in range(n_people)],
        texts=texts,
        gold=gold,
        stemmed_gold=stemmed,
        has_bodies=body_key is not None,
    )


def _body(row: dict, key: str) -> str:
    """A full text without its title and abstract, in paragraphs (blank-line separated)."""
    text = row.get(key, "") or ""
    abstract = _clean(row.get("abstract", ""))
    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
    body = []
    for line in lines:
        if _clean(line) in (abstract, _clean(row.get("title", ""))) or line.upper() == "ABSTRACT":
            continue
        body.append(line)
    return "\n\n".join(body)


def inspec() -> LabCorpus:
    folder = DATASETS / "inspec"
    rows = _jsonl(folder / "dev.jsonl") + _jsonl(folder / "test.jsonl")
    return _benchmark("inspec", "en", rows, per_person=5)


def termith() -> LabCorpus:
    return _benchmark(
        "termith", "fr", _jsonl(DATASETS / "termith-eval" / "test.jsonl"), per_person=5
    )


def semeval() -> LabCorpus:
    folder = DATASETS / "semeval-2010-pre"
    rows = _jsonl(folder / "train.jsonl") + _jsonl(folder / "test.jsonl")
    return _benchmark("semeval", "en", rows, per_person=2, stemmed=True, body_key="lvl-2")


def prepare_scielo(limit: int = 1000) -> Path:
    """Keep ``limit`` agronomy abstracts in Portuguese under CC BY 4.0, with ≥ 3 author keywords."""
    import pyarrow.parquet as pq

    folder = DATASETS / "scielo-abstracts"
    out = folder / "agronomy-ccby.jsonl"
    if out.exists():
        return out
    columns = [
        "scielo_id",
        "license",
        "first_category",
        "abstract_pt",
        "keyword_list_pt",
        "title_pt",
    ]
    df = pq.read_table(folder / "train-00000-of-00008.parquet", columns=columns).to_pandas()
    licence = df["license"].fillna("")
    ccby = licence.str.contains("Attribution 4.0 International") & ~licence.str.contains(
        "NonCommercial|NoDerivatives"
    )
    keep = (
        ccby
        & df["abstract_pt"].notna()
        & (df["abstract_pt"].str.len() > 400)
        & df["title_pt"].notna()
        & df["keyword_list_pt"].map(lambda k: k is not None and len(k) >= 3)
        & df["first_category"].isin(["AGRONOMY", "AGRICULTURE, MULTIDISCIPLINARY"])
    )
    sel = df[keep].sort_values("scielo_id").head(limit)
    with out.open("w", encoding="utf-8") as handle:
        for r in sel.itertuples():
            row = {
                "id": r.scielo_id,
                "title": r.title_pt,
                "abstract": r.abstract_pt,
                "keyphrases": [str(k) for k in r.keyword_list_pt],
            }
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    return out


def scielo() -> LabCorpus:
    rows = _jsonl(prepare_scielo())
    return _benchmark("scielo", "pt", rows, per_person=5)


BENCHMARKS = {"inspec": inspec, "termith": termith, "semeval": semeval, "scielo": scielo}


def available(name: str) -> bool:
    """Whether a benchmark's files are in the cache."""
    need = {
        "inspec": DATASETS / "inspec" / "test.jsonl",
        "termith": DATASETS / "termith-eval" / "test.jsonl",
        "semeval": DATASETS / "semeval-2010-pre" / "test.jsonl",
        "scielo": DATASETS / "scielo-abstracts" / "train-00000-of-00008.parquet",
    }[name]
    return need.exists() or (
        name == "scielo" and (DATASETS / "scielo-abstracts" / "agronomy-ccby.jsonl").exists()
    )
