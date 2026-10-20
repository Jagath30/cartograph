"""The guard that keeps every test off the real spend ledger (step 10b,
the owner's ruling at stop C1): it notices any change to the file."""

from ledger_guard import ledger_fingerprint


def test_no_ledger_file_is_none(tmp_path) -> None:
    assert ledger_fingerprint(tmp_path / "model_calls.jsonl") is None


def test_a_line_appended_changes_the_fingerprint(tmp_path) -> None:
    ledger = tmp_path / "model_calls.jsonl"
    ledger.write_text('{"purpose": "a"}\n')
    before = ledger_fingerprint(ledger)
    assert before == ledger_fingerprint(ledger)
    with ledger.open("a") as appending:
        appending.write('{"purpose": "b"}\n')
    assert ledger_fingerprint(ledger) != before


def test_a_line_rewritten_in_place_changes_the_fingerprint(tmp_path) -> None:
    ledger = tmp_path / "model_calls.jsonl"
    ledger.write_text('{"cost_usd": 0.1}\n')
    before = ledger_fingerprint(ledger)
    ledger.write_text('{"cost_usd": 0.2}\n')
    assert ledger_fingerprint(ledger) != before
