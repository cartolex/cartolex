# SPDX-License-Identifier: MIT
"""Sphinx configuration for the cartolex documentation (MyST Markdown, strict builds)."""

import datetime as _dt
import re as _re
from pathlib import Path as _Path

project = "cartolex"
author = "Elisa Klüger and Pierre Ronceray"
copyright = "2025–2026, Elisa Klüger (Aix-Marseille Université) and Pierre Ronceray (CNRS)"
extensions = ["myst_parser"]
source_suffix = {".md": "markdown"}
exclude_patterns = ["_build"]
myst_heading_anchors = 3
html_theme = "furo"
html_title = "cartolex"
# The logo in the light and the dark theme: the app's mark
# (cartolex/app/static/brand/mark.svg) with its colours fixed for each theme, since a
# picture does not follow the theme chosen on the page.
html_static_path = ["_static"]
html_css_files = ["cartolex-docs.css"]
html_favicon = "_static/cartolex-mark-light.svg"
html_theme_options = {
    "light_logo": "cartolex-mark-light.svg",
    "dark_logo": "cartolex-mark-dark.svg",
}

# The version these pages describe, from the package's metadata (the source's
# pyproject.toml), for the citation; and the year they were built.
_pyproject = (_Path(__file__).resolve().parent.parent / "pyproject.toml").read_text(
    encoding="utf-8"
)
release = version = _re.search(r'(?m)^version = "([^"]+)"', _pyproject).group(1)
myst_enable_extensions = ["substitution"]
_year = str(_dt.date.today().year)
_bibtex = f"""```bibtex
@software{{cartolex,
  author  = {{Klüger, Elisa and Ronceray, Pierre}},
  title   = {{cartolex}},
  version = {{{version}}},
  year    = {{{_year}}},
  doi     = {{10.5281/zenodo.23223331}},
  url     = {{https://doi.org/10.5281/zenodo.23223331}},
  license = {{MIT}}
}}
```"""
myst_substitutions = {"version": version, "year": _year, "bibtex": _bibtex}
