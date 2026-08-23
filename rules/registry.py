"""Rulebook version registry.

Network rules change on published effective dates. A dispute must be evaluated under the
rulebook that was in force when it was raised, not whichever version happens to be newest
when AEGIS runs. That is an evidentiary requirement as much as a correctness one: a packet
exported today for a dispute raised last quarter has to state which rulebook it was judged
under, and be reproducible under that rulebook later.

Adding a future rule version means appending to the tables below. No model retrains.
"""

from __future__ import annotations

from datetime import date
from types import ModuleType
from typing import Any

from rules.ce3 import v2026_04 as _ce3_2026_04
from rules.vamp import v2026_04 as _vamp_2026_04

# Ordered oldest-first. Each entry: (effective_from, module).
CE3_VERSIONS: list[tuple[date, ModuleType]] = [
    (_ce3_2026_04.EFFECTIVE_FROM, _ce3_2026_04),
]

VAMP_VERSIONS: list[tuple[date, ModuleType]] = [
    (_vamp_2026_04.EFFECTIVE_FROM, _vamp_2026_04),
]


def _resolve(versions: list[tuple[date, ModuleType]], as_of: date | None) -> ModuleType:
    if as_of is None:
        return versions[-1][1]
    applicable = [m for eff, m in versions if eff <= as_of]
    # A dispute predating every encoded version falls back to the earliest we have, with the
    # version string on the result making that explicit rather than silent.
    return applicable[-1] if applicable else versions[0][1]


def ce3(as_of: date | None = None) -> ModuleType:
    """The CE 3.0 rulebook in force on `as_of` (default: latest)."""
    return _resolve(CE3_VERSIONS, as_of)


def vamp(as_of: date | None = None) -> ModuleType:
    """The VAMP rulebook in force on `as_of` (default: latest)."""
    return _resolve(VAMP_VERSIONS, as_of)


def manifest() -> dict[str, Any]:
    """Every rulebook version AEGIS can apply. Rendered in the console and the packet."""
    return {
        "ce3": [
            {"version": m.RULE_VERSION, "effective_from": eff.isoformat()}
            for eff, m in CE3_VERSIONS
        ],
        "vamp": [
            {"version": m.RULE_VERSION, "effective_from": eff.isoformat()}
            for eff, m in VAMP_VERSIONS
        ],
        "active": {
            "ce3": CE3_VERSIONS[-1][1].RULE_VERSION,
            "vamp": VAMP_VERSIONS[-1][1].RULE_VERSION,
        },
    }
