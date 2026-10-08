# SPDX-License-Identifier: MIT
"""The AI services the clean-up by API can call.

They speak the same chat-completion protocol, so one client serves them all: only the
address, the key and the model change. Each service has its own key, and a key is only
ever sent to the service that issued it.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["PROVIDERS", "AIProvider", "provider"]


@dataclass(frozen=True)
class AIProvider:
    """One AI service: its address, its default model, and where its key may come from."""

    id: str
    label: str
    api_url: str
    default_model: str
    env_var: str


PROVIDERS: dict[str, AIProvider] = {
    p.id: p
    for p in (
        AIProvider(
            id="mistral",
            label="Mistral AI",
            api_url="https://api.mistral.ai",
            default_model="mistral-medium-latest",
            env_var="MISTRAL_API_KEY",
        ),
        AIProvider(
            id="albert",
            label="Albert (DINUM)",
            api_url="https://albert.api.etalab.gouv.fr",
            # the closest to Mistral Medium among the models Albert opens to every user
            default_model="gpt-oss-120b",
            env_var="ALBERT_API_KEY",
        ),
    )
}


def provider(provider_id: str) -> AIProvider:
    """The service named *provider_id*; ``ValueError`` when cartolex cannot call it."""
    try:
        return PROVIDERS[provider_id]
    except KeyError:
        known = ", ".join(PROVIDERS)
        raise ValueError(f"unknown AI provider {provider_id!r} (known: {known})") from None
