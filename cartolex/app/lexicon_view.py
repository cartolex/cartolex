# SPDX-License-Identifier: MIT
"""The lexicon: what the last ``keywords.build`` made of the candidates, and its word cloud.

One row per keyword of the space (a concept of the vocabulary someone's texts use): its
term in each display language (twins merged into one keyword), its score and rank, the
people whose row of the space holds it, the texts of its candidates, its category, the
theme it sits on, its forms and the candidates it gathers (``(term, language)``, what the
keyword decisions name). Everything is read from the build's files and cached by the runs
it reads; nothing is written.

The word cloud (:func:`word_cloud`) is drawn with the ``wordcloud`` package from the most
important keywords (by score or by people), in the vendored Lato typeface embedded in the
SVG, coloured by the top-level theme (the theme editor's hue families) or by category,
for a light or a dark page.
"""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from typing import Any

import numpy as np

#: How many keywords the word cloud shows, the most important first.
CLOUD_WORDS = 200
#: The typeface of the word cloud (SIL Open Font License, see ``_data/fonts/OFL.txt``).
CLOUD_FONT = Path(__file__).resolve().parent.parent / "_data" / "fonts" / "Lato-Semibold.ttf"
#: The hue family of each category in the word cloud (``--cx-hue-<n + 1>``).
CATEGORY_HUE = {"concept": 4, "method": 1, "object": 5, "place": 8, "field": 2}
_TOKENS = Path(__file__).resolve().parent / "static" / "css" / "tokens.css"


def _rows(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with open(path, encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def _record(ctx: Any, stage: str) -> Any:
    from cartolex.build.records import read_record

    return read_record(ctx.layout, stage)


def lexicon(runtime: Any, ctx: Any) -> dict[str, Any] | None:
    """The lexicon of the last ``keywords.build`` (``None`` before it ran), cached by the runs
    of the extraction, the build and the applied themes."""
    key = _runs(ctx)
    if key is None:
        return None
    return runtime.table_cache.get(
        ("lexicon", ctx.id, *key), lambda: _compute(runtime, ctx, key[0])
    )


def _runs(ctx: Any) -> tuple[str, str | None, str | None] | None:
    """The runs the lexicon is read from: the build's, the extraction's, the applied themes'."""
    build = _record(ctx, "keywords.build")
    if build is None:
        return None
    extract = _record(ctx, "keywords.extract")
    applied = _record(ctx, "themes.apply")
    return (
        build.run_id,
        extract.run_id if extract else None,
        applied.run_id if applied else None,
    )


def _compute(runtime: Any, ctx: Any, build_run: str) -> dict[str, Any]:
    from cartolex.atlas.model_files import load_person_terms

    from .routes.keywords import extracted

    folder = ctx.layout.stage("keywords.build")
    languages = list(ctx.project.config.languages.display) or [
        ctx.project.config.languages.reference
    ]
    people: dict[str, int] = {}
    matrices = folder / "models" / "person_terms.json"
    if matrices.is_file():
        score, _, terms, _ = load_person_terms(matrices)
        used = np.diff(score.tocsc().indptr)
        for term, n in zip(terms, used.tolist(), strict=True):
            if n:
                people[str(term).lower()] = people.get(str(term).lower(), 0) + int(n)
    lang_of = {
        r["concept"]: r.get("lang") or "" for r in _rows(folder / "keywords_global_refined.csv")
    }
    categories: dict[str, str] = {}
    cats = folder / "categories.json"
    if cats.is_file():
        categories = json.loads(cats.read_text(encoding="utf-8"))
    members: dict[str, list[dict[str, str]]] = {}
    for r in _rows(folder / "concept_terms.csv"):
        members.setdefault(r["concept"], []).append({"term": r["term"], "language": r["language"]})
    candidates, _ = extracted(runtime, ctx)
    by_key = {(c["term"], c["language"]): c for c in candidates}
    nodes, node_of = _themes(ctx)
    items: list[dict[str, Any]] = []
    for rank, r in enumerate(_rows(folder / "keywords_global_refined_pairs.csv"), start=1):
        concept = r["concept"]
        n_people = people.get(concept.lower(), 0)
        if matrices.is_file() and not n_people:
            continue  # not in the space: nobody's texts use it
        terms = {lang: r.get(f"term_{lang}") or concept for lang in languages}
        cands = members.get(concept) or [{"term": concept, "language": lang_of.get(concept, "")}]
        forms: list[str] = []
        texts = 0
        for c in cands:
            found = by_key.get((c["term"], c["language"]))
            if found is not None:
                texts += found["texts"]
                forms += [f for f in found["forms"] if f not in forms]
        lower = concept.lower()
        category = categories.get(lower) or next(
            (categories[t.lower()] for t in terms.values() if t.lower() in categories), None
        )
        items.append(
            {
                "concept": concept,
                "rank": rank,
                "terms": terms,
                "language": lang_of.get(concept, ""),
                "score": float(r.get("total_score") or 0),
                "people": n_people,
                "texts": texts,
                "category": category,
                "node": node_of.get(lower),
                "forms": forms[:12],
                "candidates": cands,
            }
        )
    return {"run": build_run, "items": items, "languages": languages, "nodes": nodes}


def _themes(ctx: Any) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    """The applied tree's nodes (names, top-level node, hue family) and each keyword's node."""
    folder = ctx.layout.stage("themes.apply")
    path = folder / "themes_applied.json"
    if not path.is_file():
        return {}, {}
    doc = json.loads(path.read_text(encoding="utf-8"))
    parent = {n["id"]: n.get("parent") for n in doc["nodes"]}

    def top(node: str) -> str:
        while parent.get(node):
            node = parent[node]
        return node

    tops = [n["id"] for n in doc["nodes"] if n.get("parent") is None]
    hue = _hue_families(tops)
    nodes = {
        n["id"]: {"names": n.get("names") or {}, "top": top(n["id"]), "hue": hue[top(n["id"])]}
        for n in doc["nodes"]
    }
    node_of = {
        r["term"].lower(): r["node"]
        for r in _rows(folder / "theme_keywords.csv")
        if r.get("node") and r["node"] in nodes
    }
    return nodes, node_of


def _hue_families(tops: list[str]) -> dict[str, int]:
    """Each top-level node's hue family, as the theme editor gives them: ``s<k>`` keeps family
    ``k``, any other top-level node takes the next free one (0-based, of 12)."""
    hue: dict[str, int] = {}
    used: set[int] = set()
    for node in tops:
        m = re.fullmatch(r"s(\d+)", node)
        if m:
            hue[node] = int(m.group(1)) % 12
            used.add(hue[node])
    free = 0
    for node in tops:
        if node in hue:
            continue
        while free % 12 in used and len(used) < 12:
            free += 1
        hue[node] = free % 12
        used.add(free % 12)
        free += 1
    return hue


def node_label(nodes: dict[str, dict[str, Any]], node: str | None, language: str) -> str:
    """A node's name in *language* (else its first name), under its top-level node's."""
    if not node or node not in nodes:
        return ""

    def name(n: str) -> str:
        names = nodes[n]["names"]
        return str(names.get(language) or next(iter(names.values()), n))

    top = nodes[node]["top"]
    return name(node) if top == node else f"{name(top)} › {name(node)}"


# ── the word cloud ──────────────────────────────────────────────────────────


def _palette(theme: str) -> tuple[list[str], str]:
    """The twelve hue families and the muted text colour of the light or dark page, read
    from the interface's tokens."""
    text = _TOKENS.read_text(encoding="utf-8")
    hues = re.findall(r"--cx-hue-(\d+):\s*(#[0-9a-fA-F]{6})", text)
    muted = re.findall(r"--cx-text-muted:\s*(#[0-9a-fA-F]{6})", text)
    light = [h for _, h in hues[:12]]
    dark = [h for _, h in hues[12:24]] or light
    if theme == "dark":
        return dark, (muted[1] if len(muted) > 1 else muted[0] if muted else "#a0a0a0")
    return light, (muted[0] if muted else "#606060")


def word_cloud(
    runtime: Any,
    ctx: Any,
    *,
    by: str = "score",
    theme: str = "light",
    colour: str = "theme",
    language: str = "",
) -> str | None:
    """The word cloud of the lexicon as SVG (``None`` before the vocabulary is built), cached
    by the runs it is made from and these options."""
    data = lexicon(runtime, ctx)
    if data is None:
        return None
    language = language if language in data["languages"] else data["languages"][0]
    key = ("lexicon-cloud", ctx.id, *(_runs(ctx) or ()), by, theme, colour, language)
    return runtime.table_cache.get(
        key, lambda: draw_cloud(data, by=by, theme=theme, colour=colour, language=language)
    )


def draw_cloud(data: dict[str, Any], *, by: str, theme: str, colour: str, language: str) -> str:
    """The SVG of the *data*'s most important keywords (see :func:`word_cloud`)."""
    from wordcloud import WordCloud

    weight = "people" if by == "people" else "score"
    items = sorted(data["items"], key=lambda i: (-i[weight], i["rank"]))[:CLOUD_WORDS]
    hues, muted = _palette(theme)
    frequencies: dict[str, float] = {}
    colour_of: dict[str, str] = {}
    for item in items:
        word = item["terms"].get(language) or item["concept"]
        if word in frequencies or item[weight] <= 0:
            continue
        frequencies[word] = float(item[weight])
        if colour == "category":
            hue = CATEGORY_HUE.get(item["category"] or "")
        else:
            node = data["nodes"].get(item["node"] or "")
            hue = node["hue"] if node else None
        colour_of[word] = hues[hue] if hue is not None else muted
    width, height = 1400, 760
    if not frequencies:
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
            f'viewBox="0 0 {width} {height}"></svg>'
        )
    cloud = WordCloud(
        width=width,
        height=height,
        font_path=str(CLOUD_FONT),
        background_color=None,
        mode="RGBA",
        prefer_horizontal=0.95,
        max_words=CLOUD_WORDS,
        relative_scaling=0.35,
        min_font_size=10,
        max_font_size=78,
        margin=8,
        random_state=7,
        collocations=False,
        normalize_plurals=False,
        color_func=lambda word, **_: colour_of.get(word, muted),
    )
    cloud.generate_from_frequencies(frequencies)
    svg = cloud.to_svg(embed_font=True, optimize_embedded_font=True)
    return _accessible(_fitted(svg, cloud), list(frequencies)[:12])


def _fitted(svg: str, cloud: Any) -> str:
    """*svg* with each word held to the width its layout measured (``textLength``): a
    browser's text runs a little wider than the layout's, and words would touch or leave the
    picture."""
    from PIL import ImageFont

    widths = []
    for (word, _), font_size, _, _, _ in cloud.layout_:
        font = ImageFont.truetype(cloud.font_path, int(font_size * cloud.scale))
        (size_x, _), _ = font.font.getsize(word)
        widths.append(size_x)
    it = iter(widths)

    def fit(m: re.Match) -> str:
        width = next(it, None)
        if width is None:
            return m.group(0)
        return f'{m.group(1)} textLength="{width}" lengthAdjust="spacingAndGlyphs"{m.group(2)}'

    return re.sub(r"(<text[^>]*?)(>)", fit, svg)


def _accessible(svg: str, words: list[str]) -> str:
    """*svg* with a viewBox (it scales with its box), no background rectangle, and a title."""
    from xml.sax.saxutils import escape

    m = re.search(r'<svg[^>]*width="(\d+)"[^>]*height="(\d+)"', svg)
    if m and "viewBox" not in svg[: svg.find(">")]:
        w, h = m.group(1), m.group(2)
        svg = svg.replace("<svg ", f'<svg viewBox="0 0 {w} {h}" ', 1)
    svg = re.sub(r'<rect[^>]*fill="(none|rgba?\([^)]*\))"[^>]*/>', "", svg, count=1)
    title = f"<title>{escape(', '.join(words))}</title>"
    return re.sub(r"(<svg[^>]*>)", r"\1" + title, svg, count=1)


# ── the export ──────────────────────────────────────────────────────────────


def lexicon_csv(data: dict[str, Any], language: str) -> str:
    """The lexicon as CSV: rank, a term per display language, score, people, texts,
    category, theme (in *language*), forms."""
    import io

    out = io.StringIO()
    writer = csv.writer(out)
    langs = data["languages"]
    writer.writerow(
        ["rank", *[f"term_{lang}" for lang in langs], "language", "score", "people", "texts"]
        + ["category", "theme", "forms"]
    )
    for item in data["items"]:
        writer.writerow(
            [item["rank"], *[item["terms"].get(lang, "") for lang in langs], item["language"]]
            + [f"{item['score']:.6g}", item["people"], item["texts"], item["category"] or ""]
            + [node_label(data["nodes"], item["node"], language), "|".join(item["forms"])]
        )
    return out.getvalue()
