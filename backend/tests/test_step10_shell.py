"""Step 10's shell pieces: the transport retry, the rows hash, the
relative-date mark, the cap's headroom, the code manifest and the held-out
loader's hash check. No paid call; the held-out file is never read here
(the loader is shown refusing a file whose hash is not the pinned one, and
reading a hand-made one only when told its hash).
"""

import datetime as dt
import hashlib
import json
from decimal import Decimal

import pytest

from app.config import ModelPrice
from app.shell.model_client import FakeModel, Message, ModelReply, ReplyShape
from app.shell.step10 import (
    HELDOUT_SHA256,
    HeldOutChanged,
    RetryingModel,
    check_manifest,
    code_manifest,
    load_heldout,
    rows_sha256,
    uses_current_date,
    worst_case_usd,
)

ASK = (Message("user", "x"),)
SHAPE = ReplyShape("s", {"type": "object"})


def _failed(failure: str) -> ModelReply:
    return ModelReply("m", None, None, None, 0, 0, 0, 0.0, 5, failure, "a fixed sentence")


OK = ModelReply("m", "m", None, "{}", 10, 0, 5, 0.001, 12)


# --------------------------------------------------------------------------
# Transport and provider failures: retried twice, with backoff
# --------------------------------------------------------------------------


@pytest.mark.parametrize("failure", ["transport", "provider"])
def test_a_transport_or_provider_failure_is_retried_with_backoff(failure) -> None:
    slept: list[float] = []
    model = RetryingModel(FakeModel([_failed(failure), _failed(failure), OK]), sleep=slept.append)
    assert model.complete(ASK, SHAPE) is OK
    assert slept == [5.0, 20.0]
    assert [event["failure"] for event in model.events] == [failure, failure]


def test_after_two_retries_the_failure_is_returned_for_the_run_to_be_voided() -> None:
    slept: list[float] = []
    fake = FakeModel([_failed("transport")] * 3)
    reply = RetryingModel(fake, sleep=slept.append).complete(ASK, SHAPE)
    assert reply.failure == "transport" and len(fake.calls) == 3 and slept == [5.0, 20.0]


@pytest.mark.parametrize("failure", ["cut_off", "shape", "refused", "ceiling"])
def test_the_methods_own_failures_are_never_retried_here(failure) -> None:
    fake = FakeModel([_failed(failure)])
    model = RetryingModel(fake, sleep=lambda seconds: pytest.fail("slept"))
    assert model.complete(ASK, SHAPE).failure == failure and len(fake.calls) == 1 and model.events == []


def test_a_good_reply_passes_straight_through() -> None:
    fake = FakeModel([OK])
    model = RetryingModel(fake, sleep=lambda seconds: pytest.fail("slept"))
    assert model.complete(ASK, SHAPE) is OK and model.model == fake.model


def test_the_events_are_taken_and_cleared_question_by_question() -> None:
    model = RetryingModel(FakeModel([_failed("provider"), OK, OK]), sleep=lambda seconds: None)
    model.complete(ASK, SHAPE)
    assert len(model.take_events()) == 1
    model.complete(ASK, SHAPE)
    assert model.take_events() == []


# --------------------------------------------------------------------------
# The rows hash
# --------------------------------------------------------------------------


def test_the_rows_hash_does_not_depend_on_row_order() -> None:
    a = ((1, "x"), (2, "y"))
    assert rows_sha256(a) == rows_sha256(tuple(reversed(a)))
    assert rows_sha256(a) != rows_sha256(((1, "x"), (2, "z")))


def test_the_rows_hash_is_over_a_canonical_text_of_the_values() -> None:
    rows = ((Decimal("1.50"), dt.date(2002, 1, 2), None, 2.5, True),)
    text = json.dumps(["1.50", "2002-01-02", None, "2.5", True], ensure_ascii=True, separators=(",", ":"))
    assert rows_sha256(rows) == hashlib.sha256(text.encode()).hexdigest()


def test_no_rows_has_a_hash_too() -> None:
    assert rows_sha256(()) == hashlib.sha256(b"").hexdigest()


def test_a_decimal_keeps_its_scale() -> None:
    assert rows_sha256(((Decimal("1.5"),),)) != rows_sha256(((Decimal("1.50"),),))


# --------------------------------------------------------------------------
# Item 60: relative dates
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "sql",
    ["SELECT 1 WHERE d_year = EXTRACT(YEAR FROM CURRENT_DATE) - 1", "select now()", "SELECT current_timestamp",
     "select NOW ( )"],  # fmt: skip
)
def test_a_relative_date_is_seen(sql) -> None:
    assert uses_current_date(sql)


@pytest.mark.parametrize("sql", ["SELECT d.d_current_year FROM date_dim d", "SELECT 1 AS nowhere", None])
def test_a_column_named_like_one_is_not(sql) -> None:
    assert not uses_current_date(sql)


# --------------------------------------------------------------------------
# The cap's headroom
# --------------------------------------------------------------------------

PRICE = ModelPrice(input=0.10, cached_input=0.01, output=0.50, as_of="2026-10-09")


def test_the_worst_case_is_three_uncached_attempts_each_at_the_output_cap() -> None:
    # 3,000 characters is 1,000 tokens at three characters a token. Each
    # later attempt resends what came before: the reply and the feedback.
    got = worst_case_usd(3000, PRICE, max_output_tokens=800)
    tokens_in = 1000 + (1000 + 1100) + (1000 + 2200)
    assert got == pytest.approx((tokens_in * 0.10 + 3 * 800 * 0.50) / 1_000_000)


def test_the_worst_case_grows_with_the_prompt() -> None:
    assert worst_case_usd(31_000, PRICE, 800) > worst_case_usd(8_000, PRICE, 800)


# --------------------------------------------------------------------------
# The code manifest: what the protocol froze
# --------------------------------------------------------------------------


def test_the_manifest_hashes_every_file_given_and_finds_a_change(tmp_path) -> None:
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "a.py").write_text("one")
    (tmp_path / "over.yaml").write_text("two")
    manifest = code_manifest(tmp_path, ("app/*.py", "over.yaml"))
    assert manifest == {
        "app/a.py": hashlib.sha256(b"one").hexdigest(),
        "over.yaml": hashlib.sha256(b"two").hexdigest(),
    }
    assert check_manifest(tmp_path, ("app/*.py", "over.yaml"), manifest) == []

    (tmp_path / "app" / "a.py").write_text("changed")
    (tmp_path / "app" / "b.py").write_text("new")
    (tmp_path / "over.yaml").unlink()
    assert check_manifest(tmp_path, ("app/*.py", "over.yaml"), manifest) == [
        "app/a.py changed", "app/b.py added", "over.yaml removed",
    ]  # fmt: skip


def test_the_manifest_never_names_the_held_out_set() -> None:
    from app.shell.step10 import FROZEN

    assert not any("heldout" in pattern for pattern in FROZEN)


# --------------------------------------------------------------------------
# The held-out loader
# --------------------------------------------------------------------------


def test_the_pinned_hash_is_the_one_the_freeze_recorded() -> None:
    assert HELDOUT_SHA256 == "14e1441e5c9717c519c00410ad27c1bf9b78caf07184141064dffc2377f806fe"


def test_a_file_that_is_not_the_frozen_one_is_refused_before_it_is_parsed(tmp_path) -> None:
    path = tmp_path / "heldout.yaml"
    path.write_text("not yaml: [")
    with pytest.raises(HeldOutChanged):
        load_heldout(path)


def test_a_file_of_the_pinned_hash_is_parsed(tmp_path) -> None:
    text = (
        "provenance: made here\nquestions:\n"
        "  - id: H1\n    question: How many items?\n    tables: [item]\n    joins: []\n"
    )
    path = tmp_path / "heldout.yaml"
    path.write_text(text)
    got = load_heldout(path, sha256=hashlib.sha256(text.encode()).hexdigest())
    assert [q.id for q in got.questions] == ["H1"]
