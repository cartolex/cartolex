# SPDX-License-Identifier: MIT
"""The offline site: a self-contained atlas of a project that opens from ``file://``.

A site is a folder of static files (``outputs/sites/<dated>/``): one page,
classic scripts (a page opened from ``file://`` cannot load ES modules), the
map's modules turned into one script, the app's colour tokens, the data split
per page, and a README that says to unzip the folder first. It needs no
server, no network and no web font.

- :mod:`cartolex.site.data` gathers what a site shows from the built project:
  the theme tree, the map (people, keywords, organisations, projected people),
  each person's and organisation's themes and keywords, who writes with whom
  (co-authors, and the organisations each writes with), and on request the
  titles (and abstracts) of the texts, never a full text.
- :mod:`cartolex.site.checks` gives the privacy summary and the checks before
  publishing (names, private parts, the words of the themes, a stale map).
- :mod:`cartolex.site.builder` writes a build, never over an earlier one,
  keeps the ``latest`` marker and says when a build is stale.
- :mod:`cartolex.site.exports` makes the figures, the theme table, the map
  bundle and the project as one file.

People are shown by name only when the person building says so at each build
(else by pseudonyms); organisations are always named.
"""

from __future__ import annotations

from .builder import OfflineSiteBuilder, SiteOptions, list_builds

__all__ = ["OfflineSiteBuilder", "SiteOptions", "list_builds"]
