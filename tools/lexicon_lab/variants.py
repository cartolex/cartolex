# SPDX-License-Identifier: MIT
"""The choices the lab compares, one at a time around the current defaults."""

from __future__ import annotations

from dataclasses import dataclass, replace

from cartolex.lexicon.scoring import BandRules, ScoringOptions

BASE = ScoringOptions()


@dataclass(frozen=True)
class Variant:
    """One setting of the scoring: the family it belongs to, its label and its options."""

    family: str
    label: str
    options: ScoringOptions
    names: bool = False  # set aside recognised names of people and places
    needs_bodies: bool = False

    @property
    def id(self) -> str:
        return f"{self.family}: {self.label}"


def _v(family: str, label: str, **changes) -> Variant:
    names = changes.pop("names", False)
    bodies = changes.pop("needs_bodies", False)
    return Variant(family, label, replace(BASE, **changes), names, bodies)


BASELINE = Variant("baseline", "current defaults", BASE)

#: One family per open choice; the first variant of each family is the current default.
FAMILIES: dict[str, list[Variant]] = {
    "of complement": [
        _v("of complement", "on (current)"),
        _v("of complement", "off", of_complement=False),
    ],
    "counting unit": [
        _v("counting unit", "person (current)"),
        _v("counting unit", "text", counting_unit="text"),
        _v("counting unit", "organisation", counting_unit="organisation"),
    ],
    "text vote": [
        _v("text vote", "frequency (current)"),
        _v("text vote", "presence", vote="presence"),
        _v("text vote", "sublinear", vote="sublinear"),
    ],
    "part weights": [
        _v("part weights", "equal (current)", needs_bodies=True),
        _v("part weights", "body 0.5", part_weights={"body": 0.5}, needs_bodies=True),
        _v("part weights", "body 0.25", part_weights={"body": 0.25}, needs_bodies=True),
    ],
    "length bonus": [
        _v("length bonus", "α = 2 (current)"),
        _v("length bonus", "α = 1", length_bonus_alpha=1.0),
        _v("length bonus", "α = 0 (none)", length_bonus_alpha=0.0),
    ],
    "names": [
        _v("names", "not recognised (current)"),
        _v("names", "people and places set aside", names=True),
    ],
}

#: Operating points of the bands: (keep_share, drop_share, fragment rule on).
BAND_POINTS = [
    (keep, drop, fragments)
    for fragments in (True, False)
    for keep in (1.0, 0.5)
    for drop in (0.0, 0.1, 0.2)
]


def band_variant(keep: float, drop: float, fragments: bool, base: ScoringOptions = BASE) -> Variant:
    rules = BandRules(keep_share=keep, drop_share=drop, fragment_share=0.9 if fragments else 1.1)
    label = f"keep {keep:.0%}, drop {drop:.0%}, fragments {'on' if fragments else 'off'}"
    return Variant("bands", label, replace(base, bands=rules))


def recommended() -> Variant:
    """The default set the lab recommends (see docs/dev/lexicon-lab.md)."""
    return Variant("recommended", "recommended set", replace(BASE, of_complement=False))
