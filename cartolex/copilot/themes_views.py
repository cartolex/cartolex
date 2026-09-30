# SPDX-License-Identifier: MIT
"""The theme copilot's views of a tree: who uses each node, and the comb on the current tree.

A mixin of :class:`cartolex.copilot.themes.ThemesSession`; it reads the
session's tree, keywords, people's usage (``U``, people × keywords, people as
opaque rows) and, when the bundle has it, the texts × keywords incidence
(``D``: which keywords each text uses, texts as opaque rows in a random order;
never a text):

- :meth:`~ThemesViews.people_of`: how many people use each node, and whether
  one or two people make most of its use (« one person's vocabulary »: counts
  only, never who);
- :meth:`~ThemesViews.levels`: the comb, read again on the current tree.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from typing import Any

import numpy as np

__all__ = ["ONE_PERSON", "ThemesViews"]

#: A node whose two heaviest users make this share of its use is « one person's vocabulary ».
ONE_PERSON = 0.6


class ThemesViews:
    """Views of the tree for the copilot (a mixin of the themes session)."""

    # Set by the session.
    tree: dict[str, Any]
    terms: list[str]
    U: Any
    D: Any

    # ── helpers ──────────────────────────────────────────────────────────────
    def _under(self, doc: Mapping[str, Any]) -> dict[str, list[str]]:
        """Each node's keywords, on it and below it."""
        parent = {n["id"]: n.get("parent") for n in doc["nodes"]}
        out: dict[str, list[str]] = defaultdict(list)
        for k, nid in doc["keywords"].items():
            while nid is not None:
                out[nid].append(k)
                nid = parent.get(nid)
        return out

    def people_of(self, doc: Mapping[str, Any] | None = None) -> dict[str, dict[str, Any]]:
        """For each node: how many people use its keywords, and the share of its use the two
        heaviest users make (people are counted, never named or numbered)."""
        doc = doc or self.tree
        col = {t: j for j, t in enumerate(self.terms)}
        out = {}
        for nid, kws in self._under(doc).items():
            cols = [col[k] for k in kws if k in col]
            if not cols:
                out[nid] = {"people": 0, "top2_share": 0.0, "one_person": False}
                continue
            use = np.asarray(self.U[:, cols].sum(axis=1)).ravel()
            total = float(use.sum())
            n = int((use > 0).sum())
            share = float(np.sort(use)[-2:].sum() / total) if total else 0.0
            out[nid] = {
                "people": n,
                "top2_share": round(share, 3),
                "one_person": share >= ONE_PERSON,
            }
        return out

    # ── the comb, on the current tree ────────────────────────────────────────
    def levels(self, n: int = 10, *, detail: bool = False) -> list[dict[str, Any]]:
        """The comb read on the current tree: keywords whose texts support a higher node
        (``to``) or no theme (``to`` None), with the share that node holds. Read again after
        every change from the bundle's texts × keywords incidence (no text is in it); a
        bundle without it gives the comb of the tree as bundled. *n*: ten by default, up to
        200 with *detail*."""
        n = max(n, 200) if detail else n
        if self.D is None:
            path = self.path("baseline/levels.json")  # type: ignore[attr-defined]
            if not path.is_file():
                return []
            return list(self.json("baseline/levels.json")["items"])[:n]  # type: ignore[attr-defined]
        from cartolex.lexicon.theme_comb import tree_levels

        _, found = tree_levels(self.tree, self.terms, self.D, options=self.comb)  # type: ignore[attr-defined]
        return [
            {"keyword": x.keyword, "node": x.node, "to": x.to, "share": x.share, "texts": x.texts}
            for x in found
        ][:n]
