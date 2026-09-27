# SPDX-License-Identifier: MIT
"""Synthetic demo world: an invented research community on coastal and marine systems.

The generator is deterministic: the same ``(size, seed)`` always gives the
same people, groups, works and texts, byte for byte. Use it for tests,
reference runs, documentation, screenshots and usability sessions::

    from cartolex.demo import generate

    world = generate(size="S", seed=0)
    world.write("demo-world")            # neutral cartolex-demo/1 files
    world.write_corpus("demo-workspace")  # the engine's corpus contract

or from the command line::

    python -m cartolex.demo create --size S --seed 0 --out DIR [--corpus] [--languages en,fr,pt]
"""

from __future__ import annotations

from .generator import generate
from .model import (
    FORMAT,
    GENERATOR_VERSION,
    LANGUAGE_SETS,
    SIZES,
    DemoWorld,
    Group,
    Person,
    SizeSpec,
    Work,
)
from .writers import lexicon_truth

__all__ = [
    "FORMAT",
    "GENERATOR_VERSION",
    "LANGUAGE_SETS",
    "SIZES",
    "DemoWorld",
    "Group",
    "Person",
    "SizeSpec",
    "Work",
    "generate",
    "lexicon_truth",
]
