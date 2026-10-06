# SPDX-License-Identifier: MIT
"""The texts of what the map has in focus: a person's, an organisation's members', and with
the network those of the people (or organisations) the network's rings reach.

The map's texts layer draws, without a focus, a sample of every text (at most
:data:`cartolex.app.atlas_layers.MAX_TEXTS`); a focus asks for its own texts instead, from
every text of the tables, so that a person's texts are all there however large the corpus.
Who counts:

- a person (or a projected person): them, and the people merged into them;
- an organisation: its current members on the map (directly or through an organisation
  below it), as the map lights them;
- with ``net`` rings (1 to 3): the people of the person's rings of co-authors, or the
  members of the organisations of the organisation's rings (:mod:`cartolex.app.coauthors`).

Their texts are found in the texts' view (:mod:`cartolex.app.texts_view`, every authorship
as codes), then placed as the layer places every text
(:func:`cartolex.app.atlas_layers.place_texts`): at most *limit*, a sample the same each
time beyond it.
"""

from __future__ import annotations

from typing import Any

import numpy as np

__all__ = ["MAX_FOCUS_TEXTS", "focus_people", "focus_texts"]

#: The most texts of a focus one answer places (a sample, the same each time, beyond it).
MAX_FOCUS_TEXTS = 20_000


def _merged(project: Any, people: set[str]) -> set[str]:
    """*people* and everyone merged into one of them."""
    from cartolex.collect.decisions import read_people
    from cartolex.project.identity import merge_roots, merged_groups

    try:
        groups = merged_groups(merge_roots(read_people(project.layout)))
    except (OSError, ValueError):  # no decisions to read: nobody merged
        return people
    out = set(people)
    for p in people:
        out.update(groups.get(p, ()))
    return out


def focus_people(
    project: Any, cache: Any, kind: str, id_: str, net: int, extras: dict[str, Any]
) -> list[str] | None:
    """The people whose texts are the texts of the focus (``None``: no such focus).
    *extras* are the atlas page's (``people``, ``organisations``)."""
    from .coauthors import org_graph, person_graph, rings
    from .space_index import members_of

    if kind in ("person", "projected"):
        people = {id_}
        if net > 0:
            graph = person_graph(project, cache)
            code = graph.code(id_)
            if code is not None:
                for ring in rings(graph, code, net):
                    people.update(graph.ids[c] for c in ring.codes.tolist())
        return sorted(_merged(project, people))
    orgs = {o["id"]: o for o in extras.get("organisations") or []}
    if id_ not in orgs:
        return None
    members = members_of(extras)
    chosen = {id_}
    if net > 0:
        graph = org_graph(project, orgs[id_].get("level") or "", cache)
        code = graph.code(id_)
        if code is not None:
            for ring in rings(graph, code, net):
                chosen.update(graph.ids[c] for c in ring.codes.tolist())
    people = {p for o in chosen for p in members.get(o, ())}
    return sorted(_merged(project, people))


def focus_texts(project: Any, cache: Any, people: list[str]) -> list[str]:
    """The ids of the texts with one of *people* among their authors, in the view's order."""
    from .texts_view import texts_view

    view = texts_view(project, cache)
    rows = np.flatnonzero(view.of_people(people))
    if not len(rows):
        return []
    return view.table["text_id"].take(rows).to_pylist()
