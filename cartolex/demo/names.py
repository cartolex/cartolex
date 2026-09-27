# SPDX-License-Identifier: MIT
"""Invented names: people, places, institutions, groups and venues.

Surnames are built from syllables with a seeded generator, first names come
from a mixed international list, and every place is fictional. Nothing here
describes a real person, institution or journal. People carry no gender
attribute: the first-name list is one undivided pool.
"""

from __future__ import annotations

import random
import re
import unicodedata
from dataclasses import dataclass

FIRST_NAMES: tuple[str, ...] = (
    "Ada", "Adrien", "Aiko", "Alba", "Aleksi", "Amara", "Amir", "Anaïs", "Andrés",
    "Anouk", "Arjun", "Aurelio", "Ayla", "Bao", "Beatriz", "Benedek", "Björn", "Bram",
    "Camille", "Caoimhe", "Chidi", "Chloé", "Cosmin", "Dagny", "Dalia", "Dario",
    "Deniz", "Dmitri", "Eamon", "Élodie", "Elif", "Emeka", "Émile", "Esme", "Eun-ji",
    "Farah", "Felix", "Femi", "Florin", "Freya", "Gaël", "Giada", "Goran", "Hana",
    "Haruto", "Helga", "Hugo", "Ifeoma", "Ilse", "Imani", "Inès", "Ioana", "Ivo",
    "Jalen", "Joaquín", "Jonas", "Jun", "Kai", "Kalani", "Kenji", "Kofi", "Lars",
    "Leila", "Lena", "Lior", "Luca", "Lucía", "Maëlle", "Malik", "Marit", "Mateo",
    "Mei", "Mira", "Nadia", "Naveen", "Nico", "Nilufar", "Noor", "Oona", "Oskar",
    "Pablo", "Paola", "Priya", "Quentin", "Rafael", "Rania", "Ravi", "Rosa", "Ruben",
    "Sacha", "Sami", "Saoirse", "Selin", "Sigrid", "Soren", "Sunita", "Tamar",
    "Teodor", "Thea", "Tiago", "Tomás", "Uma", "Valentin", "Vera", "Wen", "Wren",
    "Xavi", "Yara", "Yusuf", "Zainab", "Zeynep", "Zoltan", "Zora",
)  # fmt: skip

# Syllables for invented surnames: a first syllable that may open with a
# consonant cluster, then simpler syllables, sometimes an ending. Chosen to
# read as plausible names from no particular country.
_FIRST_ONSETS = (
    "b", "br", "c", "d", "dr", "f", "g", "gr", "h", "k", "l", "m", "n", "p", "pr",
    "r", "s", "st", "t", "tr", "v", "z",
)  # fmt: skip
_INNER_ONSETS = ("b", "d", "f", "g", "l", "m", "n", "p", "r", "s", "t", "v", "z")
_NUCLEI = ("a", "e", "i", "o", "u", "a", "e", "o", "ai", "ou", "ie")
_INNER_CODAS = ("", "", "", "n", "r", "l")
_FINAL_CODAS = ("", "", "", "n", "r", "l", "s", "m", "nd", "rt", "st")
_ENDINGS = (
    "", "", "", "", "ec", "ard", "sen", "ini", "oux", "ez", "ley", "ic", "ath",
    "orne", "ille", "ant", "ius", "enko", "aro",
)  # fmt: skip
_AWKWARD = re.compile(r"(.)\1\1|[aeiou]{3}|[^aeiou]{3}")
_VOWEL_PAIR = re.compile(r"(?=([aeiou]{2}))")
_GOOD_PAIRS = frozenset({"ai", "ou", "ie", "iu"})


def _pronounceable(word: str) -> bool:
    if _AWKWARD.search(word):
        return False
    return all(m.group(1) in _GOOD_PAIRS for m in _VOWEL_PAIR.finditer(word))


def invented_surname(rng: random.Random) -> str:
    """Return one invented surname, e.g. ``Tavelin`` or ``Kousard``."""
    for _ in range(200):
        three = rng.random() < 0.2
        word = rng.choice(_FIRST_ONSETS) + rng.choice(_NUCLEI) + rng.choice(_INNER_CODAS)
        if three:
            word += rng.choice(_INNER_ONSETS) + rng.choice(_NUCLEI) + rng.choice(_INNER_CODAS)
        word += rng.choice(_INNER_ONSETS) + rng.choice(_NUCLEI) + rng.choice(_FINAL_CODAS)
        if not three:
            word += rng.choice(_ENDINGS)
        if 4 <= len(word) <= 10 and _pronounceable(word):
            return word[0].upper() + word[1:]
    raise RuntimeError("could not build a surname")  # pragma: no cover - unreachable


def fold(text: str) -> str:
    """Accent-stripped, lower-case ASCII form of *text* (``Élodie`` → ``elodie``)."""
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return stripped.encode("ascii", "ignore").decode("ascii").lower()


def slug(text: str) -> str:
    """Lower-case ASCII slug with hyphens (``Eun-ji Tavrelin`` → ``eun-ji-tavrelin``)."""
    return re.sub(r"[^a-z0-9]+", "-", fold(text)).strip("-")


@dataclass(frozen=True)
class Site:
    """A fictional coastal town. Coordinates are synthetic, not a real place's."""

    name: str
    code: str
    lat: float
    lon: float


SITES: tuple[Site, ...] = (
    Site("Port Aurel", "PAU", 47.14, -4.62),
    Site("Veldhaven", "VEL", 51.87, 2.35),
    Site("Saint-Onval", "SON", 45.83, -2.41),
    Site("Corvanne", "COR", 48.95, -5.73),
    Site("Brenmouth", "BRE", 50.42, -2.96),
    Site("Havrelune", "HAV", 49.88, -0.87),
    Site("Marsombre", "MAR", 42.93, 4.35),
    Site("Estrelle-sur-Mer", "EST", 42.41, 6.84),
    Site("Tidewick", "TID", 52.64, 1.12),
    Site("Pontrevel", "PON", 44.47, -2.18),
    Site("Kelvaro", "KEL", 41.85, 8.37),
    Site("Lostrand", "LOS", 53.21, 4.02),
)

# Institution name patterns; "{site}" is replaced by the site name.
INSTITUTION_PATTERNS: tuple[str, ...] = (
    "University of {site}",
    "{site} Institute of Marine Science",
    "{site} Ocean Observatory",
    "{site} Centre for Coastal Research",
    "Marine Station of {site}",
    "{site} Polytechnic",
)

GROUP_KINDS: tuple[tuple[str, str], ...] = (
    ("Laboratory", "L"),
    ("Unit", "U"),
    ("Group", "G"),
    ("Team", "T"),
)

# Invented venues. Place names in them are the fictional sites above.
VENUES_EN: tuple[str, ...] = (
    "Veldhaven Journal of Marine Science",
    "Port Aurel Coastal Letters",
    "Saint-Onval Review of Ocean Studies",
    "Tidewick Journal of Estuarine Research",
    "Brenmouth Fisheries Review",
    "Bulletin of the Havrelune Oceanographic Circle",
    "Pontrevel Studies in Coastal Society",
    "Corvanne Journal of Sea and Shore",
)
VENUES_FR: tuple[str, ...] = (
    "Cahiers littoraux de Marsombre",
    "Revue d'océanographie d'Estrelle",
    "Annales maritimes de Kelvaro",
)
PROCEEDINGS_EN: tuple[str, ...] = (
    "Proceedings of the Veldhaven Coastal Symposium",
    "Proceedings of the Port Aurel Ocean Sciences Meeting",
)
PROCEEDINGS_FR: tuple[str, ...] = ("Actes des Journées littorales de Saint-Onval",)
PREPRINT_SERVER = "OpenTide Preprints"
