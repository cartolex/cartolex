# SPDX-License-Identifier: MIT
"""The language models the keyword extraction parses texts with.

Each corpus language the extraction supports has one pinned spaCy model: its
package name, version, licence, and the address and sha256 of its wheel. The
models are separate installs (they carry their own licences and are never
bundled with cartolex: the Spanish model is under the GNU GPL, the Italian one
under a non-commercial licence); nothing here downloads anything. A model that is not
installed, or installed at another version, is an error with the command that
installs the right one: there is no silent fallback to another model or to
another extraction method.

``spacy`` itself is imported only when a model is loaded, so importing this
module is cheap and has no side effect.
"""

from __future__ import annotations

import gc
import importlib.metadata
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from spacy.language import Language

__all__ = [
    "INSTALL_VERB",
    "MODELS",
    "UNUSED_COMPONENTS",
    "LanguageModel",
    "LanguageModelMissing",
    "identity",
    "installed",
    "installed_version",
    "load",
    "release",
    "require",
    "spec",
    "supported_languages",
]

_RELEASES = "https://github.com/explosion/spacy-models/releases/download"


@dataclass(frozen=True)
class LanguageModel:
    """One pinned spaCy model: what to install, from where, under which licence."""

    lang: str
    name: str
    version: str
    licence: str
    sha256: str

    @property
    def wheel(self) -> str:
        """File name of the model's wheel."""
        return f"{self.name}-{self.version}-py3-none-any.whl"

    @property
    def url(self) -> str:
        """Address of the model's wheel (a release of the spaCy models)."""
        return f"{_RELEASES}/{self.name}-{self.version}/{self.wheel}"

    @property
    def identity(self) -> str:
        """``name@version``: the model identity recorded with runs and cache entries."""
        return f"{self.name}@{self.version}"

    @property
    def requirement(self) -> str:
        """A pip requirement for exactly this wheel, with its hash."""
        return f"{self.name} @ {self.url}#sha256={self.sha256}"

    @property
    def pip_command(self) -> str:
        """The command that installs this model into the current environment."""
        return f'python -m pip install "{self.requirement}"'


#: The pinned model of each supported corpus language.
MODELS: Mapping[str, LanguageModel] = MappingProxyType(
    {
        "en": LanguageModel(
            lang="en",
            name="en_core_web_md",
            version="3.8.0",
            licence="MIT",
            sha256="5e6329fe3fecedb1d1a02c3ea2172ee0fede6cea6e4aefb6a02d832dba78a310",
        ),
        "fr": LanguageModel(
            lang="fr",
            name="fr_core_news_md",
            version="3.8.0",
            licence="LGPL-LR",
            sha256="8a70d090a54ef77525c3ffa6a6195b9d365f2cf369ae1cd84ede93f3d709079e",
        ),
        "pt": LanguageModel(
            lang="pt",
            name="pt_core_news_md",
            version="3.8.0",
            licence="CC BY-SA 4.0",
            sha256="54382cda034e41f3ec605ceeb924cd6cfdbced00a65f7afa12218520ba7008c5",
        ),
        "es": LanguageModel(
            lang="es",
            name="es_core_news_md",
            version="3.8.0",
            licence="GNU GPL 3.0",
            sha256="478b8bb3f3e8eb149192f7d80e7cd64f990b7e9bccbc5329df41a57e86326be2",
        ),
        "de": LanguageModel(
            lang="de",
            name="de_core_news_md",
            version="3.8.0",
            licence="MIT",
            sha256="b903f59220f1e76dd672acdaa7fa454d6703fe056c5ccd6457820e70874116d0",
        ),
        "it": LanguageModel(
            lang="it",
            name="it_core_news_md",
            version="3.8.0",
            licence="CC BY-NC-SA 3.0",
            sha256="a731d2d8e7c5a7093ac42f88774ea2a2ad3d5809959eedee65632b3533f804ec",
        ),
    }
)

#: Pipeline components the extraction never uses: not loaded at all.
UNUSED_COMPONENTS: tuple[str, ...] = ("ner",)

#: The cartolex command that installs a model (followed by the language code).
INSTALL_VERB = "cartolex models add"


class LanguageModelMissing(RuntimeError):
    """The language model a corpus language needs is not installed (or not the pinned one)."""

    def __init__(self, lang: str, message: str) -> None:
        super().__init__(message)
        self.lang = lang


def supported_languages() -> tuple[str, ...]:
    """The corpus languages the extraction has a model for."""
    return tuple(MODELS)


def spec(lang: str) -> LanguageModel:
    """The pinned model of *lang*; raises :class:`LanguageModelMissing` for another language."""
    model = MODELS.get(str(lang).strip().lower())
    if model is None:
        raise LanguageModelMissing(
            str(lang),
            f"No language model is known for the corpus language {lang!r}: the keyword "
            f"extraction supports {', '.join(MODELS)}. Remove it from the corpus languages "
            "(KeywordsConfig.corpus_languages).",
        )
    return model


def installed_version(lang: str) -> str | None:
    """The installed version of *lang*'s model package, or ``None`` when it is absent."""
    try:
        return importlib.metadata.version(spec(lang).name)
    except importlib.metadata.PackageNotFoundError:
        return None


def installed(lang: str) -> bool:
    """Whether the pinned model of *lang* is installed (the pinned version, not another)."""
    return installed_version(lang) == spec(lang).version


def _install_hint(model: LanguageModel) -> str:
    return (
        f"Install it with `{INSTALL_VERB} {model.lang}`, or with pip:\n"
        f"    {model.pip_command}\n"
        f"(licence of the model: {model.licence})."
    )


def require(lang: str) -> LanguageModel:
    """The pinned model of *lang*, checked to be installed.

    Raises :class:`LanguageModelMissing` naming the language, the model and
    the command that installs it when the model is absent or installed at
    another version.
    """
    model = spec(lang)
    found = installed_version(lang)
    if found == model.version:
        return model
    if found is None:
        problem = (
            f"The keyword extraction needs the language model {model.name} {model.version} "
            f"for the corpus language {model.lang!r}, and it is not installed."
        )
    else:
        problem = (
            f"The keyword extraction needs the language model {model.name} {model.version} "
            f"for the corpus language {model.lang!r}; version {found} is installed instead."
        )
    raise LanguageModelMissing(model.lang, f"{problem} {_install_hint(model)}")


def identity(lang: str) -> str:
    """``name@version`` of *lang*'s pinned model (for run records and cache keys)."""
    return spec(lang).identity


#: Pipelines loaded in this process, by language (see :func:`load` and :func:`release`).
_LOADED: dict[str, Any] = {}
_LOCK = threading.Lock()


def load(lang: str) -> Language:
    """The spaCy pipeline of *lang*, loaded once per process, without unused components.

    Raises :class:`LanguageModelMissing` when the pinned model is not installed.
    The pipeline stays loaded until :func:`release`.
    """
    model = require(lang)
    with _LOCK:
        nlp = _LOADED.get(model.lang)
        if nlp is None:
            import spacy

            nlp = _LOADED[model.lang] = spacy.load(model.name, exclude=list(UNUSED_COMPONENTS))
        return nlp


def release(lang: str | None = None) -> None:
    """Unload the pipeline of *lang* (every loaded pipeline when ``None``) to free its memory.

    The next :func:`load` loads it again.
    """
    with _LOCK:
        if lang is None:
            _LOADED.clear()
        else:
            _LOADED.pop(str(lang).strip().lower(), None)
    gc.collect()
