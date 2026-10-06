# SPDX-License-Identifier: MIT
"""The copilot's session on the keyword triage: read the candidates by group, decide, hand back.

Each candidate term of the bundle (the Kept, To check and Set aside bands, never
those rejected automatically; or Kept and To check, or To check only) comes
with its evidence: how many people and texts use it, its other spellings, the
longer phrases it sits in, its band and the extraction's reason, and, when the
curator asked for them, a few usage lines with names masked.

**Code does the bulk, the assistant judges groups.** The kit sorts the
candidates first (:mod:`cartolex.copilot.sorting`): patterns of junk, formulas,
a paper's own phrases, families that share a head word, the rest by theme
cluster, each of another language with its likely twin in the reference
language. The assistant reads them group by group
(:meth:`TriageSession.next_batch`: a group is shown once, and only what is left
of it) and decides a whole group in a line (:meth:`~TriageSession.apply`), term
by term only where a group is mixed. Nothing is decided by omission: a group
line applies to a group that was shown, and a candidate nobody decided keeps
the curator's current state.

**The kit keeps the count, not the assistant.** Every candidate shown and every
decision (by group or by term, read or not) is in the ledger:
:meth:`~TriageSession.coverage` and :meth:`~TriageSession.progress` count what
was truly decided, and the result carries those counts and the caveats they
give (what was not read).

**On disk and resumable.** Every decision is appended to
``result/decisions.jsonl`` as it is made (``decisions-part-<k>.jsonl`` for a
part): a session that stops is taken up by another (:meth:`~TriageSession.resume`).
The curator's standing rules (``data/context.json``, and those agreed during the
session, :meth:`~TriageSession.add_rule`) go back with the result.

Decisions are ``keep``, ``exclude`` or ``merge`` into another term, each with a
reason (a group's reason covers its members) and a triage code, which gives the
decision its category (:mod:`cartolex.lexicon.categories`): ``never`` for a term
never a keyword in any field, ``here`` for one not informative in this field only.
"""

from __future__ import annotations

import json
import re
import time
from collections import Counter
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

import numpy as np

from ..lexicon.categories import ACCEPTED, CATEGORIES, REJECTED, category_of
from .bundle import CODES, CONFIDENCES, DECISIONS
from .session import ASK, Session
from .sorting import BAND_ORDER, Group, is_formula, same_acronym, sort_candidates, twin_pairs

__all__ = ["TriageSession"]

#: Characters per token, for the budget (a cautious rule for lists of short terms).
CHARS_PER_TOKEN = 3.5
#: What one assistant conversation holds comfortably, in tokens (the budget's advice).
CONVERSATION_TOKENS = 120_000
#: Unread candidates a single call may decide before the kit asks for ``unread_ok``.
UNREAD_LIMIT = 50
_KIND_WORDS = {
    "pattern": "flagged",
    "formula": "formulas and acronyms",
    "specific": "a paper's own phrases",
    "family": "families",
    "theme": "theme groups",
}


def _fold(text: str) -> str:
    return " ".join(str(text).split()).casefold()


class TriageSession(Session):
    """Candidate keywords to judge, grouped, with their evidence and a ledger of the work."""

    task = "triage"

    def __init__(
        self,
        root: Path | str,
        *,
        truth: Iterable[str] | None = None,
        part: int | None = None,
        parts: int | None = None,
    ) -> None:
        from scipy import sparse
        from sklearn.preprocessing import normalize

        started = time.perf_counter()
        super().__init__(root)
        self.items: list[dict[str, Any]] = list(self.json("data/terms.json")["items"])
        self._at = {(it["term"], it["lang"]): i for i, it in enumerate(self.items)}
        with np.load(self.path("data/term_people.npz"), allow_pickle=False) as a:
            shape = tuple(int(x) for x in a["P_shape"])
            P = sparse.csr_matrix((a["P_data"], a["P_indices"], a["P_indptr"]), shape=shape)
        # People who use many of the terms say little about which ones go together.
        df = np.asarray((P > 0).sum(axis=0)).ravel()
        weighted = P @ sparse.diags(np.log((1 + P.shape[0]) / (1 + df)) + 1.0)
        self._V = normalize(sparse.csr_matrix(weighted, dtype=float))
        self.decisions: dict[tuple[str, str], dict[str, Any]] = {}
        if truth is None and self.path("data/truth.json").is_file():
            truth = self.json("data/truth.json")
        self.truth = {str(t).casefold() for t in truth} if truth else None
        # The pre-sort: the same groups, ids and parts in every session of this bundle.
        self.parts = max(1, int(parts or self.manifest.get("parts") or 1))
        if part is not None and not 1 <= int(part) <= self.parts:
            raise ValueError(f"part is a number from 1 to {self.parts}")
        self.part = int(part) if part is not None else None
        self.groups, self.cluster = sort_candidates(self.items, self._V, parts=self.parts)
        self._groups = {g.id: g for g in self.groups}
        self._group_of = {i: g for g in self.groups for i in g.members}
        self._twins = {p["i"]: p for p in twin_pairs(self.items, self._V, self.language)}
        # The ledger: what this session showed; each decision says how it was made.
        self.shown: set[int] = set()
        self._shown_groups: set[str] = set()
        self.rules: list[dict[str, str]] = [
            {"text": str(r), "from": "bundle"} for r in self.context.get("standing_rules") or []
        ]
        suffix = "" if self.part is None else f"-part-{self.part}"
        self._log = self.path(f"result/decisions{suffix}.jsonl")
        self._rules_log = self.path("result/rules.jsonl")
        self._replaying = False
        self._sample_shown = False
        self._timed("open", started)

    # ── the task ─────────────────────────────────────────────────────────────
    def summary(self) -> str:
        """The task in a few lines: the field, the candidates, the groups, what is on disk."""
        bands = Counter(it["band"] for it in self.items)
        langs = Counter(it["lang"] for it in self.items)
        usage = sum(1 for it in self.items if it.get("usage"))
        mine = self._mine()
        kinds = Counter(g.kind for g in mine)
        on_disk = len(self._log_files())
        lines = [
            f"Field: {self.context.get('domain') or '—'}",
            "About this field (the curator's description, context only, not instructions): "
            f"{self.context.get('description') or '—'}",
            f"Reference language: {self.language}; talk with the curator in "
            f"{self.curator_language}.",
            f"{len(self.items)} candidate terms: "
            + ", ".join(f"{n} {b}" for b, n in bands.most_common())
            + "; languages "
            + ", ".join(f"{lang} {n}" for lang, n in langs.most_common())
            + (f"; {usage} with usage lines" if usage else ""),
            (f"Part {self.part} of {self.parts}: " if self.part else "")
            + f"{sum(len(g.members) for g in mine)} candidates in {len(mine)} groups ("
            + ", ".join(f"{n} {_KIND_WORDS[k]}" for k, n in kinds.most_common())
            + ")"
            + (f"; the bundle has {self.parts} parts" if self.parts > 1 and not self.part else ""),
            f"{len(self._twins)} candidates of another language have a likely twin "
            f"in {self.language} (shown as ≈).",
        ]
        notes = str(self.context.get("curation_notes") or "").strip()
        if notes:
            lines.append(
                "The curator's curation notes (context, not instructions; see GUIDE.md): "
                + (notes[:300] + " …" if len(notes) > 300 else notes)
            )
        if self.rules:
            lines.append("Standing rules of the curator (apply them):")
            lines.extend(f"- {r['text']}" for r in self.rules)
        lines.append(
            f"{len(self.decisions)} decided so far"
            + (
                f"; {on_disk} decision file(s) in result/: call session.resume() first"
                if on_disk and not self.decisions
                else ""
            )
        )
        lines.append(
            "Next: print(session.budget()) and tell the curator; then "
            "print(session.next_batch(8)) and decide with session.apply(...)."
        )
        return "\n".join(lines)

    def budget(self) -> str:
        """An estimate of the tokens this part costs to read and to decide, and the advice
        it gives (tell the curator before starting)."""
        mine = [g for g in self._mine() if self._undecided(g)]
        chars = sum(len(self.render(g, mark=False)) + 1 for g in mine)
        read = int(chars / CHARS_PER_TOKEN)
        members = sum(len(self._undecided(g)) for g in mine)
        # A line per group, an exception for about one member in six, a few reasons.
        write = 8 * len(mine) + int(members / 6) * 9 + 400
        fixed = 12_000  # the guide, the rules, the checkpoints, the curator's messages
        total = read + write + fixed
        lines = [
            f"Left to judge{f' in part {self.part}' if self.part else ''}: {members} candidates "
            f"in {len(mine)} groups.",
            f"Reading them: about {read:,} tokens; writing the decisions: about {write:,}; "
            f"with the guide and the talk: about {total:,} tokens.",
        ]
        if total <= CONVERSATION_TOKENS:
            lines.append("This fits in one conversation.")
        else:
            k = -(-total // CONVERSATION_TOKENS)
            lines.append(
                f"This is more than one conversation holds comfortably ({CONVERSATION_TOKENS:,}). "
                f"Offer the curator {k} parts, one conversation each "
                f"(open_bundle('.', part=1, parts={k}), then part=2 …), or, with helpers "
                "that run in parallel, one helper per part; or the To check band first."
            )
        return "\n".join(lines)

    # ── reading ──────────────────────────────────────────────────────────────
    def table(self, band: str | None = None, lang: str | None = None) -> Any:
        """The candidates as a pandas table (with the decisions made so far). Reading it
        does not count as reading the candidates: decide from :meth:`next_batch`."""
        import pandas as pd

        rows = []
        for i, it in enumerate(self.items):
            if (band and it["band"] != band) or (lang and it["lang"] != lang):
                continue
            d = self.decisions.get((it["term"], it["lang"]), {})
            g = self._group_of.get(i)
            rows.append(
                {
                    "id": f"t{i}",
                    "term": it["term"],
                    "lang": it["lang"],
                    "band": it["band"],
                    "group": g.id if g else "",
                    "reason": it["reason"],
                    "people": it["people"],
                    "texts": it["texts"],
                    "forms": "; ".join(it.get("forms") or []),
                    "inside": "; ".join(it.get("inside") or []),
                    "current": it.get("current") or "",
                    "decision": d.get("decision", ""),
                    "target": d.get("target", ""),
                }
            )
        return pd.DataFrame(rows)

    def item(self, term: str, lang: str | None = None) -> dict[str, Any]:
        """One candidate and its evidence (it counts as read)."""
        i = self._index(term, lang)
        self.shown.add(i)
        return self.items[i]

    def _index(self, term: str, lang: str | None) -> int:
        if lang is not None:
            if (term, lang) not in self._at:
                raise KeyError(f"no candidate {term!r} in {lang!r}")
            return self._at[(term, lang)]
        found = [i for (t, _), i in self._at.items() if t == term]
        if len(found) != 1:
            raise KeyError(f"{term!r}: {'no candidate' if not found else 'give its language'}")
        return found[0]

    def neighbours(
        self, term: str, lang: str | None = None, *, n: int = 10
    ) -> list[dict[str, Any]]:
        """The candidates the same people use, by cosine. Weak evidence: a rare term shares
        its few users with many others."""
        i = self._index(term, lang)
        sims = np.asarray((self._V @ self._V[i].T).todense()).ravel()
        sims[i] = -1
        order = np.argsort(-sims, kind="stable")[:n]
        return [
            {
                "term": self.items[j]["term"],
                "lang": self.items[j]["lang"],
                "cosine": round(float(sims[j]), 3),
            }
            for j in order
            if sims[j] > 0
        ]

    def pairs(
        self, *, min_score: float = 0.75, n: int = 10, detail: bool = False
    ) -> list[dict[str, Any]]:
        """Likely twins across languages: a candidate and the reference-language candidate
        that names the same thing, the surest first. *score* is how alike their words are as
        cognates (1: the same words), *cosine* how alike their users are (a tie-breaker).
        Ten by default (the batches show each twin as ≈ anyway); *n* of them, at most 300, with
        *detail*."""
        n = min(max(n, 10), 300) if detail else min(n, 10)
        found = twin_pairs(self.items, self._V, self.language, min_score=min_score)
        return [{k: v for k, v in p.items() if k not in ("i", "j")} for p in found[:n]]

    def lookup(self, terms: Iterable[str] | str) -> dict[str, list[dict[str, Any]]]:
        """Which of *terms* are candidates (any language, case aside): to check a merge target."""
        wanted = [terms] if isinstance(terms, str) else list(terms)
        index: dict[str, list[dict[str, Any]]] = {}
        for i, it in enumerate(self.items):
            index.setdefault(_fold(it["term"]), []).append(
                {"id": f"t{i}", "term": it["term"], "lang": it["lang"], "band": it["band"]}
            )
        return {t: index.get(_fold(t), []) for t in wanted}

    # ── groups: shown once, decided in a line ────────────────────────────────
    def _mine(self) -> list[Group]:
        """The groups of this session's part (every group without a part)."""
        return [g for g in self.groups if self.part is None or g.part == self.part]

    def group(self, group_id: str) -> Group:
        """One group: its kind, label, band, language, part and members (item indices)."""
        if group_id not in self._groups:
            raise KeyError(f"no group {group_id!r}")
        return self._groups[group_id]

    def _key(self, i: int) -> tuple[str, str]:
        return (self.items[i]["term"], self.items[i]["lang"])

    def _undecided(self, g: Group) -> list[int]:
        return [i for i in g.members if self._key(i) not in self.decisions]

    def _row(self, number: int, i: int) -> list[str]:
        it = self.items[i]
        extra = []
        if it.get("inside"):
            extra.append("in «" + it["inside"][0] + "»")
        forms = [f for f in it.get("forms") or [] if _fold(f) != _fold(it["term"])]
        if forms:
            extra.append("also «" + "», «".join(forms[:2]) + "»")
        twin = self._twins.get(i)
        if twin:
            extra.append(f"≈ {twin['b']}")
        if it.get("current"):
            extra.append(f"now {it['current']}")
        head = f"  {number} {it['term']} ({it['people']}p)"
        out = [head + ("  " + "; ".join(extra) if extra else "")]
        for u in (it.get("usage") or [])[:1]:
            out.append(f"      « {u[:160]} »")
        return out

    def render(self, g: Group, *, mark: bool = True) -> str:
        """A group as text: its header, then its undecided members, numbered in the group
        (the numbers stay the same as members get decided). With *mark*, they count as read."""
        head = {
            "pattern": f"flagged: {g.label}",
            "formula": "formulas and acronyms (keep whole)",
            "specific": "a paper's own phrases (one text or one person)",
            "family": f"family «{g.label}»",
            "theme": g.label,
        }[g.kind]
        shown = self._undecided(g)
        lines = [f"{g.id} · {head} · {g.lang} · {g.band} · {len(shown)} of {len(g.members)}"]
        if g.band == "kept" and not any(self.items[i].get("usage") for i in shown):
            # The kept band, most of the work and mostly right: its members on a few lines.
            line = " "
            for n, i in enumerate(g.members, 1):
                if i not in shown:
                    continue
                twin = self._twins.get(i)
                piece = f" {n} {self.items[i]['term']}" + (f" (≈ {twin['b']})" if twin else "")
                if len(line) + len(piece) > 100:
                    lines.append(line)
                    line = " "
                line += piece + " ·"
            lines.append(line.rstrip(" ·"))
        else:
            for n, i in enumerate(g.members, 1):
                if i in shown:
                    lines.extend(self._row(n, i))
        if mark:
            self.shown.update(shown)
            self._shown_groups.add(g.id)
            self.save()
        return "\n".join(lines)

    def next_batch(self, n: int = 8) -> str:
        """The next *n* groups this session has not shown yet, with only their undecided
        members (the order: to check, set aside, kept; patterns first, then families, then
        theme groups). A group shown and left undecided is in :meth:`pending`."""
        out = []
        for g in self._mine():
            if g.id not in self._shown_groups and self._undecided(g):
                out.append(self.render(g))
                if len(out) >= n:
                    break
        if not out:
            left = self.pending()
            return "Every group of this part has been shown. " + (
                left if left else "Check session.progress(), then hand back."
            )
        return "\n".join(out)

    def show(self, group_id: str) -> str:
        """One group again, with its undecided members only."""
        return self.render(self.group(group_id))

    def pending(self) -> str:
        """The groups shown and still undecided, as ids only (show one with :meth:`show`)."""
        left = [
            f"{g.id} ({len(self._undecided(g))})"
            for g in self._mine()
            if g.id in self._shown_groups and self._undecided(g)
        ]
        if not left:
            return ""
        more = " …" if len(left) > 60 else ""
        return (
            f"{len(left)} groups were shown and still have undecided members: "
            + ", ".join(left[:60])
            + more
        )

    # ── deciding ─────────────────────────────────────────────────────────────
    def decide(
        self,
        term: str,
        lang: str | None,
        decision: str,
        reason: str,
        *,
        target: str = "",
        code: str = "",
        category: str = "",
        confidence: str = "unsure",
        group: str = "",
        by: str = "term",
    ) -> None:
        """Decide one candidate: ``keep``, ``exclude``, or ``merge`` into *target*.

        *category* defaults to the code's; a kept or merged term takes an accepted
        category (``concept``, ``method``, ``object``, ``place``, ``field``), an
        excluded one ``never`` or ``here``. *confidence* is ``sure`` or ``unsure`` (the
        default): only a ``never`` exclusion given as sure spares other projects the
        question (it enters the machine's rejection cache). Two formulas are never the
        same term: ``CO`` does not merge into ``CO2``; but an acronym's plural merges into
        it (``VOCs`` into ``VOC``), and an acronym of another language into its
        translation (``ADN`` into ``DNA``).
        """
        if decision not in DECISIONS:
            raise ValueError(f"decision is one of {', '.join(DECISIONS)}")
        if code and code not in CODES:
            raise ValueError(f"code is one of {', '.join(CODES)}")
        category = category or category_of(code)
        if category and category not in CATEGORIES:
            raise ValueError(f"category is one of {', '.join(CATEGORIES)}")
        allowed = REJECTED if decision == "exclude" else ACCEPTED
        if category and category not in allowed:
            raise ValueError(f"a {decision} takes a category among {', '.join(allowed)}")
        if confidence not in CONFIDENCES:
            raise ValueError(f"confidence is one of {', '.join(CONFIDENCES)}")
        if decision == "merge" and not target.strip():
            raise ValueError("a merge names the term it goes into")
        if not str(reason or "").strip():
            raise ValueError("give the reason of every decision: the curator reads it")
        i = self._index(term, lang)
        it = self.items[i]
        if (
            decision == "merge"
            and is_formula(it["term"])
            and is_formula(target)
            and it["term"].strip() != target.strip()
            and not same_acronym(it["term"], target, translation=it["lang"] != self.language)
        ):
            raise ValueError(
                f"{it['term']} and {target.strip()} are two formulas (or acronyms): "
                "they name different things, keep each whole (only an acronym's plural, "
                "or its translation from another language, merges into it)"
            )
        record = {
            "term": it["term"],
            "language": it["lang"],
            "decision": decision,
            "target": target.strip() if decision == "merge" else "",
            "code": code,
            "category": category,
            "confidence": confidence,
            "reason": str(reason).strip()[:2000],
            "group": group or (self._group_of[i].id if i in self._group_of else ""),
            "by": by if by in ("group", "term") else "term",
            "read": i in self.shown,
        }
        self.decisions[(it["term"], it["lang"])] = record
        self._append(record)

    def keep(self, term: str, lang: str | None, reason: str, *, code: str = "C") -> None:
        """Keep a candidate as a keyword of the field."""
        self.decide(term, lang, "keep", reason, code=code)

    def exclude(
        self,
        term: str,
        lang: str | None,
        reason: str,
        *,
        code: str = "G",
        confidence: str = "unsure",
    ) -> None:
        """Exclude a candidate (a name, not informative here, admin wording, too generic, a
        broken piece). ``K``, ``G`` and ``F`` say it is never a keyword in any field; say
        ``confidence="sure"`` when you are sure of it."""
        self.decide(term, lang, "exclude", reason, code=code, confidence=confidence)

    def merge(
        self, term: str, lang: str | None, into: str, reason: str, *, code: str = "C"
    ) -> None:
        """Merge a candidate into another term (its translation, or its usual spelling)."""
        self.decide(term, lang, "merge", reason, target=into, code=code)

    def undecide(self, term: str, lang: str | None = None) -> None:
        """Forget the decision on a candidate: it keeps the curator's current state."""
        it = self.items[self._index(term, lang)]
        if self.decisions.pop((it["term"], it["lang"]), None) is not None:
            self._append({"term": it["term"], "language": it["lang"], "undo": True})

    def decide_many(self, rows: Iterable[Mapping[str, Any]], *, unread_ok: bool = False) -> int:
        """Several decisions at once: rows with ``term``, ``lang``, ``decision``, ``reason``
        and optionally ``target``, ``code``, ``category`` and ``confidence``. Returns how many
        were recorded. More than a few candidates never shown are refused unless *unread_ok*:
        nothing is decided by omission."""
        rows = list(rows)
        if not unread_ok:
            unread = sum(1 for r in rows if self._index(r["term"], r.get("lang")) not in self.shown)
            if unread > UNREAD_LIMIT:
                raise ValueError(
                    f"{unread} of these candidates were never shown: read them first "
                    "(next_batch, show) and decide them there; a candidate nobody decided "
                    "keeps the curator's current state"
                )
        for r in rows:
            self.decide(
                r["term"],
                r.get("lang"),
                r["decision"],
                r["reason"],
                target=r.get("target", "") or "",
                code=r.get("code", "") or "",
                category=r.get("category", "") or "",
                confidence=r.get("confidence", "") or "unsure",
            )
        return len(rows)

    def _decide_code(
        self, i: int, spec: str, reason: str, *, sure: bool, group: str, by: str
    ) -> None:
        """Decide candidate *i* by a compact code: a letter (``C`` … ``F``), ``>target`` (a
        merge; ``M>target`` with its code), ``~`` (a merge into its twin), ``-`` (forget)."""
        spec = spec.strip()
        it = self.items[i]
        if spec == "-":
            self.undecide(it["term"], it["lang"])
            return
        code, _, target = spec.partition(">")
        if code.strip().endswith("~"):
            twin = self._twins.get(i)
            code = code.strip()[:-1]
            if twin is not None:
                target = twin["b"]
            elif by == "term":
                raise ValueError(f"{it['term']} has no twin: give its target with >")
        code = (code.strip() or "C").upper()
        if code not in CODES:
            raise ValueError(f"{spec!r}: a code is one of {' '.join(CODES)}, or >target, ~, -")
        kw = {"code": code, "group": group, "by": by}
        if target.strip():
            self.decide(it["term"], it["lang"], "merge", reason, target=target.strip(), **kw)
        elif category_of(code) in ACCEPTED:
            self.decide(it["term"], it["lang"], "keep", reason, **kw)
        else:
            confidence = "sure" if sure else "unsure"
            self.decide(it["term"], it["lang"], "exclude", reason, confidence=confidence, **kw)

    def _default_reason(self, g: Group | None, spec: str) -> str:
        code, _, target = spec.partition(">")
        if code.strip().endswith("~"):
            meaning = "its twin in the reference language, or kept"
        elif target.strip():
            meaning = "the same term as " + target.strip()
        else:
            meaning = CODES.get(code.strip().upper() or "C", "")
        if g is None:
            return meaning
        what = {
            "pattern": f"flagged {g.label}",
            "formula": "formula or acronym",
            "specific": "a paper's own phrase",
            "family": f"«{g.label}» family",
            "theme": g.label,
        }[g.kind]
        return f"{what}: {meaning}"

    def _need_shown(self, g: Group) -> None:
        if g.id not in self._shown_groups:
            raise ValueError(
                f"{g.id} was not shown yet: print(session.show('{g.id}')) and read it first "
                "(nothing is decided unread)"
            )

    def decide_group(
        self,
        group_id: str,
        code: str,
        reason: str = "",
        *,
        sure: bool = False,
        except_: Mapping[int, str] | None = None,
    ) -> int:
        """Decide the undecided members of a group shown to you by one code, and its
        exceptions by their place in the group (the group's reason covers its members).

        ``session.decide_group("g12", "M", "every ‹X› assay is a method", except_={3: "G",
        5: ">flood risk"})``. *sure* marks the exclusions as sure (``never`` codes).
        Returns how many candidates were decided.
        """
        g = self.group(group_id)
        self._need_shown(g)
        exceptions = {int(k): v for k, v in (except_ or {}).items()}
        for k in exceptions:
            if not 1 <= k <= len(g.members):
                raise ValueError(f"{group_id} has {len(g.members)} members: no member {k}")
        open_ = set(self._undecided(g))
        n = 0
        for place, i in enumerate(g.members, 1):
            if place in exceptions:
                spec, by = exceptions[place], "term"
            elif i in open_:
                spec, by = code, "group"
            else:
                continue
            why = reason if (reason and by == "group") else self._default_reason(g, spec)
            self._decide_code(i, spec, why, sure=sure, group=g.id, by=by)
            n += 1
        return n

    _LINE = re.compile(r"^(g\d+|t\d+)(?:\.([\d,\-]+))?\s+(\S[^;]*?)\s*(?:;\s*(.*))?$")

    def apply(self, text: str) -> str:
        """Decide in a compact form, one line per group or candidate (the fewest tokens)::

            g12 M                 the undecided members of g12: a method (kept)
            g12.3 G!              member 3 of g12: too generic, sure (never a keyword)
            g12.4,7 F             members 4 and 7: broken pieces
            g14 C ; processes of an object of the field
            g15.2 >sea level rise the same thing as « sea level rise » (a merge)
            g15.5 O>salt marsh    a merge, with its code
            g16 C~                every member with a twin (≈) merged into it, the others kept
            t812 N                candidate 812 (its id in session.lookup or table)
            g17.2 -               forget the decision on member 2 of g17

        A code is one of C M O P D (kept) or N H K G F (excluded); ``!`` after it: sure.
        Text after ``;`` is the reason (the curator reads it; a group's reason covers its
        members); without it, the group's label and the code's meaning. Member lines are
        applied first, wherever they stand: a group line then decides only the members
        still undecided. Only groups shown to you can be decided. ``#`` starts a comment.
        Returns what was done.
        """
        done = 0
        problems: list[str] = []
        parsed = []
        for raw in str(text).splitlines():
            line = raw.split("#", 1)[0].strip()
            if not line:
                continue
            m = self._LINE.match(line)
            if not m:
                problems.append(f"not understood: {line!r}")
                continue
            parsed.append((line, *m.groups()))
        parsed.sort(key=lambda p: p[1].startswith("g") and p[2] is None)
        for line, ref, places, spec, reason in parsed:
            sure = "!" in spec
            spec = spec.replace("!", "").strip()
            try:
                if ref.startswith("t"):
                    i = int(ref[1:])
                    if not 0 <= i < len(self.items):
                        raise ValueError(f"no candidate {ref}")
                    if i not in self.shown:
                        raise ValueError(f"{ref} was not shown yet: read it first")
                    g = self._group_of.get(i)
                    why = reason or self._default_reason(g, spec)
                    gid = g.id if g else ""
                    self._decide_code(i, spec, why, sure=sure, group=gid, by="term")
                    done += 1
                elif places:
                    g = self.group(ref)
                    self._need_shown(g)
                    chosen: list[int] = []
                    for piece in places.split(","):
                        a, _, b = piece.partition("-")
                        chosen.extend(range(int(a), int(b or a) + 1))
                    for k in chosen:
                        if not 1 <= k <= len(g.members):
                            raise ValueError(f"{g.id} has {len(g.members)} members: no member {k}")
                    why = reason or self._default_reason(g, spec)
                    for k in chosen:
                        self._decide_code(
                            g.members[k - 1], spec, why, sure=sure, group=g.id, by="term"
                        )
                    done += len(chosen)
                else:
                    done += self.decide_group(ref, spec, reason or "", sure=sure)
            except (KeyError, ValueError) as exc:
                problems.append(f"{line!r}: {exc}")
        left = sum(len(self._undecided(g)) for g in self._mine())
        out = f"{done} decisions recorded; {left} candidates left in this part."
        if problems:
            out += "\nNot applied:\n" + "\n".join(f"- {p}" for p in problems[:20])
        if len(self.decisions) >= 200 and not self._sample_shown:
            out += (
                "\nCheckpoint 1 is due: show the curator session.sample(), ask whether it looks "
                "right and whether they have standing rules (session.add_rule). " + ASK
            )
        self.save()
        return out

    # ── the curator ──────────────────────────────────────────────────────────
    def add_rule(self, text: str) -> None:
        """Record a standing rule the curator agreed to (« research discourse: always excluded,
        sure »; « medical vocabulary: kept »). It goes back with the result, and the next
        bundle of this project carries it."""
        text = " ".join(str(text).split())[:300]
        if not text:
            raise ValueError("a rule is a sentence")
        if any(r["text"] == text for r in self.rules):
            return
        self.rules.append({"text": text, "from": "session"})
        if not self._replaying:
            self.save()
            self._rules_log.parent.mkdir(parents=True, exist_ok=True)
            with open(self._rules_log, "a", encoding="utf-8") as fh:
                fh.write(json.dumps({"rule": text}, ensure_ascii=False) + "\n")

    def sample(self, n: int = 20, *, seed: int = 0) -> str:
        """A sample of the decisions so far to show the curator (checkpoint 1), the
        « not informative here » (H) exclusions first: ask about those."""
        import random

        rows = list(self.decisions.values())
        off = [d for d in rows if d["code"] == "H"][: n // 2]
        rest = [d for d in rows if d["code"] != "H"]
        chosen = off + random.Random(seed).sample(rest, min(n - len(off), len(rest)))
        self._sample_shown = True
        self.save()
        return "\n".join(
            f"- {d['term']} [{d['language']}]: {d['decision']}"
            + (f" → {d['target']}" if d["decision"] == "merge" else "")
            + f" ({d['code'] or '—'}"
            + (f", by group {d['group']}" if d.get("by") == "group" else "")
            + f") — {d['reason']}"
            for d in chosen
        )

    # ── the ledger ───────────────────────────────────────────────────────────
    def state(self) -> dict[str, Any]:
        """What this session showed you (kept between two processes of one conversation)."""
        return {
            "shown": sorted(self.shown),
            "groups": sorted(self._shown_groups),
            "sample_shown": self._sample_shown,
        }

    def restore(self, state: Mapping[str, Any]) -> None:
        self.shown |= {int(i) for i in state.get("shown") or [] if 0 <= int(i) < len(self.items)}
        self._shown_groups |= {str(g) for g in state.get("groups") or [] if g in self._groups}
        self._sample_shown = bool(state.get("sample_shown"))

    def coverage(self) -> dict[str, dict[str, int]]:
        """What was truly done, per band (and ``all``), counted by the kit: the candidates,
        those decided by a group line after reading them, term by term after reading them,
        without reading them; those read and left undecided; those never shown."""
        out: dict[str, dict[str, int]] = {}
        in_part = {i for g in self._mine() for i in g.members}
        for i, it in enumerate(self.items):
            if i not in in_part:
                continue
            d = self.decisions.get(self._key(i))
            if d is not None and not d.get("read", True):
                what = "decided_unread"
            elif d is not None:
                what = "decided_by_group" if d.get("by") == "group" else "decided_by_term"
            elif i in self.shown:
                what = "read_undecided"
            else:
                what = "not_shown"
            for band in (it["band"], "all"):
                row = out.setdefault(
                    band,
                    {
                        "candidates": 0,
                        "decided_by_group": 0,
                        "decided_by_term": 0,
                        "decided_unread": 0,
                        "read_undecided": 0,
                        "not_shown": 0,
                    },
                )
                row["candidates"] += 1
                row[what] += 1
        order = [b for b in (*BAND_ORDER, "all") if b in out]
        return {b: out[b] for b in order}

    def progress(self) -> str:
        """How far the work is, counted by the kit: decided, read and left, per band."""
        cov = self.coverage()
        lines = [f"Part {self.part} of {self.parts}:"] if self.part else []
        for band, c in cov.items():
            decided = c["decided_by_term"] + c["decided_by_group"] + c["decided_unread"]
            lines.append(
                f"{band}: {decided} of {c['candidates']} decided "
                f"({c['decided_by_group']} by group, {c['decided_by_term']} term by term"
                + (f", {c['decided_unread']} without reading" if c["decided_unread"] else "")
                + f"); {c['read_undecided']} read and undecided, {c['not_shown']} not shown yet"
            )
        lines.append(
            f"Every decision is saved as it is made, in {self._log.relative_to(self.root)}."
        )
        if len(self.decisions) >= 200 and not self._sample_shown:
            lines.append(
                "Checkpoint 1 is due: session.sample(), ask the curator, ask for rules; " + ASK
            )
        whole = cov.get("all", {})
        if not whole.get("not_shown") and not whole.get("read_undecided"):
            lines.append("Everything is decided: session.report(), ask the curator, write_result.")
        return "\n".join(lines)

    def caveats(self, read_lightly: str = "") -> dict[str, Any]:
        """What the result does not cover, from the ledger: per band, the candidates never
        shown, read and left undecided, decided without reading; and your own words on
        what you read lightly."""
        cov = self.coverage()

        def of(key: str) -> dict[str, int]:
            return {b: c[key] for b, c in cov.items() if c[key]}

        return {
            "not_read": of("not_shown"),
            "read_undecided": of("read_undecided"),
            "decided_unread": of("decided_unread"),
            "read_lightly": str(read_lightly or "")[:2000],
        }

    # ── on disk: every decision as it is made, and taking a session up again ──
    def _append(self, record: Mapping[str, Any]) -> None:
        if self._replaying:
            return
        self._log.parent.mkdir(parents=True, exist_ok=True)
        with open(self._log, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    def _log_files(self) -> list[Path]:
        folder = self.path("result")
        return sorted(folder.glob("decisions*.jsonl")) if folder.is_dir() else []

    def _records(self, f: Path) -> list[dict[str, Any]]:
        if f.suffix == ".json":
            doc = json.loads(f.read_text(encoding="utf-8"))
            for rule in doc.get("rules") or []:
                self.add_rule(rule)
            return list(doc.get("decisions") or [])
        return [json.loads(x) for x in f.read_text(encoding="utf-8").splitlines() if x.strip()]

    def resume(self, *paths: str | Path) -> str:
        """Take up the work already done: every ``result/decisions*.jsonl`` (this part's
        and the other parts'), the rules of ``result/rules.jsonl``, and any other decision
        file or result given in *paths*. Later lines win; candidates this bundle does not
        hold are skipped. What an earlier session read and left undecided comes again in
        :meth:`next_batch`."""
        given = [Path(p) if Path(p).is_absolute() else self.path(str(p)) for p in paths]
        files = self._log_files() + [f for f in given if f not in self._log_files()]
        read = skipped = 0
        self._replaying = True
        try:
            if self._rules_log.is_file():
                for line in self._rules_log.read_text(encoding="utf-8").splitlines():
                    if line.strip():
                        self.add_rule(json.loads(line)["rule"])
            for f in files:
                for r in self._records(f):
                    key = (str(r.get("term")), str(r.get("language") or ""))
                    if key not in self._at:
                        skipped += 1
                        continue
                    if r.get("undo"):
                        self.decisions.pop(key, None)
                    else:
                        self.decide(
                            key[0],
                            key[1],
                            r["decision"],
                            r.get("reason") or "taken up",
                            target=r.get("target") or "",
                            code=r.get("code") or "",
                            category=r.get("category") or "",
                            confidence=r.get("confidence") or "unsure",
                            group=r.get("group") or "",
                            by=r.get("by") or "term",
                        )
                        self.decisions[key]["read"] = bool(r.get("read", True))
                    read += 1
        finally:
            self._replaying = False
        return (
            f"{read} decisions taken up from {len(files)} file(s)"
            + (f", {skipped} for candidates not in this bundle" if skipped else "")
            + f".\n{self.progress()}"
        )

    # ── measures and the result ──────────────────────────────────────────────
    def measure(self) -> dict[str, Any]:
        """Counts of the decisions (by decision, code and band); the truth's scores if any."""
        by = Counter(d["decision"] for d in self.decisions.values())
        codes = Counter(d["code"] for d in self.decisions.values() if d["code"])
        categories = Counter(d["category"] for d in self.decisions.values() if d["category"])
        band_of = {(it["term"], it["lang"]): it["band"] for it in self.items}
        by_band = Counter(f"{band_of[k]}→{d['decision']}" for k, d in self.decisions.items())
        out: dict[str, Any] = {
            "candidates": len(self.items),
            "decided": len(self.decisions),
            "undecided": len(self.items) - len(self.decisions),
            "decisions": dict(by),
            "codes": dict(codes),
            "categories": dict(categories),
            "bands": dict(by_band),
        }
        if self.truth is not None:
            kept = {
                (d["target"] if d["decision"] == "merge" else d["term"]).casefold()
                for d in self.decisions.values()
                if d["decision"] != "exclude"
            } | {
                it["term"].casefold()
                for it in self.items
                if it["band"] == "kept" and (it["term"], it["lang"]) not in self.decisions
            }
            gold = self.truth
            hit = len(kept & gold)
            out["truth"] = {
                "precision": round(hit / len(kept), 4) if kept else 0.0,
                "recall": round(hit / len(gold), 4) if gold else 0.0,
            }
        return out

    def report(self, *, detail: bool = False) -> str:
        """The work in words: the kit's counts, then the decisions by group and the exceptions
        (the first ten of each by default; up to 200 with *detail*)."""
        m = self.measure()
        lines = [
            f"{m['decided']} of {m['candidates']} candidates decided: "
            + ", ".join(f"{n} {k}" for k, n in m["decisions"].items()),
            self.progress(),
        ]
        by_group: dict[str, list[dict[str, Any]]] = {}
        singles = []
        for d in self.decisions.values():
            if d.get("by") == "group":
                by_group.setdefault(d["group"], []).append(d)
            else:
                singles.append(d)
        cap = 200 if detail else 10
        for gid, rows in list(by_group.items())[:cap]:
            code = Counter(d["code"] for d in rows).most_common(1)[0][0]
            lines.append(f"- {gid}: {len(rows)} × {code} — {rows[0]['reason']}")
        if len(by_group) > cap:
            lines.append(f"… {len(by_group) - cap} more groups")
        for d in singles[:cap]:
            extra = f" → {d['target']}" if d["decision"] == "merge" else ""
            lines.append(f"- {d['term']} [{d['language']}]: {d['decision']}{extra} — {d['reason']}")
        if len(singles) > cap:
            lines.append(f"… {len(singles) - cap} more term by term")
        if not detail and (len(by_group) > cap or len(singles) > cap):
            lines.append("(report(detail=True) lists more)")
        if self.rules:
            lines.append("Standing rules: " + "; ".join(r["text"] for r in self.rules))
        return "\n".join(lines)

    def write_result(
        self,
        notes: str = "",
        *,
        curator_agreed: bool = False,
        partial: bool = False,
        read_lightly: str = "",
    ) -> Path:
        """Write the result: the decisions, the kit's counts, the caveats, the standing rules
        and your notes, in ``result/result.json`` (``result/result-part-<k>.json`` for a part).

        Checkpoint 2: show the curator :meth:`report` in their language and ask
        before handing back; then call with ``curator_agreed=True``. With
        *partial* (the context runs short, or the curator stops), write what is
        decided so far without the checkpoint: the curator imports it, and a new
        session takes the work up from ``result/`` (:meth:`resume`). *read_lightly*:
        your own words on what you read quickly (a band, a kind of group).
        """
        cov = self.coverage()
        whole = cov.get("all", {})
        left = whole.get("not_shown", 0) + whole.get("read_undecided", 0)
        doc = self._result(
            {
                "decisions": list(self.decisions.values()),
                "measures": {"after": self.measure()},
                "coverage": cov,
                "caveats": self.caveats(read_lightly),
                "rules": [r["text"] for r in self.rules],
                "partial": bool(partial or left),
                "part": self.part,
                "parts": self.parts,
                "timings": self.timings,
            },
            notes,
        )
        name = "result/result.json" if self.part is None else f"result/result-part-{self.part}.json"
        return self._write(doc, curator_agreed or partial, name=name)
