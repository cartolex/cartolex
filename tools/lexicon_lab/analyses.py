# SPDX-License-Identifier: MIT
"""Parse a lab corpus once: the analyses of every text part, and the names in them.

Parts are parsed with the engine's own code (``analyse_texts``, the parse cache
in ``.cache/lexicon_lab/parse/``), so every variant scores the same analyses.
Names of people and places come from the model's named-entity recogniser, run
separately (the extraction does not load it) and cached in
``.cache/lexicon_lab/names/``.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path

from corpora import ROOT, LabCorpus

from cartolex.lexicon import language_models
from cartolex.lexicon.extract_raw import analyse_texts, person_pieces
from cartolex.lexicon.noun_phrases import TextAnalysis
from cartolex.lexicon.parse_cache import text_key
from cartolex.lexicon.scoring import TextUnit

CACHE = ROOT / ".cache" / "lexicon_lab"
#: Entity labels that name a person or a place, by model family.
NAME_KINDS = {"PERSON": "person", "PER": "person", "GPE": "place", "LOC": "place"}


@dataclass
class ParsedCorpus:
    """A corpus with its text units per language, the analysed paragraphs and timings."""

    corpus: LabCorpus
    units: dict[str, list[TextUnit]]
    paragraphs: dict[int, str]  # id(analysis) -> paragraph text
    parse_seconds: float
    names: dict[int, dict[str, str]] | None = None
    names_seconds: float = 0.0


def parse(corpus: LabCorpus, *, n_jobs: int = 1, names: bool = False) -> ParsedCorpus:
    """Every part of every text of *corpus*, analysed (and, with *names*, the names found)."""
    units: dict[str, list[TextUnit]] = {}
    paragraphs: dict[int, str] = {}
    t0 = time.perf_counter()
    for lang in corpus.languages:
        texts = corpus.texts_in(lang)
        if not texts:
            continue
        pieces = [
            {part: person_pieces(content) for part, content in t.parts.items() if content}
            for t in texts
        ]
        wanted = {p for parts in pieces for ps in parts.values() for p in ps}
        try:
            analyses = analyse_texts(lang, wanted, cache_dir=CACHE / "parse", n_jobs=n_jobs)
        finally:
            language_models.release(lang)
        by_key = {text_key(p): p for p in wanted}
        for key, a in analyses.items():
            paragraphs[id(a)] = by_key[key]
        units[lang] = [
            TextUnit(
                t.person,
                t.organisation,
                t.text_id,
                tuple(
                    (part, tuple(analyses[text_key(p)] for p in ps)) for part, ps in parts.items()
                ),
            )
            for t, parts in zip(texts, pieces, strict=True)
        ]
    parsed = ParsedCorpus(corpus, units, paragraphs, time.perf_counter() - t0)
    if names:
        t0 = time.perf_counter()
        parsed.names = find_names(parsed)
        parsed.names_seconds = time.perf_counter() - t0
    return parsed


def _names_file(model: str) -> Path:
    return CACHE / "names" / f"{model.replace('@', '-')}.jsonl"


def find_names(parsed: ParsedCorpus) -> dict[int, dict[str, str]]:
    """``{id(analysis): {name in lower case: "person" | "place"}}`` for every analysed paragraph."""
    import spacy

    out: dict[int, dict[str, str]] = {}
    for lang, units in parsed.units.items():
        model = language_models.require(lang)
        path = _names_file(model.identity)
        cached: dict[str, dict[str, str]] = {}
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                row = json.loads(line)
                cached[row["sha256"]] = row["names"]
        analyses = {id(a): a for u in units for _, part in u.parts for a in part}
        todo = sorted(
            {
                parsed.paragraphs[i]
                for i in analyses
                if hashlib.sha256(parsed.paragraphs[i].encode()).hexdigest() not in cached
            }
        )
        if todo:
            nlp = spacy.load(model.name, disable=["parser", "lemmatizer"])
            new = []
            for text, doc in zip(todo, nlp.pipe(todo, batch_size=64), strict=True):
                found = {
                    ent.text.lower(): NAME_KINDS[ent.label_]
                    for ent in doc.ents
                    if ent.label_ in NAME_KINDS
                }
                key = hashlib.sha256(text.encode()).hexdigest()
                cached[key] = found
                new.append(json.dumps({"sha256": key, "names": found}, ensure_ascii=False))
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write("\n".join(new) + "\n")
            del nlp
        for i in analyses:
            key = hashlib.sha256(parsed.paragraphs[i].encode()).hexdigest()
            if cached.get(key):
                out[i] = cached[key]
    return out


def all_analyses(units: list[TextUnit]) -> list[TextAnalysis]:
    return [a for u in units for _, part in u.parts for a in part]
