# SPDX-License-Identifier: MIT
"""The settings on the real app (the S demo world): one keyboard-and-mouse scenario over the
sections, and the review screenshots."""

from __future__ import annotations

import re

import pytest


@pytest.fixture()
def settings(demo_s, app_for, open_app, monkeypatch):
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
    monkeypatch.delenv("OPENALEX_API_KEY", raising=False)
    server = app_for(demo_s)
    ui = open_app(server)
    ui.server = server
    ui.navigate("/settings")
    return ui


def section(ui, name: str) -> None:
    before = ui.token()
    ui.page.get_by_role("navigation", name="Sections of the settings").get_by_role(
        "link", name=name, exact=True
    ).click()
    ui.wait_ready(before)


def test_the_sections_of_the_settings(settings):
    ui = settings
    page = ui.page
    assert page.get_by_role("heading", name="Interface", level=2).is_visible()
    # the project's description, labelled as the AI's context
    section(ui, "Project")
    context = page.get_by_label("Description: the AI's context")
    context.fill("Coastal and ocean sciences; not the economics of fisheries.")
    page.get_by_role("button", name="Save").click()
    page.get_by_text("Settings saved").wait_for()
    # a key of this computer: saved, then shown by its last four characters only
    section(ui, "AI")
    page.get_by_label("Mistral API key").fill("sk-demo-key-12345678")
    page.get_by_role("button", name="Save the key").click()
    page.get_by_text(re.compile(r"saved on this computer \(…5678\)")).wait_for()
    assert page.get_by_text("The AI clean-up can run by API.").is_visible()
    assert "sk-demo-key" not in page.content()
    # the build options and every parameter with its origin
    section(ui, "Sizes and build options")
    page.get_by_role("button", name="Show every parameter").click()
    page.get_by_role("heading", name=re.compile("place keywords in a common space")).wait_for()
    assert page.get_by_text(re.compile(r"by a rule: 20 up to 2 000 people")).count() >= 1
    # stop words, one per line
    section(ui, "Stop words")
    page.get_by_label("Never keywords").first.fill("coast\nshore")
    page.get_by_role("button", name="Save").click()
    page.get_by_text("Stop words saved").wait_for()
    # the diagnostic, without project data
    section(ui, "Backup, diagnostics, reset")
    page.get_by_role("button", name="Show the diagnostic").click()
    page.locator(".cx-settings__pre").wait_for()
    assert "cartolex" in page.locator(".cx-settings__pre").inner_text()


@pytest.mark.slow
def test_screenshots_of_the_settings(demo_s, app_for, open_app, pytestconfig):
    target = pytestconfig.getoption("--ui-screenshots")
    if not target:
        pytest.skip("pass --ui-screenshots DIR to write the screenshots")
    from pathlib import Path

    out = Path(target) / "settings"
    out.mkdir(parents=True, exist_ok=True)
    ids = [
        "interface",
        "project",
        "languages",
        "ai",
        "sources",
        "build",
        "words",
        "prompts",
        "privacy",
        "care",
    ]
    for theme, locale in (("light", "en"), ("dark", "en"), ("light", "fr")):
        ui = open_app(app_for(demo_s), theme=theme, locale=locale)
        for sid in ids:
            ui.navigate(f"/settings?section={sid}")
            ui.page.wait_for_function("() => !document.querySelector('[aria-busy=true]')")
            ui.page.wait_for_timeout(200)
            ui.page.screenshot(path=str(out / f"{sid}-{theme}-{locale}.png"), full_page=True)
        ui.page.context.close()
