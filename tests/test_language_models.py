# SPDX-License-Identifier: MIT
"""The pinned language models: registry, install check, error message and loading."""

from __future__ import annotations

import importlib.metadata
import re
from pathlib import Path

import pytest

from cartolex.lexicon import language_models as lm

ROOT = Path(__file__).resolve().parent.parent


def _fake_versions(monkeypatch: pytest.MonkeyPatch, versions: dict[str, str | None]) -> None:
    real = importlib.metadata.version

    def version(name: str) -> str:
        if name in versions:
            if versions[name] is None:
                raise importlib.metadata.PackageNotFoundError(name)
            return versions[name]
        return real(name)

    monkeypatch.setattr(lm.importlib.metadata, "version", version)


def test_registry_pins_three_languages() -> None:
    assert lm.supported_languages() == ("en", "fr", "pt")
    for lang, model in lm.MODELS.items():
        assert model.lang == lang
        assert model.version == "3.8.0"
        assert re.fullmatch(r"[0-9a-f]{64}", model.sha256)
        assert model.url.endswith(f"/{model.name}-3.8.0/{model.name}-3.8.0-py3-none-any.whl")
        assert lm.identity(lang) == f"{model.name}@3.8.0"
    assert {m.licence for m in lm.MODELS.values()} == {"MIT", "LGPL-LR", "CC BY-SA 4.0"}


def test_requirements_file_matches_the_registry() -> None:
    """The check installs exactly the pinned wheels, with their hashes."""
    text = (ROOT / "tools" / "requirements-models.txt").read_text(encoding="utf-8")
    found = {
        name: (url, sha)
        for name, url, sha in re.findall(
            r"^(\S+) @ (\S+) \\\n\s+--hash=sha256:([0-9a-f]{64})", text, re.M
        )
    }
    assert set(found) == {m.name for m in lm.MODELS.values()}
    for model in lm.MODELS.values():
        assert found[model.name] == (model.url, model.sha256)


def test_unknown_language() -> None:
    with pytest.raises(lm.LanguageModelMissing, match="'de'.*en, fr, pt"):
        lm.require("de")


def test_missing_model_names_language_model_and_commands(monkeypatch) -> None:
    _fake_versions(monkeypatch, {"pt_core_news_md": None})
    assert not lm.installed("pt")
    with pytest.raises(lm.LanguageModelMissing) as info:
        lm.require("pt")
    message = str(info.value)
    assert info.value.lang == "pt"
    assert "pt_core_news_md 3.8.0" in message and "'pt'" in message
    assert "cartolex models add pt" in message
    assert lm.MODELS["pt"].pip_command in message
    assert f"#sha256={lm.MODELS['pt'].sha256}" in message
    assert "CC BY-SA 4.0" in message


def test_other_version_is_not_accepted(monkeypatch) -> None:
    """No silent fallback: another version of the model is an error too."""
    _fake_versions(monkeypatch, {"fr_core_news_md": "3.7.0"})
    assert not lm.installed("fr")
    with pytest.raises(lm.LanguageModelMissing, match="version 3.7.0 is installed instead"):
        lm.load("fr")


def test_importing_the_module_does_not_import_spacy() -> None:
    import subprocess
    import sys

    code = (
        "import sys, cartolex.lexicon.language_models, cartolex.lexicon.extract_raw; "
        "print('spacy' in sys.modules)"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "False"


@pytest.mark.models("en")
def test_load_is_cached_and_leaves_out_unused_components() -> None:
    lm.release()
    nlp = lm.load("en")
    assert lm.load("en") is nlp
    assert not set(lm.UNUSED_COMPONENTS) & set(nlp.pipe_names)
    assert {"tagger", "parser", "lemmatizer"} <= set(nlp.pipe_names)
    lm.release("en")
    assert lm.load("en") is not nlp
    lm.release()
