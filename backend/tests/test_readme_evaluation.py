"""The README's Evaluation section stays byte for byte as it was written at
step 10b (step 13, the owner's ruling 6): both evaluations, the headline
and the first, kept beneath it.

The section runs from its heading to the line before the next `## `
heading. The README is outside backend/, so this runs where the whole
repository is checked out (GitHub's CI) and skips where only backend/ is
mounted (the local CI command); the close checks it by hand as well.
"""

import hashlib
from pathlib import Path

import pytest

README = Path(__file__).resolve().parents[2] / "README.md"

# sha256 of the section as committed at step 10b's close, read at step 13.
EVALUATION_SHA256 = "03851d51dd980070571e110df89910c730dc3c51c1567a14ea3bd477f99dda9f"


def evaluation_section(text: str) -> str:
    start = text.index("## Evaluation\n")
    end = text.index("\n## ", start)
    return text[start : end + 1]


def test_the_section_is_read_from_its_heading_to_the_next() -> None:
    text = "# T\n\n## Evaluation\n\nbody\n\n### kept\n\nmore\n\n## Next\n"
    assert evaluation_section(text) == "## Evaluation\n\nbody\n\n### kept\n\nmore\n\n"


def test_the_evaluation_section_is_unchanged() -> None:
    if not README.exists():
        pytest.skip("README.md is not mounted here; GitHub's CI checks it")
    section = evaluation_section(README.read_text(encoding="utf-8"))
    assert hashlib.sha256(section.encode("utf-8")).hexdigest() == EVALUATION_SHA256
