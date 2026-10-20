"""What the suite-wide guard in conftest.py compares, before and after
each test: the real spend ledger's size and sha256."""

import hashlib
from pathlib import Path


def ledger_fingerprint(path: Path) -> tuple[int, str] | None:
    """None when there is no ledger file; otherwise its size and sha256."""
    if not path.exists():
        return None
    data = path.read_bytes()
    return len(data), hashlib.sha256(data).hexdigest()
