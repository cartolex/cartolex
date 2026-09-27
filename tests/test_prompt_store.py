# SPDX-License-Identifier: MIT
"""The shipped prompt surface must exist, load, and carry its placeholders;
a missing or broken template fails with an error naming the file and the placeholder."""

from __future__ import annotations

import pytest

from cartolex.lexicon.prompt_store import PromptTemplateError, load_prompt, packaged_prompt_dir

SHIPPED = {
    "triage_typed_system": (
        "{domain_title}",
        "{domain_description}",
        "{reference_language_name}",
        "{person_whitelist_block}",
    ),
    "labels_translate_system": ("{language_name}",),
}


def test_shipped_prompts_dir_exists():
    assert packaged_prompt_dir().is_dir()


@pytest.mark.parametrize("name", sorted(SHIPPED))
def test_shipped_prompt_loads_with_placeholders(name: str):
    text = load_prompt(name, required_placeholders=SHIPPED[name]).text
    assert len(text) > 100  # a real template, not a stub


def test_missing_prompt_fails_loudly():
    with pytest.raises(PromptTemplateError, match="no_such_prompt.txt"):
        load_prompt("no_such_prompt")


def test_missing_placeholder_fails_loudly(tmp_path):
    (tmp_path / "broken.txt").write_text("no placeholders here", encoding="utf-8")
    with pytest.raises(PromptTemplateError, match=r"broken\.txt.*\{domain_title\}"):
        load_prompt("broken", required_placeholders=("{domain_title}",), prompt_dir=tmp_path)


def test_unknown_placeholder_fails_when_rendered(tmp_path):
    (tmp_path / "odd.txt").write_text("Domain {domain_title}, {typo}.", encoding="utf-8")
    template = load_prompt("odd", required_placeholders=("{domain_title}",), prompt_dir=tmp_path)
    with pytest.raises(PromptTemplateError, match=r"odd\.txt.*\{typo\}"):
        template.render(domain_title="x")


def test_unbalanced_brace_fails_when_loaded(tmp_path):
    (tmp_path / "bad.txt").write_text("Domain {domain_title", encoding="utf-8")
    with pytest.raises(PromptTemplateError, match=r"bad\.txt"):
        load_prompt("bad", prompt_dir=tmp_path)
