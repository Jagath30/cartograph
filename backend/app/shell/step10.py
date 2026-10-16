"""Step 10's shell pieces, for the runner (`app.run_step10`).

    RetryingModel    a transport or provider failure is not the method's
                     behaviour (the owner's ruling 5 at stop 1): the call
                     is tried again up to twice, with backoff. If it still
                     fails, the reply goes back failed and the runner voids
                     the run. Nothing else is retried here: what the
                     orchestrator retries, it retries itself.
    rows_sha256      a hash over the rows sorted into a canonical order.
                     No value is kept (DR-15). A capped result's hash
                     covers only the rows returned; the truncated flag is
                     kept beside it.
    uses_current_date  item 60: whether the SQL takes today's date.
    worst_case_usd   what one question could cost at most, for the cap's
                     check before each question.
    code_manifest    the sha256 of every file of the code that answers and
                     measures, written when the protocol is committed and
                     checked before every run of the frozen sets.
    load_heldout     the only reader of eval/heldout.yaml: it refuses the
                     file unless its sha256 is the one frozen.
"""

import hashlib
import json
import re
import time
from collections.abc import Callable
from datetime import date, datetime
from datetime import time as clock
from decimal import Decimal
from pathlib import Path

from app.config import ModelPrice
from app.core.heldout import HeldOutSet, parse_heldout
from app.shell.model_client import Message, ModelClient, ModelReply, ReplyShape

# --------------------------------------------------------------------------
# Transport and provider failures
# --------------------------------------------------------------------------

RETRIED = ("transport", "provider")
BACKOFF_SECONDS = (5.0, 20.0)


class RetryingModel:
    """Wraps the ledgered model. Each failed call is on the ledger, at the
    cost the provider reported (none, for a failure)."""

    def __init__(self, inner: ModelClient, sleep: Callable[[float], None] = time.sleep) -> None:
        self._inner = inner
        self.model = inner.model
        self._sleep = sleep
        self.events: list[dict] = []

    def complete(self, messages: tuple[Message, ...], shape: ReplyShape) -> ModelReply:
        reply = self._inner.complete(messages, shape)
        for wait in BACKOFF_SECONDS:
            if reply.failure not in RETRIED:
                return reply
            self.events.append({"failure": reply.failure, "message": reply.message, "waited_s": wait})
            self._sleep(wait)
            reply = self._inner.complete(messages, shape)
        return reply

    def take_events(self) -> list[dict]:
        events, self.events = self.events, []
        return events


# --------------------------------------------------------------------------
# The rows hash
# --------------------------------------------------------------------------


def _canonical(value):
    if value is None or isinstance(value, bool | int | str):
        return value
    if isinstance(value, Decimal | float):
        return str(value)
    if isinstance(value, datetime | date | clock):
        return value.isoformat()
    if isinstance(value, bytes | bytearray | memoryview):
        return bytes(value).hex()
    return str(value)


def rows_sha256(rows) -> str:
    """Each row as compact JSON of canonical values; the lines sorted, joined
    by newlines, hashed."""
    lines = sorted(
        json.dumps([_canonical(value) for value in row], ensure_ascii=True, separators=(",", ":")) for row in rows
    )
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()


# --------------------------------------------------------------------------
# Item 60
# --------------------------------------------------------------------------

_TODAY = re.compile(r"\bcurrent_date\b|\bcurrent_timestamp\b|\bnow\s*\(", re.IGNORECASE)


def uses_current_date(sql: str | None) -> bool:
    return bool(sql) and _TODAY.search(sql) is not None


# --------------------------------------------------------------------------
# The cap
# --------------------------------------------------------------------------

STEP_PREFIX = "step10"
STEP_CAP_USD = 0.30
# Characters to a token, on the low side: the prompts measured about 3.9.
CHARS_PER_TOKEN = 3
# What a retry sends back besides the reply: the validator's findings.
FEEDBACK_TOKENS = 300
ATTEMPTS = 3


def worst_case_usd(prompt_chars: int, price: ModelPrice, max_output_tokens: int) -> float:
    """Three attempts, none cached, each reply at the output cap; each later
    attempt resends the earlier replies and their feedback."""
    prompt = prompt_chars / CHARS_PER_TOKEN
    tokens_in = sum(prompt + k * (max_output_tokens + FEEDBACK_TOKENS) for k in range(ATTEMPTS))
    return (tokens_in * price.input + ATTEMPTS * max_output_tokens * price.output) / 1_000_000


# --------------------------------------------------------------------------
# The code manifest
# --------------------------------------------------------------------------

# Relative to backend/. The code that answers a question and the code that
# measures the answer, the overlays and the settings they read. Not
# eval/heldout.yaml: it has its own pinned hash, and is not opened before
# the protocol is committed.
FROZEN = (
    "app/*.py",
    "app/core/*.py",
    "app/shell/*.py",
    "overlays/*.yaml",
    "eval/calibration.json",
    "eval/questions.yaml",
    "requirements.txt",
)


def code_manifest(root: Path, patterns: tuple[str, ...] = FROZEN) -> dict[str, str]:
    files = sorted({path for pattern in patterns for path in root.glob(pattern) if path.is_file()})
    return {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest() for path in files}


def check_manifest(root: Path, patterns: tuple[str, ...], manifest: dict[str, str]) -> list[str]:
    """Every difference, in path order: changed, added or removed."""
    now = code_manifest(root, patterns)
    differences = []
    for path in sorted(set(now) | set(manifest)):
        if path not in manifest:
            differences.append(f"{path} added")
        elif path not in now:
            differences.append(f"{path} removed")
        elif now[path] != manifest[path]:
            differences.append(f"{path} changed")
    return differences


# --------------------------------------------------------------------------
# The held-out set
# --------------------------------------------------------------------------

# As pinned in tests/core/test_heldout.py when the set was frozen.
HELDOUT_SHA256 = "14e1441e5c9717c519c00410ad27c1bf9b78caf07184141064dffc2377f806fe"


class HeldOutChanged(RuntimeError):
    pass


def load_heldout(path: Path, sha256: str = HELDOUT_SHA256) -> HeldOutSet:
    """Read once, hashed, and parsed only if the hash is the frozen one."""
    data = path.read_bytes()
    found = hashlib.sha256(data).hexdigest()
    if found != sha256:
        raise HeldOutChanged(f"{path.name} is not the file that was frozen: sha256 {found[:12]}, not {sha256[:12]}")
    return parse_heldout(data.decode())
