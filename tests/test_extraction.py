# SPDX-License-Identifier: MIT
"""The candidate extraction end to end, with the real language models.

A small invented trilingual corpus: French, Portuguese and English texts on
coastal topics. The terms with short words (« trait de côte », « linha de
costa ») must come out whole and reach the attribution; a language without
text or without enough people must not stop the run; the output must not
depend on the number of worker processes or on the parse cache.
"""

from __future__ import annotations

import csv
import logging
import shutil
from pathlib import Path

import pandas as pd
import pytest

from cartolex.context import RunContext
from cartolex.lexicon import KeywordsConfig, extract_raw, run_pipeline_stage_1, run_pipeline_stage_3
from cartolex.lexicon import language_models as lm
from cartolex.lexicon.llm_triage import _load_global_terms

FR = [
    "Le trait de côte recule sous l'effet des tempêtes. La masse d'eau côtière se réchauffe "
    "et les zones à risque s'étendent le long du littoral.",
    "Nous suivons le trait de côte par imagerie aérienne. La masse d'eau du lagon et les "
    "zones à risque sont cartographiées chaque année.",
    "L'érosion menace le trait de côte des plages sableuses. Les zones à risque et la masse "
    "d'eau de la baie font l'objet d'un suivi régulier.",
]
PT = [
    "A linha de costa recua com as tempestades. O nível do mar sobe e a erosão costeira "
    "avança sobre as praias arenosas.",
    "Monitoramos a linha de costa com imagens aéreas. O nível do mar e a erosão costeira "
    "são medidos todos os anos.",
    "A erosão costeira ameaça a linha de costa das praias. O nível do mar é registrado "
    "por marégrafos desde a década passada.",
]
EN = [
    "Tide gauge records show that coastal erosion accelerates during storms. Sediment "
    "transport along the beach is measured every season.",
    "We compare tide gauge records with satellite altimetry. Coastal erosion and sediment "
    "transport are mapped from aerial surveys.",
    "Coastal erosion shapes sandy beaches. Tide gauge records and sediment transport "
    "estimates are combined in a single model.",
]


def write_corpus(ws: Path, people: list[tuple[str, list[str]]]) -> None:
    """A corpus-contract workspace: one text per person (``(name, paragraphs)``)."""
    corpus = ws / "automatic_data" / "corpus_manual"
    corpus.mkdir(parents=True, exist_ok=True)
    (ws / "manual_data").mkdir(exist_ok=True)
    rows = []
    for i, (name, paragraphs) in enumerate(people):
        path = corpus / f"doc{i:02d}.txt"
        path.write_text("\n\n".join(paragraphs) + "\n", encoding="utf-8")
        rows.append(
            {
                "last_name": name,
                "first_name": "Test",
                "unit": "G1",
                "txt_path": f"automatic_data/corpus_manual/{path.name}",
            }
        )
    with (ws / "manual_index.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def trilingual(ws: Path) -> None:
    people = [(f"Fr{i}", [t]) for i, t in enumerate(FR)]
    people += [(f"Pt{i}", [t]) for i, t in enumerate(PT)]
    people += [(f"En{i}", [t]) for i, t in enumerate(EN)]
    write_corpus(ws, people)


def settings(**changes) -> KeywordsConfig:
    base = dict(
        corpus_languages=("fr", "pt", "en"),
        display_languages=("fr", "pt", "en"),
        min_df=2,
        max_df=1.0,
        kw_recency_years=0,
        use_llm=False,
    )
    base.update(changes)
    return KeywordsConfig(**base)


def raw(ctx: RunContext, lang: str) -> pd.DataFrame:
    return pd.read_csv(ctx.paths.raw_terms_csv(lang), keep_default_na=False)


@pytest.mark.models("en", "fr", "pt")
def test_terms_with_short_words_are_kept_whole_through_the_pipeline(tmp_path: Path) -> None:
    trilingual(tmp_path)
    ctx = RunContext.for_workspace(tmp_path, settings(), now_year=2026)
    run_pipeline_stage_1(ctx)
    fr, pt, en = raw(ctx, "fr"), raw(ctx, "pt"), raw(ctx, "en")
    assert list(fr.columns) == extract_raw.RAW_COLUMNS
    assert {"trait de côte", "masse d'eau", "zones à risque"} <= set(fr["term"])
    assert {"linha de costa", "nível do mar", "erosão costeira"} <= set(pt["term"])
    assert {"tide gauge records", "sediment transport", "coastal erosion"} <= set(en["term"])
    row = fr.set_index("term").loc["trait de côte"]
    assert row["len"] == 3 and row["score_len"] == pytest.approx(row["score"] * 5)

    # The triage's safety net keeps them.
    terms, _ = _load_global_terms(ctx.paths.global_terms_csv)
    assert {"trait de côte", "linha de costa", "masse d'eau", "zones à risque"} <= set(terms)

    # Consolidation counts them in the texts, through every surface form.
    run_pipeline_stage_3(ctx)
    aliases = pd.read_csv(ctx.paths.term_aliases_csv)
    assert {"trait de côte", "linha de costa", "masse eau", "zones risque"} <= set(aliases["alias"])
    persons = pd.read_csv(ctx.paths.person_terms_csv)
    counted = set(persons["term"])
    assert {"trait de côte", "linha de costa", "masse d'eau"} <= counted


@pytest.mark.models("en", "fr", "pt")
def test_a_language_below_the_window_gives_an_empty_table(tmp_path: Path, caplog) -> None:
    """One Portuguese text among English ones: no candidate reaches min_df, nothing raises."""
    people = [(f"En{i}", [t]) for i, t in enumerate(EN)] + [("Pt0", [PT[0]])]
    write_corpus(tmp_path, people)
    ctx = RunContext.for_workspace(tmp_path, settings(), now_year=2026)
    with caplog.at_level(logging.WARNING, logger="cartolex.lexicon.extract_raw"):
        run_pipeline_stage_1(ctx)
    assert raw(ctx, "pt").empty and list(raw(ctx, "pt").columns) == extract_raw.RAW_COLUMNS
    assert "No text in corpus language 'fr'" in caplog.text
    assert "[pt] No candidate term within the document-frequency window" in caplog.text
    assert len(raw(ctx, "en")) > 0


@pytest.mark.models("en", "fr", "pt")
def test_output_does_not_depend_on_workers_or_on_the_cache(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "source"
    trilingual(source)
    # One text per parser batch: the two workers get several batches each.
    monkeypatch.setattr(extract_raw, "PARSE_BATCH", 1)
    outputs = {}
    for name, jobs in (("serial", 1), ("parallel", 2)):
        ws = tmp_path / name
        shutil.copytree(source, ws)
        ctx = RunContext.for_workspace(ws, settings(extraction_n_jobs=jobs), now_year=2026)
        run_pipeline_stage_1(ctx)
        outputs[name] = {
            lang: ctx.paths.raw_terms_csv(lang).read_bytes() for lang in ("fr", "pt", "en")
        }
    assert outputs["serial"] == outputs["parallel"]

    # A second run reads every analysis from the parse cache: nothing is parsed.
    def no_parsing(*args, **kwargs):
        raise AssertionError("a cached text was parsed again")

    monkeypatch.setattr(extract_raw, "parse_texts", no_parsing)
    ws = tmp_path / "serial"
    ctx = RunContext.for_workspace(ws, settings(), now_year=2026)
    run_pipeline_stage_1(ctx)
    again = {lang: ctx.paths.raw_terms_csv(lang).read_bytes() for lang in ("fr", "pt", "en")}
    assert again == outputs["serial"]
    assert any(ctx.paths.parse_cache_dir.rglob("part-*.jsonl"))


@pytest.mark.models("fr", "pt")
def test_a_phrase_after_an_elided_word_with_the_real_models() -> None:
    """The word after « l' », « d' » starts a phrase, whichever apostrophe and model."""
    from cartolex.lexicon import noun_phrases as npx

    def found(lang: str, text: str) -> dict[str, tuple[str, ...]]:
        a = npx.analyse(lm.load(lang)(text), lang)
        spans = npx.spans(a, npx.PATTERNS[lang], npx.lemma_table([a]))
        return {s.surface: s.containers for s in spans}

    fr = found(
        "fr",
        "Le choix de l'apprentissage profond et des systèmes d’information géographique.",
    )
    assert {"choix de l'apprentissage profond", "systèmes d’information géographique"} <= set(fr)
    assert fr["apprentissage profond"] and fr["information géographique"]
    # The Portuguese model keeps « d'água » as one token: it is split into « d' » and « água ».
    pt = found("pt", "A coluna d'água e a lâmina d’água no solo arenoso.")
    assert {"coluna d'água", "lâmina d’água", "água no solo"} <= set(pt)
    assert not any(s.startswith(("d'", "d’")) for s in pt)


def test_a_missing_model_stops_the_run_before_any_parsing(tmp_path: Path, monkeypatch) -> None:
    trilingual(tmp_path)
    versions = {"en": "3.8.0", "fr": "3.8.0", "pt": None}
    monkeypatch.setattr(lm, "installed_version", lambda lang: versions[lang])

    def no_parsing(*args, **kwargs):
        raise AssertionError("parsing started before every model was checked")

    monkeypatch.setattr(extract_raw, "parse_texts", no_parsing)
    ctx = RunContext.for_workspace(tmp_path, settings(), now_year=2026)
    with pytest.raises(lm.LanguageModelMissing, match="pt_core_news_md"):
        run_pipeline_stage_1(ctx)


def test_a_language_without_text_needs_no_model(tmp_path: Path, monkeypatch) -> None:
    """The default languages on an English-only corpus: the French model is not required."""
    write_corpus(tmp_path, [(f"En{i}", [t]) for i, t in enumerate(EN)])
    checked = []

    def require(lang):
        checked.append(lang)
        raise lm.LanguageModelMissing(lang, "stop here")

    monkeypatch.setattr(lm, "require", require)
    ctx = RunContext.for_workspace(tmp_path, KeywordsConfig(kw_recency_years=0), now_year=2026)
    with pytest.raises(lm.LanguageModelMissing):
        run_pipeline_stage_1(ctx)
    assert checked == ["en"]
