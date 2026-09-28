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


BASELINE = Variant("baseline", "defaults", BASE)

#: One family per open choice; the first variant of each family is the default.
FAMILIES: dict[str, list[Variant]] = {
    "of complement": [
        _v("of complement", "off (default)"),
        _v("of complement", "on", of_complement=True),
    ],
    "counting unit": [
        _v("counting unit", "person (default)"),
        _v("counting unit", "text", counting_unit="text"),
        _v("counting unit", "organisation", counting_unit="organisation"),
    ],
    "text vote": [
        _v("text vote", "frequency (default)"),
        _v("text vote", "presence", vote="presence"),
        _v("text vote", "sublinear", vote="sublinear"),
    ],
    "part weights": [
        _v("part weights", "equal (default)", needs_bodies=True),
        _v("part weights", "body 0.5", part_weights={"body": 0.5}, needs_bodies=True),
        _v("part weights", "body 0.25", part_weights={"body": 0.25}, needs_bodies=True),
    ],
    "length bonus": [
        _v("length bonus", "α = 2 (default)"),
        _v("length bonus", "α = 1", length_bonus_alpha=1.0),
        _v("length bonus", "α = 0 (none)", length_bonus_alpha=0.0),
    ],
    "names": [
        _v("names", "not recognised (default)"),
        _v("names", "people and places set aside", names=True),
    ],
    "common modifiers": [
        _v("common modifiers", "off (default)"),
        _v("common modifiers", "to check, 20 %", bands=replace(BASE.bands, generic_spread=0.2)),
    ],
}


def _share(x: float | None) -> str:
    return "off" if x is None else f"{x:.0%}"


def _band_label(r: BandRules) -> str:
    return (
        f"keep {r.keep_share:.0%}, low score {r.drop_share:.0%}, "
        f"fragments {_share(r.fragment_share)}, common modifiers {_share(r.generic_spread)}"
    )


def recommended() -> Variant:
    """The set the lab recommends (docs/dev/lexicon-lab.md): the defaults, since gate G2."""
    return Variant("recommended", "recommended set", BASE)


_REC = recommended().options.bands

#: Operating points of the bands, around the recommended rules: every
#: combination of the keep share, the low-score share and the part-of rule
#: (every time, or off), then the part-of rule at 90 % and the common-modifier
#: rule on (20 %).
BAND_POINTS: list[BandRules] = [
    replace(_REC, keep_share=keep, drop_share=drop, fragment_share=fragments)
    for fragments in (_REC.fragment_share, None)
    for keep in (1.0, 0.5)
    for drop in (0.0, 0.1, 0.2)
] + [
    replace(_REC, fragment_share=0.9),
    replace(_REC, generic_spread=0.2),
]


def band_variant(rules: BandRules, base: ScoringOptions = BASE) -> Variant:
    return Variant("bands", _band_label(rules), replace(base, bands=rules))


def diagnostic(names: bool) -> Variant:
    """Every band rule on (low score: the least specific tenth), to see what each one catches."""
    rules = replace(BASE.bands, drop_share=max(BASE.bands.drop_share, 0.1))
    return Variant("reasons", "every rule on", replace(BASE, bands=rules), names=names)
