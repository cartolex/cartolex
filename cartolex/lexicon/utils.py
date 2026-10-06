# SPDX-License-Identifier: MIT
import re
import unicodedata

_WS_RE = re.compile(r"\s+")
_PUNCT_RE = re.compile(r"[^a-z0-9\s-]")  # applied after folding to lower-case ASCII
_DASH_RE = re.compile(r"[-\s]+")


def canonicalize_names(s: str) -> str:
    """
    Canonical form for identity fields (names).
    - unicode NFKD decomposition
    - strip diacritics (accents)
    - ascii-only
    - lower-case
    - normalize whitespace/punctuation
    Examples:
      "Cécile" -> "cecile"
      "  Jean-Luc " -> "jean luc" -> "jean-luc" (final join by '-')
      "D'Haën" -> "d haen" -> "d-haen"
    """
    if s is None:
        return ""
    s = str(s).strip()
    if not s:
        return ""

    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.encode("ascii", "ignore").decode("ascii")
    s = s.lower()

    s = _WS_RE.sub(" ", s)
    s = s.replace("’", "'")
    s = s.replace("'", " ")  # "d'Haen" -> "d haen"
    s = s.replace(".", " ")
    s = _PUNCT_RE.sub(" ", s)  # drops any remaining unusual punctuation
    s = _WS_RE.sub(" ", s).strip()
    s = _DASH_RE.sub("-", s)  # option: uniformiser espaces/tirets en '-'

    return s


#: The unit of a person who has none, as the engine names it.
NO_UNIT = "NA"


def _text(value: object) -> str:
    """An identity cell as text: a missing value (``None``, a float NaN) is empty."""
    if value is None or (isinstance(value, float) and value != value):
        return ""
    return str(value).strip()


def unit_value(raw: object) -> str:
    """A unit as the engine names it: :data:`NO_UNIT` when there is none (an empty or
    missing value, or the text ``nan`` an earlier table may hold)."""
    unit = _text(raw)
    return NO_UNIT if not unit or unit.lower() == "nan" else unit


def make_researcher_id(last_name: str, first_name: str, unit: str) -> str:
    """
    Canonical ID used for indexing / joins.
    Names are canonicalized (ASCII-safe) via utils.canonicalize_names; a missing
    name is empty. The unit is kept as a stripped string (to preserve mapping with
    UNITS codes), a missing one named :data:`NO_UNIT` (:func:`unit_value`), so a
    person without a unit has one id whether a table wrote their unit empty or ``NA``.
    """
    last_c = canonicalize_names(_text(last_name))
    first_c = canonicalize_names(_text(first_name))
    unit_c = unit_value(unit)
    return f"{last_c}||{first_c}||{unit_c}"
