# SPDX-License-Identifier: MIT
"""The settings on the real app (the S demo world): one scenario over the sections; the start
screen on an app with no project: a new project from a folder of texts; the review screenshots."""

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
    page.get_by_role("button", name="Save", exact=True).click()
    page.get_by_text("Settings saved").wait_for()
    # a key of this computer: saved, then shown by its last four characters only
    section(ui, "AI")
    page.get_by_label("Mistral API key").fill("sk-demo-key-12345678")
    page.get_by_role("button", name="Save the key").click()
    page.get_by_text(re.compile(r"saved on this computer \(…5678\)")).wait_for()
    assert page.get_by_text("The AI clean-up can run by API.").is_visible()
    assert "sk-demo-key" not in page.content()
    # the build options; every other parameter is in its page's « Tune » panel, all in the Recipe
    section(ui, "Sizes and build options")
    page.get_by_role("link", name="open the recipe").wait_for()
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


@pytest.fixture()
def no_project(tmp_path, open_app, monkeypatch):
    """The app with no project open, new projects suggested under a fresh home folder."""
    from app_harness import AppServer

    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    server = AppServer(None, tmp_path / "app")
    yield server
    server.stop()


def test_a_new_project_from_the_start_screen(no_project, open_app, tmp_path):
    ui = open_app(no_project)
    page = ui.page
    ui.navigate("/start")
    page.get_by_role("heading", name="No project opened yet").wait_for()
    before = ui.token()
    page.get_by_role("button", name="Create a project").click()
    ui.wait_ready(before)
    page.get_by_role("radio", name=re.compile("^A folder of texts")).check()
    page.get_by_role("textbox", name=re.compile("^Name")).fill("Reef ecology")
    page.get_by_role("textbox", name=re.compile("^The field")).fill("Coral reef ecology")
    page.get_by_label("Description: the AI's context").fill("Reefs and their fishes.")
    folder = page.get_by_role("textbox", name=re.compile("^Folder"))
    assert folder.input_value() == str(tmp_path / "home" / "cartolex-projects" / "reef-ecology")
    page.get_by_role("button", name="Create the project").click()
    page.wait_for_url(re.compile(r"/\?start=folder$"))
    ui.wait_ready(0)
    assert (tmp_path / "home" / "cartolex-projects" / "reef-ecology" / "project.json").exists()
    ui.navigate("/start")
    page.get_by_role("button", name="Open Reef ecology").wait_for()
    assert page.get_by_text("Continue with this project").is_visible()


@pytest.mark.slow
def test_screenshots_of_the_start_screen(tmp_path, open_app, pytestconfig, monkeypatch):
    target = pytestconfig.getoption("--ui-screenshots")
    if not target:
        pytest.skip("pass --ui-screenshots DIR to write the screenshots")
    from pathlib import Path

    from app_harness import AppServer

    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    out = Path(target) / "start"
    out.mkdir(parents=True, exist_ok=True)
    for theme, locale in (("light", "en"), ("dark", "en"), ("light", "fr")):
        server = AppServer(None, tmp_path / f"app-{theme}-{locale}")
        try:
            ui = open_app(server, theme=theme, locale=locale)
            for name, path in (("start", "/start"), ("new", "/start?new=1")):
                ui.navigate(path)
                ui.page.wait_for_function("() => !document.querySelector('[aria-busy=true]')")
                ui.page.wait_for_timeout(200)
                ui.page.screenshot(path=str(out / f"{name}-{theme}-{locale}.png"), full_page=True)
            ui.page.context.close()
        finally:
            server.stop()
