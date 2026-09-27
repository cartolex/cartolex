# SPDX-License-Identifier: MIT
"""Registry of LLM prompt templates used across the pipeline.

This module *re-exports* the prompt constants that already live next to
their call sites and attaches a small metadata dict so a consuming
application can show them without duplicating the strings. Since 0.7.0 the only
LLM stage left is the keyword triage: the subfield hierarchy is deterministic
and curated by hand.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, TypedDict

from cartolex.lexicon.triage_typed import TYPED_PROMPT_OVERRIDE_NAME, load_typed_template

if TYPE_CHECKING:
    from cartolex.lexicon.prompt_store import PromptDir


class PromptTemplate(TypedDict):
    name: str
    title: str
    description: str
    used_by: str
    model_default: str
    template: str
    parameters: list[str]


def list_templates(prompt_dir: PromptDir | None = None) -> list[PromptTemplate]:
    """Return every LLM prompt template with its metadata.

    One entry per LLM stage — the keyword triage is the only one since 0.7.0.
    Templates are read from *prompt_dir* (default: the packaged prompts).
    """
    return [
        {
            "name": "triage_typed",
            "title": "Keyword triage — typed pass",
            "description": (
                "One LLM call per batch of terms. "
                "Classifies each term as C (concept), M (method) or O (object) "
                "when accepted, N/K/G/F when rejected. "
                "Anchored on the domain's reference subfields when a domain catalog lists them. "
                "Editable in the workspace: manual_data/llm_prompts/"
                f"{TYPED_PROMPT_OVERRIDE_NAME}"
            ),
            "used_by": "cartolex.lexicon.llm_triage.run_pipeline_stage_2_llm",
            "model_default": "mistral-small-latest",
            "template": load_typed_template(prompt_dir=prompt_dir).text,
            "parameters": ["domain_title", "subfields_block"],
        },
    ]


__all__ = [
    "PromptTemplate",
    "list_templates",
]
