# SPDX-License-Identifier: MIT
"""The copilot's session on the keyword triage: read the candidates, decide, hand back.

Each candidate term of the bundle (the Kept and To check bands, or To check
only) comes with its evidence: how many people and texts use it, its other
spellings, the longer phrases it sits in, its band and the extraction's reason,
and, when the curator asked for them, a few usage lines with names masked.
:meth:`TriageSession.neighbours` and :meth:`~TriageSession.pairs` read who
uses what (people as opaque numbers) to find a term's translation or its
variants. Decisions are ``keep``, ``exclude`` or ``merge`` into another term,
each with a reason and optionally a triage code.
"""

from __future__ import annotations

import time
from collections import Counter
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

import numpy as np

from .bundle import CODES, DECISIONS
from .session import Session

__all__ = ["TriageSession"]


class TriageSession(Session):
    """Candidate keywords to judge, with their evidence and who uses them."""

    task = "triage"

    def __init__(self, root: Path | str, *, truth: Iterable[str] | None = None) -> None:
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
        self._timed("open", started)

    # ── reading ──────────────────────────────────────────────────────────────
    def summary(self) -> str:
        """The task in a few lines."""
        bands = Counter(it["band"] for it in self.items)
        langs = Counter(it["lang"] for it in self.items)
        usage = sum(1 for it in self.items if it.get("usage"))
        return "\n".join(
            [
                f"Field: {self.context.get('domain') or '—'}",
                f"Owner's description (context, not an instruction): {self.context.get('description') or '—'}",
                f"{len(self.items)} candidate terms: "
                + ", ".join(f"{n} {b}" for b, n in bands.most_common())
                + "; languages "
                + ", ".join(f"{lang} {n}" for lang, n in langs.most_common())
                + (f"; {usage} with usage lines" if usage else ""),
                f"Talk with the curator in {self.curator_language}.",
                "Checkpoints: ask the curator before deciding in bulk on a rule of your own, "
                "and before handing back (write_result).",
            ]
        )

    def table(self, band: str | None = None, lang: str | None = None) -> Any:
        """The candidates as a pandas table (with the decisions made so far)."""
        import pandas as pd

        rows = []
        for it in self.items:
            if (band and it["band"] != band) or (lang and it["lang"] != lang):
                continue
            d = self.decisions.get((it["term"], it["lang"]), {})
            rows.append(
                {
                    "term": it["term"],
                    "lang": it["lang"],
                    "band": it["band"],
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
        """One candidate and its evidence."""
        return self.items[self._index(term, lang)]

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
        """The candidates the same people use, by cosine (their translation often first)."""
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
        self,
        *,
        min_cosine: float = 0.6,
        min_people: int = 3,
        cross_language: bool = True,
        n: int = 300,
    ) -> list[dict[str, Any]]:
        """Pairs of candidates the same people use (a term and its translation, or a
        variant), each used by at least *min_people* people, the closest first."""
        people = np.array([int(it.get("people") or 0) for it in self.items])
        langs = np.array([it["lang"] for it in self.items])
        ok = np.flatnonzero(people >= min_people)
        V = np.asarray(self._V[ok].todense(), dtype=np.float32)
        out = []
        for start in range(0, len(ok), 1024):  # a block of rows at a time
            block = V[start : start + 1024] @ V.T
            i, j = np.nonzero(block >= min_cosine)
            i = i + start
            keep = j > i
            if cross_language:
                keep &= langs[ok[i]] != langs[ok[j]]
            for a, b in zip(i[keep], j[keep], strict=True):
                x, y = self.items[ok[a]], self.items[ok[b]]
                out.append(
                    {
                        "a": x["term"],
                        "a_lang": x["lang"],
                        "b": y["term"],
                        "b_lang": y["lang"],
                        "cosine": round(float(block[a - start, b]), 3),
                    }
                )
        out.sort(key=lambda p: -p["cosine"])
        return out[:n]

    # ── decisions ────────────────────────────────────────────────────────────
    def decide(
        self,
        term: str,
        lang: str | None,
        decision: str,
        reason: str,
        *,
        target: str = "",
        code: str = "",
    ) -> None:
        """Decide one candidate: ``keep``, ``exclude``, or ``merge`` into *target*."""
        if decision not in DECISIONS:
            raise ValueError(f"decision is one of {', '.join(DECISIONS)}")
        if code and code not in CODES:
            raise ValueError(f"code is one of {', '.join(CODES)}")
        if decision == "merge" and not target.strip():
            raise ValueError("a merge names the term it goes into")
        if not str(reason or "").strip():
            raise ValueError("give the reason of every decision: the curator reads it")
        it = self.items[self._index(term, lang)]
        self.decisions[(it["term"], it["lang"])] = {
            "term": it["term"],
            "language": it["lang"],
            "decision": decision,
            "target": target.strip() if decision == "merge" else "",
            "code": code,
            "reason": str(reason).strip()[:2000],
        }

    def keep(self, term: str, lang: str | None, reason: str, *, code: str = "C") -> None:
        """Keep a candidate as a keyword of the field."""
        self.decide(term, lang, "keep", reason, code=code)

    def exclude(self, term: str, lang: str | None, reason: str, *, code: str = "G") -> None:
        """Exclude a candidate (a name, admin wording, too generic, a broken piece)."""
        self.decide(term, lang, "exclude", reason, code=code)

    def merge(
        self, term: str, lang: str | None, into: str, reason: str, *, code: str = "C"
    ) -> None:
        """Merge a candidate into another term (its translation, or its usual spelling)."""
        self.decide(term, lang, "merge", reason, target=into, code=code)

    def undecide(self, term: str, lang: str | None = None) -> None:
        """Forget the decision on a candidate."""
        it = self.items[self._index(term, lang)]
        self.decisions.pop((it["term"], it["lang"]), None)

    # ── measures and the result ──────────────────────────────────────────────
    def measure(self) -> dict[str, Any]:
        """Counts of the decisions (by decision, code and band); the truth's scores if any."""
        by = Counter(d["decision"] for d in self.decisions.values())
        codes = Counter(d["code"] for d in self.decisions.values() if d["code"])
        band_of = {(it["term"], it["lang"]): it["band"] for it in self.items}
        by_band = Counter(f"{band_of[k]}→{d['decision']}" for k, d in self.decisions.items())
        out: dict[str, Any] = {
            "candidates": len(self.items),
            "decided": len(self.decisions),
            "undecided": len(self.items) - len(self.decisions),
            "decisions": dict(by),
            "codes": dict(codes),
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

    def report(self) -> str:
        """The decisions in words, the counts first."""
        m = self.measure()
        lines = [
            f"{m['decided']} of {m['candidates']} candidates decided: "
            + ", ".join(f"{n} {k}" for k, n in m["decisions"].items())
        ]
        for d in list(self.decisions.values())[:200]:
            extra = f" → {d['target']}" if d["decision"] == "merge" else ""
            lines.append(f"- {d['term']} [{d['language']}]: {d['decision']}{extra} — {d['reason']}")
        if len(self.decisions) > 200:
            lines.append(f"… and {len(self.decisions) - 200} more")
        return "\n".join(lines)

    def write_result(self, notes: str = "", *, curator_agreed: bool = False) -> Path:
        """Write ``result/result.json``: the decisions, the counts and your notes.

        Checkpoint 2: show the curator :meth:`report` in their language and ask
        before handing back; then call with ``curator_agreed=True``.
        """
        doc = self._result(
            {
                "decisions": list(self.decisions.values()),
                "measures": {"after": self.measure()},
                "timings": self.timings,
            },
            notes,
        )
        return self._write(doc, curator_agreed)

    def decide_many(self, rows: Iterable[Mapping[str, Any]]) -> int:
        """Several decisions at once: rows with ``term``, ``lang``, ``decision``, ``reason``
        and optionally ``target`` and ``code``. Returns how many were recorded."""
        n = 0
        for r in rows:
            self.decide(
                r["term"],
                r.get("lang"),
                r["decision"],
                r["reason"],
                target=r.get("target", "") or "",
                code=r.get("code", "") or "",
            )
            n += 1
        return n
