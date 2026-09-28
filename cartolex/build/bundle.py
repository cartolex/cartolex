# SPDX-License-Identifier: MIT
"""A project's map bundle, from its current results: usage, vocabulary and the theme tree.

:func:`project_bundle` gathers what a portable bundle carries
(:mod:`cartolex.atlas.map_bundle`) from the results of a built project: each
mapped person's keyword usage (the plain-TF track), the vocabulary, the applied
theme tree of any depth (levels, nodes, parents, names, colours) and each
person's weights on every level. People are named by their ``person_id``.
The theme tree makes the bundle ``map_bundle/3``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..atlas.map_bundle import CohortBundle
    from ..project.project import Project

__all__ = ["project_bundle"]


def project_bundle(
    project: Project,
    *,
    cohort_id: str | None = None,
    build_date: str = "",
    producer: str = "cartolex",
) -> CohortBundle:
    """The bundle of *project*'s current results (the layout's applied tree, when built).

    The taxonomy (``map_taxonomy/1``) lists the finest level's nodes with the
    keywords on them, each under its top-level node; the theme tree and the
    people's weights on every level travel beside it. Raises
    ``FileNotFoundError`` when the themes are not applied yet.
    """
    import pandas as pd

    from ..atlas.map_bundle import TAXONOMY_SCHEMA, THEMES_FORMAT, build_bundle
    from ..atlas.map_merge import CohortInput
    from ..atlas.model_files import load_lexical_data
    from ..lexicon.subfields import _researcher_id_series
    from .engine import person_ids
    from .enginefiles import results_paths

    layout = project.layout
    paths = results_paths(layout.derived, layout.outputs, layout.root)
    for needed in (paths.lexical_data_json, paths.themes_applied_json, paths.theme_people_parquet):
        if not needed.exists():
            raise FileNotFoundError(f"{needed} not found: build the project's themes first")
    data = load_lexical_data(paths.lexical_data_json)
    meta = data.meta_ind
    rids = _researcher_id_series(meta).tolist()
    ids = person_ids(layout.stage("corpus.assemble"), [s.id for s in project.config.slots])
    entity = [ids.get(r) or r for r in rids]
    units = meta["unit"].fillna("").astype(str).tolist() if "unit" in meta.columns else None
    applied = json.loads(paths.themes_applied_json.read_text(encoding="utf-8"))
    themes = {
        "format": THEMES_FORMAT,
        "depth": applied["depth"],
        "levels": applied["levels"],
        "nodes": [
            {k: n[k] for k in ("id", "parent", "level", "order", "names", "color")}
            for n in applied["nodes"]
        ],
    }
    people = pd.read_parquet(paths.theme_people_parquet)
    entity_of = dict(zip(rids, entity, strict=True))
    weights = pd.DataFrame(
        {
            "entity_id": people["researcher_id"].map(entity_of),
            "level": people["level"].astype(int),
            "node": people["node"].astype(str),
            "weight": people["weight"].astype(float),
            "share": people["share"].astype(float),
        }
    )
    X_tf = data.X_tf if getattr(data, "X_tf", None) is not None else data.X
    cohort = CohortInput(
        cohort_id=cohort_id or project.config.identity.domain_title or "cohort",
        terms=[str(t) for t in data.terms],
        X_tf=X_tf,
        researcher_ids=entity,
        units=units,
    )
    return build_bundle(
        cohort,
        scores=data.X,
        taxonomy=_taxonomy(applied, paths.theme_keywords_csv, project, TAXONOMY_SCHEMA),
        build_date=build_date,
        producer=producer,
        themes=themes,
        theme_weights=weights,
    )


def _taxonomy(applied: dict[str, Any], keywords_csv: Path, project: Project, schema: str) -> dict:
    """``map_taxonomy/1``: the finest level's nodes and their keywords, under their top nodes."""
    import pandas as pd

    language = project.config.languages.reference
    nodes = {n["id"]: n for n in applied["nodes"]}

    def label(node: dict) -> str:
        names = node.get("names") or {}
        return str(names.get(language) or next(iter(names.values()), node["id"]))

    def top(node_id: str) -> str:
        while nodes[node_id]["parent"] is not None:
            node_id = nodes[node_id]["parent"]
        return node_id

    keywords = pd.read_csv(keywords_csv, dtype={"term": str, "node": str})
    finest = [n for n in applied["nodes"] if n["level"] == applied["depth"]]
    return {
        "schema": schema,
        "concepts": [
            {
                "id": n["id"],
                "label": label(n),
                "subfield_id": top(n["id"]),
                "terms": keywords.loc[keywords["node"] == n["id"], "term"].tolist(),
            }
            for n in finest
        ],
        "subfields": [
            {"id": n["id"], "label": label(n)} for n in applied["nodes"] if n["level"] == 1
        ],
    }
