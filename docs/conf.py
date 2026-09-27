# SPDX-License-Identifier: MIT
"""Sphinx configuration for the cartolex documentation (MyST Markdown, strict builds)."""

project = "cartolex"
author = "cartolex contributors"
extensions = ["myst_parser"]
source_suffix = {".md": "markdown"}
exclude_patterns = ["_build"]
myst_heading_anchors = 3
html_theme = "furo"
html_title = "cartolex"
