# SPDX-License-Identifier: MIT
"""The offline site opened from `file://`, with every request refused: it needs no server and
no network. A search leads to a person's page, then to the app's atlas mounted over the
site's files; an unknown address says « Not found »; a page without its files says to unzip
them first."""

from __future__ import annotations

import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest
from site_walk import open_page, walk

from cartolex.project.project import Project
from cartolex.site.builder import SiteOptions, build_site

HERE = Path(__file__).resolve().parent


@pytest.fixture(scope="module")
def site(demo_s, tmp_path_factory) -> Path:
    """A pseudonymous site of the S world, with the texts' titles (built on a copy)."""
    from app_harness import copy_project

    project = Project.open(copy_project(demo_s, tmp_path_factory.mktemp("site") / "p"))
    try:
        record = build_site(project, SiteOptions(names=False, texts="titles"))
        return project.layout.outputs / "sites" / record["id"]
    finally:
        project.close()


def test_the_site_opens_from_a_file_without_the_network(site, browser):
    start = time.monotonic()
    page, errors, refused, context = open_page(browser, (site / "index.html").as_uri())
    try:
        page.locator("#cx-site:not([hidden])").wait_for(timeout=5000)
        assert time.monotonic() - start < 3
        walk(page)
        assert errors == [] and refused == []
    finally:
        context.close()


def test_a_page_without_its_files_says_to_unzip_them(site, browser, tmp_path):
    shutil.copy(site / "index.html", tmp_path / "index.html")
    page, _, refused, context = open_page(browser, (tmp_path / "index.html").as_uri())
    try:
        page.wait_for_timeout(200)
        assert page.locator("#cx-missing").is_visible()
        assert "Unzip the whole folder first" in page.locator("#cx-missing").inner_text()
        assert refused == []
    finally:
        context.close()


def test_the_site_opens_in_firefox_when_a_build_is_installed(site):
    run = subprocess.run(
        [sys.executable, str(HERE / "site_walk.py"), str(site / "index.html")],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if run.returncode == 77:
        pytest.skip("no Firefox build for this Playwright version")
    assert run.returncode == 0, run.stdout + run.stderr
