"""Guard: copyleft libraries must not come back as declared runtime dependencies (D-053, D-054).

PyMuPDF is AGPL-3.0; python-Levenshtein and Levenshtein are GPL-2.0-or-later. They were
replaced by pypdfium2 (Apache-2.0 OR BSD-3-Clause) and rapidfuzz (MIT). This only reads
pyproject.toml; it does not scan installed packages or transitive dependencies, so it is a
tripwire for the two known cases, not a license audit.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

PYPROJECT = Path(__file__).resolve().parents[2] / "pyproject.toml"

# Normalised distribution names (lower case, runs of - _ . collapsed to "-").
FORBIDDEN = {
    "pymupdf": "AGPL-3.0; use pypdfium2",
    "pymupdf4llm": "AGPL-3.0 (depends on PyMuPDF)",
    "python-levenshtein": "GPL-2.0-or-later; use rapidfuzz",
    "levenshtein": "GPL-2.0-or-later; use rapidfuzz",
}


def _normalise(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _requirement_name(requirement: str) -> str:
    return _normalise(re.split(r"[<>=!~;\[\s(]", requirement.strip(), maxsplit=1)[0])


def _runtime_names() -> set[str]:
    project = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]
    return {_requirement_name(item) for item in project.get("dependencies", [])}


@pytest.mark.unit
def test_no_forbidden_copyleft_package_is_a_declared_runtime_dependency():
    found = {name: why for name, why in FORBIDDEN.items() if name in _runtime_names()}
    assert not found, f"copyleft runtime dependency declared: {found}"


@pytest.mark.unit
def test_the_replacements_are_declared():
    names = _runtime_names()
    assert {"pypdfium2", "rapidfuzz"} <= names
