# SPDX-License-Identifier: MIT
"""Synthetic identifiers that can never belong to a real person or work.

* ORCID-format identifiers in the block ``0000-0000-XXXX-XXXX``, which no
  real ORCID record uses, with a valid ISO 7064 MOD 11-2 check character;
* author identifiers in the style of a bibliographic index, ``A999`` followed
  by seven digits;
* open-archive author slugs ``demo-<first>-<last>``;
* DOIs under the test prefix ``10.5555``:
  ``10.5555/cartolex-demo.<size>.<n>``.
"""

from __future__ import annotations

import random
import re

from .names import slug

ORCID_RE = re.compile(r"^0000-0000-\d{4}-\d{3}[\dX]$")
OPENALEX_RE = re.compile(r"^A999\d{7}$")
IDHAL_RE = re.compile(r"^demo-[a-z0-9]+(?:-[a-z0-9]+)*$")
DOI_PREFIX = "10.5555/cartolex-demo."
DOI_RE = re.compile(r"^10\.5555/cartolex-demo\.[a-z]+\.\d+$")


def orcid_check_character(base_digits: str) -> str:
    """ISO 7064 MOD 11-2 check character for the first 15 digits of an ORCID.

    >>> orcid_check_character("000000001234567")
    '2'
    """
    if len(base_digits) != 15 or not base_digits.isdigit():
        raise ValueError("expected 15 digits")
    total = 0
    for digit in base_digits:
        total = (total + int(digit)) * 2
    result = (12 - total % 11) % 11
    return "X" if result == 10 else str(result)


def is_valid_orcid(value: str) -> bool:
    """True when *value* has the ORCID shape and a correct check character."""
    if not re.fullmatch(r"\d{4}-\d{4}-\d{4}-\d{3}[\dX]", value):
        return False
    digits = value.replace("-", "")
    return orcid_check_character(digits[:15]) == digits[15]


def make_orcid(rng: random.Random) -> str:
    """A random ORCID-format identifier in the unissued ``0000-0000`` block."""
    body = "00000000" + "".join(str(rng.randrange(10)) for _ in range(7))
    full = body + orcid_check_character(body)
    return "-".join(full[i : i + 4] for i in range(0, 16, 4))


def make_openalex_id(rng: random.Random) -> str:
    """A random author identifier ``A999`` + seven digits."""
    return "A999" + "".join(str(rng.randrange(10)) for _ in range(7))


def make_idhal(first_name: str, last_name: str) -> str:
    """The open-archive author slug ``demo-<first>-<last>`` (ASCII, lower case)."""
    return f"demo-{slug(first_name)}-{slug(last_name)}"


def make_doi(size: str, number: int) -> str:
    """The DOI of work *number* in a world of the given *size*."""
    return f"{DOI_PREFIX}{size.lower()}.{number}"
