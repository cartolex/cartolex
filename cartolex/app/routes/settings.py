# SPDX-License-Identifier: MIT
"""Settings of the project: languages, language models, the AI identity, data sources."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import Request, Response
from pydantic import BaseModel, Field

from ..deps import ProjectDep
from ..errors import ApiError
from ..etags import etag_of, expected_version, version_of
from ..routing import Routes, runtime_of

routes = Routes(tags=["settings"])

Language = Annotated[str, Field(pattern=r"^[a-z]{2}$")]

#: What changing each part of a frozen identity costs, in words.
CHANGE_COSTS = {
    "domain_title": "AI answers already paid for are not reused: they are paid for again",
    "ai": "AI answers already paid for are not reused: they are paid for again",
    "language_models": "every text is parsed again with the new model (time, not money)",
}


def _view(runtime: Any, ctx: Any) -> dict[str, Any]:
    from cartolex.lexicon import language_models as lm
    from cartolex.project.files import fingerprint
    from cartolex.project.models import LANGUAGES

    config = ctx.project.config
    identity = config.identity
    models = []
    for lang in lm.supported_languages():
        spec = lm.spec(lang)
        models.append(
            {
                "language": lang,
                "model": spec.identity,
                "licence": spec.licence,
                "installed": lm.installed_version(lang),
                "needed": lang in config.languages.corpus,
                "install": f"cartolex models add {lang}",
            }
        )
    ai = runtime.ai_access()
    return {
        "name": config.name,
        "identity": {
            "domain_title": identity.domain_title,
            "domain_description": identity.domain_description,
            "frozen": identity.frozen,
            "ai": identity.ai.model_dump(mode="json") if identity.ai else None,
            "language_models": dict(identity.language_models),
        },
        "change_costs": CHANGE_COSTS if identity.frozen else {},
        "languages": {**config.languages.model_dump(mode="json"), "available": list(LANGUAGES)},
        "models": models,
        "slots": [s.model_dump(mode="json") for s in config.slots],
        "overlays": [o.model_dump(mode="json") for o in config.overlays],
        "levels": [lv.model_dump(mode="json") for lv in config.levels],
        "data_sources": runtime.collection.describe(),
        "ai": {
            "providers": ["mistral"],
            "api_key_given": bool(ai is not None and (ai.api_key or ai.client_factory)),
            "sends": "keyword strings, never texts or people, with the field's title and "
            "description",
        },
        "version": version_of(fingerprint(ctx.layout.project_json)),
    }


@routes.get("/api/settings", action="settings.read")
def get_settings(request: Request, response: Response, ctx: ProjectDep) -> dict[str, Any]:
    """The project's settings, what changing a frozen identity costs, and the data sources."""
    view = _view(runtime_of(request), ctx)
    response.headers["ETag"] = etag_of(view["version"])
    return view


class AIBody(BaseModel):
    provider: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_-]{0,31}$")]
    model: Annotated[str, Field(min_length=1, max_length=100)]


class LanguagesBody(BaseModel):
    corpus: Annotated[list[Language], Field(min_length=1, max_length=8)]
    reference: Language = "en"
    display: Annotated[list[Language], Field(min_length=1, max_length=8)]


class SettingsBody(BaseModel):
    """The parts to change; ``confirm_identity_change`` accepts what a frozen identity costs."""

    name: Annotated[str | None, Field(min_length=1, max_length=200)] = None
    domain_title: Annotated[str | None, Field(min_length=1, max_length=300)] = None
    domain_description: Annotated[str | None, Field(max_length=4000)] = None
    ai: AIBody | None = None
    clear_ai: bool = False
    languages: LanguagesBody | None = None
    confirm_identity_change: bool = False


@routes.put("/api/settings", action="settings.write")
def put_settings(
    request: Request, response: Response, body: SettingsBody, ctx: ProjectDep
) -> dict[str, Any]:
    """Change the settings (send ``If-Match``). Changing a frozen identity is refused (409) with
    what it costs, unless ``confirm_identity_change`` is true."""
    from cartolex.project import StaleWrite
    from cartolex.project.files import fingerprint
    from cartolex.project.models import LANGUAGES, AIIdentity, Languages

    expected = expected_version(request)
    project = ctx.project
    with ctx.handle.mutex:
        found = fingerprint(ctx.layout.project_json)
        if found != expected:
            raise StaleWrite(ctx.layout.project_json, expected, found)
        config = project.config
        identity = config.identity
        updates: dict[str, Any] = {}
        changed: list[str] = []
        if body.domain_title is not None and body.domain_title != identity.domain_title:
            updates["domain_title"] = body.domain_title
            changed.append("field title")
        description = body.domain_description
        if description is not None and description != identity.domain_description:
            updates["domain_description"] = description
            changed.append("description")
        if body.clear_ai:
            updates["ai"] = None
            changed.append("AI identity")
        elif body.ai is not None:
            updates["ai"] = AIIdentity(**body.ai.model_dump())
            changed.append("AI identity")
        new = config.model_copy(update={"identity": identity.model_copy(update=updates)})
        if body.name is not None and body.name != config.name:
            new = new.model_copy(update={"name": body.name})
            changed.append("name")
        if body.languages is not None:
            bad = [
                x for x in [*body.languages.corpus, *body.languages.display] if x not in LANGUAGES
            ]
            if bad:
                raise ApiError.of(
                    "no_language_pack", languages=sorted(set(bad)), available=list(LANGUAGES)
                )
            new = new.model_copy(update={"languages": Languages(**body.languages.model_dump())})
            changed.append("languages")
        if not changed:
            raise ApiError.of("nothing_to_change")
        project.save_config(
            new,
            action="change " + ", ".join(changed),
            identity_change=body.confirm_identity_change,
        )
    view = _view(runtime_of(request), ctx)
    response.headers["ETag"] = etag_of(view["version"])
    return view
