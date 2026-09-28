# SPDX-License-Identifier: MIT
"""The demo's sources layer: deterministic, in unused identifier blocks, the world untouched."""

from __future__ import annotations

import re

import requests

from cartolex.demo import generate
from cartolex.demo.services import DemoServices, build_bibliography, sources_layer
from cartolex.demo.writers import world_files


def _issn_is_valid(issn: str) -> bool:
    digits = issn.replace("-", "")
    total = sum(int(d) * w for d, w in zip(digits[:7], range(8, 1, -1), strict=True))
    check = (11 - total % 11) % 11
    return digits[7] == ("X" if check == 10 else str(check))


def test_the_layer_is_deterministic_and_leaves_the_world_alone() -> None:
    before = world_files(generate("S", 0, "en,fr,pt", bodies=True))
    world = generate("S", 0, "en,fr,pt", bodies=True)
    a = sources_layer(build_bibliography(world))
    b = sources_layer(build_bibliography(generate("S", 0, "en,fr,pt", bodies=True)))
    assert a.deposits == b.deposits and a.scielo == b.scielo and a.arxiv == b.arxiv
    assert a.pmc == b.pmc and a.biorxiv == b.biorxiv and a.oa_links == b.oa_links
    assert world_files(world) == before
    plain = sources_layer(build_bibliography(generate("S", 0)))
    assert not plain.scielo  # a journal platform in Portuguese needs a Portuguese world
    assert plain.deposits


def test_identifiers_stay_in_blocks_nobody_uses() -> None:
    layer = sources_layer(build_bibliography(generate("S", 0, "en,fr,pt", bodies=True)))
    for d in layer.deposits.values():
        assert re.fullmatch(r"hal-09[789]\d{5}", d.hal_id) and d.docid >= 99_000_000
        assert d.doi is None or d.doi.startswith("10.5555/")
        assert d.arxiv is None or re.fullmatch(r"99\d\d\.\d{5}", d.arxiv)
    assert all(s >= 9_900_000 for s in layer.structures)
    for a in layer.scielo.values():
        assert not _issn_is_valid(a.issn) and a.pid.startswith(f"S{a.issn}")
    for e in layer.pmc.values():
        assert e.pmcid.startswith("PMC99") and int(e.pmid) >= 99_000_000
    assert all(doi.startswith("10.5555/") for doi in layer.biorxiv)


def test_the_special_cases_of_the_archive_exist() -> None:
    layer = sources_layer(build_bibliography(generate("S", 0)))
    deposits = list(layer.deposits.values())
    assert any(d.preprint_of for d in deposits), "preprints of published articles"
    assert any(d.world_work is None for d in deposits), "an outside homonym"
    world_doi = {w.work_id: w.doi for w in layer.world.works}
    missing = [d for d in deposits if d.world_work and world_doi[d.world_work] and not d.doi]
    assert missing, "deposits without their DOI"
    assert any(d.year != layer.work(d.world_work).year for d in missing), "the deposit's year"
    assert any(len(d.titles) > 1 for d in deposits), "a translated title"
    assert any(any(a.person_id is None for a in d.authors) for d in deposits), "outside authors"


def test_links_in_answers_point_at_the_server() -> None:
    with DemoServices(generate("XS", 0)) as services:
        layer = sources_layer(services.bibliography)
        deposit = next(d for d in layer.deposits.values() if d.file)
        reply = requests.get(
            f"{services.url('hal')}/search/",
            params={"q": f'halId_s:"{deposit.hal_id}"', "fl": "fileMain_s", "wt": "json"},
            timeout=5,
        ).json()
        link = reply["response"]["docs"][0]["fileMain_s"]
        assert link.startswith(services.base_url + "/hal/files/")
        pdf = requests.get(link, timeout=5)
        assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
