# SPDX-License-Identifier: MIT
"""The triage kit's pre-sort: code does the bulk, the assistant judges groups.

The candidates of a triage bundle are sorted before the assistant reads them:

- **obvious junk** is flagged by patterns (:func:`flag`): a number, a stray
  symbol, a repeated word, a phrase that starts or ends on a function word,
  words of research discourse only (« further work »), a very short or a very
  long one. Flagged candidates form groups of their own, one per pattern,
  confirmed or corrected in a line; so do the long phrases one text or one
  person uses (a paper's own wording), and the chemical formulas and acronyms
  (kept whole: ``CO`` is not ``CO2``);
- the others are grouped by **family**: the same head word (the last word of
  an English phrase, the first noun of a French, Portuguese or Spanish one,
  plural and accents aside), in the same band and language, so a whole family
  is decided at once (« every ‹X› assay: a method, keep ») and only a mixed
  family term by term;
- the candidates alone in their family (or in a family of two) are grouped by **theme cluster**: the candidates
  the same people use (k-means on who uses what, people as opaque numbers);

Everything here is deterministic: the same bundle gives the same groups, the
same ids and the same parts in every session, so a session can stop and
another resume it (:meth:`cartolex.copilot.triage.TriageSession.resume`).
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np

__all__ = [
    "BAND_ORDER",
    "Group",
    "flag",
    "head_word",
    "is_formula",
    "junk_reason",
    "sort_candidates",
    "twin_pairs",
]

#: The order the bands are judged in: to check first, then set aside (a rescue), then kept.
BAND_ORDER = ("check", "aside", "kept")
#: The largest group shown at once.
MAX_GROUP = 40
#: The smallest family grouped on its own (smaller ones join their theme cluster's group).
MIN_FAMILY = 3
#: The size of a group of candidates left alone (grouped by theme cluster).
THEME_CHUNK = 25
#: Languages whose phrases put the head noun first.
HEAD_FIRST = ("fr", "pt", "es", "it", "ca", "ro")
#: Words a phrase does not start or end on (a phrase cut out of a longer one).
EDGE_WORDS: dict[str, frozenset[str]] = {
    "en": frozenset(
        "a an the of and or in on at to for with by from as into than that which who whose "
        "is are was were be been its their our this these those such".split()
    ),
    "fr": frozenset(
        "le la les l un une des du de d et ou en au aux à a par pour sur dans avec sans sous "
        "entre que qui dont ce ces cet cette est sont son sa ses leur leurs".split()
    ),
    "pt": frozenset(
        "o a os as um uma de do da dos das e ou em no na nos nas ao aos à às por para com sem "
        "sob entre que qual cujo é são seu sua seus suas".split()
    ),
    "es": frozenset(
        "el la los las un una de del y o en al a por para con sin sobre entre que cual cuyo "
        "es son su sus".split()
    ),
}
_ELISION = re.compile(r"^(?:l|d|qu|j|m|n|s|t|c)['’]", re.IGNORECASE)
_WORD = re.compile(r"[^\W_]+(?:[-'’][^\W_]+)*", re.UNICODE)
_ALLOWED = re.compile(r"^[\w\s\-'’.,/()+]+$", re.UNICODE)


def _fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", str(text).casefold())
    return "".join(c for c in text if not unicodedata.combining(c))


def _words(term: str) -> list[str]:
    return [w.casefold() for w in _WORD.findall(term)]


def _stem(word: str, lang: str) -> str:
    """A word without accents and its plural ending (a light, per-language rule)."""
    w = _fold(word)
    w = _ELISION.sub("", w)
    if len(w) <= 3:
        return w
    if lang == "en":
        if w.endswith("ies") and len(w) > 4:
            return w[:-3] + "y"
        if w.endswith(("ses", "xes", "ches", "shes")):
            return w[:-2]
        if w.endswith("s") and not w.endswith(("ss", "us", "is")):
            return w[:-1]
        return w
    if w.endswith("aux") and lang == "fr":
        return w[:-3] + "al"
    if w.endswith(("s", "x")) and not w.endswith(("ss", "us")):
        return w[:-1]
    return w


def head_word(term: str, lang: str) -> str:
    """The head word of a candidate, stemmed: the word its family is named after.

    The last word of an English (or German, Dutch …) phrase, the first word
    that is not a function word of a French, Portuguese or Spanish one.
    """
    words = _words(term)
    if not words:
        return _fold(term)
    if lang in HEAD_FIRST:
        edge = EDGE_WORDS.get(lang, frozenset())
        for w in words:
            bare = _ELISION.sub("", w)
            if bare and bare not in edge:
                return _stem(bare, lang)
        return _stem(words[0], lang)
    return _stem(words[-1], lang)


#: Words of research discourse and generic qualifiers: a phrase made of them only
#: (« further work », « new approach », « rôle de l'étude ») says nothing of a field.
DISCOURSE: dict[str, frozenset[str]] = {
    "en": frozenset(
        "study paper work result finding approach aspect issue role effect impact importance "
        "case context framework perspective insight evidence overview review understanding "
        "implication use question problem objective aim goal purpose part way type kind number "
        "lack need basis point topic factor difference comparison presence absence influence "
        "contribution application example interest attention focus consequence advance "
        "challenge opportunity strategy method methodology tool data analysis "
        "new novel recent different various important significant main key potential possible "
        "further first general specific future previous overall major "
        "particular common other same several many".split()
    ),
    "fr": frozenset(
        "etude travail travaux resultat approche aspect enjeu role effet impact importance cas "
        "contexte cadre perspective apport comprehension question probleme objectif but partie "
        "type nombre manque besoin base point sujet facteur difference comparaison presence "
        "absence influence contribution application exemple interet aide mise prise compte "
        "evidence consequence avancee defi strategie methode methodologie outil donnee "
        "analyse nouveau nouvel nouvelle recent recente different differente important "
        "importante principal principale general generale particulier particuliere premier "
        "premiere futur possible potentiel potentielle autre meme plusieur".split()
    ),
    "pt": frozenset(
        "estudo trabalho resultado abordagem aspecto papel efeito impacto importancia caso "
        "contexto quadro perspectiva contribuicao compreensao questao problema objetivo parte "
        "tipo numero falta base ponto fator diferenca comparacao presenca ausencia influencia "
        "aplicacao exemplo interesse consequencia avanco desafio estrategia metodo "
        "metodologia ferramenta dado analise novo nova recente diferente importante principal "
        "geral primeiro primeira futuro possivel potencial outro mesmo "
        "varios".split()
    ),
}
#: A chemical formula, an isotope or an acronym: kept whole, never merged into another.
_FORMULA = re.compile(r"^[a-zδΔ]?[\d₀-₉]*(?:[A-Z][a-z]?[\d₀-₉]*){1,6}[+\-−⁺⁻]?$")


def is_formula(term: str) -> bool:
    """Whether *term* is written as a chemical formula, an isotope or an acronym (``CO2``,
    ``N2O``, ``δ18O``, ``DNA``): two such terms are never the same term."""
    text = str(term).strip()
    return bool(_FORMULA.match(text)) and sum(c.isupper() for c in text) >= 1 and len(text) <= 12


def _discourse(words: Sequence[str], lang: str) -> bool:
    known = DISCOURSE.get(lang)
    edge = EDGE_WORDS.get(lang, frozenset())
    if not known:
        return False
    content = [_stem(_ELISION.sub("", w), lang) for w in words]
    content = [w for w in content if w and w not in edge]
    return bool(content) and all(w in known or _fold(w) in known for w in content)


def flag(term: str, lang: str, *, people: int = 0, texts: int = 0) -> tuple[str, str]:
    """The pattern group a candidate falls in, as ``(kind, label)``; ``("", "")``: none.

    ``("formula", "formula")``: a chemical formula, an isotope or an acronym (kept whole);
    ``("pattern", why)``, a hint of junk the assistant confirms, where *why* is
    ``symbol`` (a character a keyword does not hold), ``short`` (under three
    letters), ``number`` (a word of digits only), ``repeat`` (the same word twice
    in a row), ``edge`` (starts or ends on a function word), ``discourse`` (words
    of research discourse or generic qualifiers only: « further work »),
    ``long`` (more than six words); ``("specific", "one text")``: a phrase of
    three words or more used in one text or by one person, likely a paper's own.
    """
    text = str(term).strip()
    if is_formula(text):
        return "formula", "formula"
    if not _ALLOWED.match(text):
        return "pattern", "symbol"
    words = _words(text)
    if not words or sum(c.isalpha() for c in text) < 3:
        return "pattern", "short"
    if any(w.isdigit() for w in words):
        return "pattern", "number"
    if any(a == b for a, b in zip(words, words[1:], strict=False)):
        return "pattern", "repeat"
    edge = EDGE_WORDS.get(lang, frozenset())
    first = _ELISION.sub("", words[0]) or words[0]
    if words[0] in edge or first in edge or words[-1] in edge or re.search(r"['’]$", text):
        return "pattern", "edge"
    if _discourse(words, lang):
        return "pattern", "discourse"
    if len(words) > 6:
        return "pattern", "long"
    if len(words) >= 3 and (0 < texts <= 1 or 0 < people <= 1):
        return "specific", "one text"
    return "", ""


def junk_reason(term: str, lang: str) -> str:
    """The junk pattern of a candidate (``""``: none): :func:`flag`'s label for a pattern."""
    kind, label = flag(term, lang)
    return label if kind == "pattern" else ""


@dataclass
class Group:
    """Candidates decided together: a pattern, the formulas, a paper's own phrases, a family,
    or a theme cluster's candidates left alone."""

    id: str
    kind: str  # "pattern", "formula", "specific", "family" or "theme"
    label: str
    band: str
    lang: str
    cluster: int
    members: list[int] = field(default_factory=list)
    part: int = 1

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "label": self.label,
            "band": self.band,
            "lang": self.lang,
            "cluster": self.cluster,
            "part": self.part,
            "members": list(self.members),
        }


def _clusters(V: Any, eligible: np.ndarray, k: int) -> np.ndarray:
    """A theme cluster per candidate (k-means on who uses it; -1: used by nobody)."""
    from sklearn.cluster import KMeans
    from sklearn.decomposition import TruncatedSVD

    labels = np.full(V.shape[0], -1, dtype=np.int64)
    rows = np.flatnonzero(eligible)
    if len(rows) < 2 or k < 2:
        labels[rows] = 0
        return labels
    X = V[rows]
    dims = min(50, X.shape[1] - 1, len(rows) - 1)
    if dims >= 2:
        X = TruncatedSVD(n_components=dims, random_state=0).fit_transform(X)
        X /= np.maximum(np.linalg.norm(X, axis=1, keepdims=True), 1e-12)
    else:
        X = np.asarray(X.todense())
    k = min(k, len(rows))
    labels[rows] = KMeans(n_clusters=k, n_init=3, random_state=0).fit_predict(X)
    return labels


def _chunks(members: list[int], size: int) -> list[list[int]]:
    n = max(1, -(-len(members) // size))
    per = -(-len(members) // n)
    return [members[i : i + per] for i in range(0, len(members), per)]


def sort_candidates(
    items: Sequence[Mapping[str, Any]],
    V: Any,
    *,
    parts: int = 1,
    clusters: int | None = None,
) -> tuple[list[Group], np.ndarray]:
    """The groups of the candidates, in the order to judge them, and each candidate's cluster.

    *V* holds who uses each candidate (rows: candidates, normalised). *parts*
    splits the groups into that many parts by theme cluster, of similar sizes.
    """
    n = len(items)
    people = np.array([int(it.get("people") or 0) for it in items])
    k = clusters if clusters is not None else int(min(40, max(2, round(n / 250))))
    cluster = _clusters(V, people > 0, k)
    flags = [
        flag(
            it["term"],
            it["lang"],
            people=int(it.get("people") or 0),
            texts=int(it.get("texts") or 0),
        )
        for it in items
    ]
    groups: list[Group] = []

    def order(members: list[int]) -> list[int]:
        return sorted(members, key=lambda i: (-people[i], items[i]["term"]))

    def main_cluster(members: list[int]) -> int:
        return Counter(int(cluster[i]) for i in members).most_common(1)[0][0]

    by_band: dict[str, list[int]] = defaultdict(list)
    for i, it in enumerate(items):
        by_band[it["band"] if it["band"] in BAND_ORDER else "check"].append(i)
    for band in BAND_ORDER:
        members = by_band.get(band, [])
        # Patterns, formulas, a paper's own phrases: one group per kind, label and language.
        flagged: dict[tuple[str, str, str], list[int]] = defaultdict(list)
        for i in members:
            if flags[i][0]:
                flagged[(*flags[i], items[i]["lang"])].append(i)
        for (kind, label, lang), found in sorted(flagged.items()):
            for chunk in _chunks(order(found), MAX_GROUP):
                groups.append(Group("", kind, label, band, lang, main_cluster(chunk), chunk))
        # Families: the same head word, band and language.
        family: dict[tuple[str, str], list[int]] = defaultdict(list)
        for i in members:
            if not flags[i][0]:
                family[(items[i]["lang"], head_word(items[i]["term"], items[i]["lang"]))].append(i)
        alone: dict[tuple[str, int], list[int]] = defaultdict(list)
        for (lang, head), found in sorted(family.items(), key=lambda kv: (-len(kv[1]), kv[0])):
            if len(found) < MIN_FAMILY:
                for i in found:
                    alone[(lang, int(cluster[i]))].append(i)
                continue
            found = order(found)
            if len(found) > MAX_GROUP:  # a large family: split by theme cluster, then in chunks
                by_cluster: dict[int, list[int]] = defaultdict(list)
                for i in found:
                    by_cluster[int(cluster[i])].append(i)
                pieces = [
                    c
                    for _, m in sorted(by_cluster.items(), key=lambda kv: -len(kv[1]))
                    for c in _chunks(m, MAX_GROUP)
                ]
            else:
                pieces = [found]
            for piece in pieces:
                groups.append(Group("", "family", head, band, lang, main_cluster(piece), piece))
        # The candidates alone in their family, by theme cluster.
        for (lang, c), found in sorted(alone.items()):
            for chunk in _chunks(order(found), THEME_CHUNK):
                groups.append(Group("", "theme", f"cluster {c}", band, lang, c, chunk))
    # Parts: whole theme clusters, the largest first into the lightest part.
    parts = max(1, int(parts))
    load = Counter()
    for g in groups:
        load[g.cluster] += len(g.members)
    part_of: dict[int, int] = {}
    sizes = [0] * parts
    for c, size in sorted(load.items(), key=lambda kv: (-kv[1], kv[0])):
        p = int(np.argmin(sizes))
        part_of[c] = p + 1
        sizes[p] += size
    band_rank = {b: i for i, b in enumerate(BAND_ORDER)}
    kind_rank = {"pattern": 0, "formula": 1, "specific": 2, "family": 3, "theme": 4}
    for g in groups:
        g.part = part_of.get(g.cluster, 1)
    groups.sort(
        key=lambda g: (
            g.part,
            band_rank[g.band],
            kind_rank[g.kind],
            g.cluster if g.kind == "theme" else 0,
            -len(g.members) if g.kind == "family" else 0,
        )
    )
    for number, g in enumerate(groups, 1):
        g.id = f"g{number}"
    return groups, cluster


def _cognate_keys(term: str, lang: str) -> list[str]:
    """The content words of a term, folded (accents, case and elisions aside)."""
    edge = EDGE_WORDS.get(lang, frozenset())
    keys = []
    for w in _words(term):
        w = _ELISION.sub("", _fold(w))
        if w and w not in edge:
            keys.append(w)
    return keys


def _cognate(a: str, b: str) -> float:
    """How alike two words are as cognates (0 to 1): they start alike and share most letters."""
    from difflib import SequenceMatcher

    if a == b:
        return 1.0
    if a[:4] != b[:4]:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def _match(a: Sequence[str], b: Sequence[str]) -> float:
    """The share of words two terms share as cognates, one to one, when they have as many
    words (0 otherwise); a single word asks a closer likeness than a phrase's words."""
    if not a or len(a) != len(b):
        return 0.0
    floor = 0.8 if len(a) == 1 else 0.6
    rest = list(b)
    total = 0.0
    for k in a:
        best = max(rest, key=lambda r: _cognate(k, r))
        score = _cognate(k, best)
        if score < floor:
            return 0.0
        rest.remove(best)
        total += score
    return total / len(a)


def twin_pairs(
    items: Sequence[Mapping[str, Any]],
    V: Any,
    reference: str,
    *,
    min_score: float = 0.75,
    min_people: int = 1,
    margin: float = 0.05,
) -> list[dict[str, Any]]:
    """Likely twins: a candidate of another language and the reference-language candidate
    that names the same thing (its translation), each at most once.

    The evidence is lexical first: the two have as many content words, each a
    cognate of one of the other's (*score*, their mean likeness, accents and
    function words aside: « diversité des cryptophytes » and « cryptophyte
    diversity » score 0.93). Who uses them (*cosine*, people as opaque numbers)
    only breaks ties: people often write each language for a different
    audience, and a rare term shares its few users with many others, so
    co-usage alone makes every rare pair look like a twin. A term with two
    equally good twins (within *margin*) gets none.
    """
    people = [int(it.get("people") or 0) for it in items]
    keys = [_cognate_keys(it["term"], it["lang"]) for it in items]
    by_key: dict[str, list[int]] = defaultdict(list)
    for j, it in enumerate(items):
        if it["lang"] == reference and people[j] >= min_people:
            for k in set(keys[j]):
                by_key[k[:4]].append(j)
    out = []
    for i, it in enumerate(items):
        if it["lang"] == reference or people[i] < min_people or not keys[i]:
            continue
        found = {j for k in set(keys[i]) for j in by_key.get(k[:4], ())}
        scored = []
        for j in found:
            s = _match(keys[i], keys[j])
            if s >= min_score:
                scored.append((s, j))
        if not scored:
            continue
        cos = {j: float(V[i].multiply(V[j]).sum()) for _, j in scored}
        scored.sort(key=lambda x: (-(x[0] + 0.05 * cos[x[1]]), items[x[1]]["term"]))
        best, j = scored[0]
        if len(scored) > 1:
            second, j2 = scored[1]
            if (best + 0.05 * cos[j]) - (second + 0.05 * cos[j2]) < margin:
                continue
        out.append(
            {
                "i": i,
                "j": j,
                "a": it["term"],
                "a_lang": it["lang"],
                "b": items[j]["term"],
                "b_lang": items[j]["lang"],
                "score": round(best, 3),
                "cosine": round(cos[j], 3),
            }
        )
    out.sort(key=lambda p: (-p["score"], -p["cosine"], p["a"]))
    return out
