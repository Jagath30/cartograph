"""Step 10's runner and report, with a scripted model and no database.

Both modes are the real orchestrator: retrieval mode over a tree the real
JoinTree builds, full-schema mode over the stand-in and the full-schema
prompt. Every statement the "model" writes was written by hand. The
questions are of the frozen original set and hand-made; heldout.yaml is
not read.
"""

from dataclasses import dataclass
from pathlib import Path

import pytest
from tracing import GRAPH, SCHEMA_REF, SNAPSHOT, Located, Runs, located, reply

from app.core.eval_set import parse_eval_set
from app.core.eval_step10 import from_original, key_columns
from app.core.full_schema import KEYS_RULES, build_full_prompt, full_schema_located
from app.core.prompt_builder import JOIN_RULES
from app.core.sql_reading import schema_of
from app.orchestrator import Orchestrator
from app.run_step10 import Asked, keep_run, main, mode_order, provenance, refusal, run_once, void_name
from app.shell.embedder import EmbeddingFailed
from app.shell.model_client import FakeModel, LedgeredModel, ModelReply, SpendLedger
from app.shell.step10 import RetryingModel, create_exclusively, keep_identical
from app.step10_report import pilot_report, runs_report

ORIGINAL = {
    str(q.id): from_original(q)
    for q in parse_eval_set((Path(__file__).resolve().parents[1] / "eval" / "questions.yaml").read_text()).questions
}
Q1 = Asked("original", "1", ORIGINAL["1"].question, ORIGINAL["1"])
Q14 = Asked("original", "14", ORIGINAL["14"].question, ORIGINAL["14"])
Q15 = Asked("original", "15", ORIGINAL["15"].question, ORIGINAL["15"])
DEV = Asked("dev", "d1", "How many customers were born in each country?")

Q1_SQL = """SELECT s.s_store_name, SUM(ss.ss_net_paid) FROM store_sales ss JOIN store s ON ss.ss_store_sk = s.s_store_sk
JOIN date_dim d ON ss.ss_sold_date_sk = d.d_date_sk WHERE d.d_year = EXTRACT(YEAR FROM CURRENT_DATE) - 1
GROUP BY s.s_store_name"""
Q1_WRONG = "SELECT s.s_store_name FROM store s"
Q14_SQL = """SELECT i.i_item_sk FROM store_returns sr JOIN store_sales ss ON sr.sr_item_sk = ss.ss_item_sk
AND sr.sr_ticket_number = ss.ss_ticket_number JOIN promotion p ON ss.ss_promo_sk = p.p_promo_sk
JOIN item i ON ss.ss_item_sk = i.i_item_sk"""
SCHEMA, KEYS = schema_of(SNAPSHOT), key_columns(SNAPSHOT)


@dataclass
class _Pipeline:
    orchestrator: Orchestrator


def _setup(tmp_path, retrieval_script, full_script, where=None, executor=None, locate=None):
    ledger = SpendLedger(tmp_path / "ledger.jsonl")
    models = {
        "retrieval": RetryingModel(LedgeredModel(FakeModel(retrieval_script), ledger, "step10 test", 1.0),
                                   sleep=lambda s: None),
        "full_schema": RetryingModel(LedgeredModel(FakeModel(full_script), ledger, "step10 test", 1.0),
                                     sleep=lambda s: None),
    }  # fmt: skip
    where = where or located("store_sales", "store", "date_dim")
    retrieval = Orchestrator(SNAPSHOT, SCHEMA_REF, locate or (lambda q: where), models["retrieval"], executor or Runs())
    everything = full_schema_located(SNAPSHOT)
    full = Orchestrator(
        SNAPSHOT, SCHEMA_REF, lambda q: everything, models["full_schema"], executor or Runs(),
        build=lambda q, snap, tables, joins: build_full_prompt(q, snap, GRAPH),
    )  # fmt: skip
    return {"retrieval": _Pipeline(retrieval), "full_schema": _Pipeline(full)}, models, ledger


def _run(tmp_path, sets, retrieval_script, full_script, number=1, cap=0.30, worst=0.005, **setup):
    pipelines, models, ledger = _setup(tmp_path, retrieval_script, full_script, **setup)
    kept: dict[str, str] = {}
    result = run_once(number, sets, pipelines, models, ledger, cap, worst, SCHEMA, KEYS, kept.__setitem__)
    return result, kept, ledger


def test_odd_runs_put_retrieval_first_and_even_runs_full_schema_first() -> None:
    assert mode_order(1) == ("retrieval", "full_schema")
    assert mode_order(2) == ("full_schema", "retrieval")
    assert mode_order(5) == ("retrieval", "full_schema")


def test_every_question_is_answered_in_both_modes_in_order_and_scored(tmp_path) -> None:
    result, kept, _ = _run(tmp_path, [[Q1, Q14]], [reply(Q1_SQL), reply(Q14_SQL)], [reply(Q1_WRONG), reply(Q14_SQL)],
                           where=None)  # fmt: skip
    assert result.void is None
    got = [(r["id"], r["mode"], r["position"]) for r in result.records]
    assert got == [("1", "retrieval", 1), ("1", "full_schema", 2), ("14", "retrieval", 3), ("14", "full_schema", 4)]
    first, second = result.records[0], result.records[1]
    assert (first["score"]["tables"], first["score"]["path"]) == ("correct", "correct")
    assert (second["score"]["tables"], second["score"]["path"]) == ("wrong", "wrong")
    assert first["uses_current_date"] is True and second["uses_current_date"] is False
    assert first["excluded"] is None and result.records[2]["excluded"].startswith("deleted from scoring")
    assert first["execution"]["rows_sha256"] and first["execution"]["row_count"] == 2
    assert len(kept) == 4  # one prompt per question and mode: the question is in each
    assert all(text.startswith("You write one PostgreSQL query") for text in kept.values())


def test_retrieval_records_its_tree_and_recall_and_full_schema_records_neither(tmp_path) -> None:
    result, kept, _ = _run(tmp_path, [[Q1]], [reply(Q1_SQL)], [reply(Q1_SQL)])
    retrieval, full = result.records
    assert KEYS_RULES in kept[full["prompt"]["sha256"]] and KEYS_RULES not in kept[retrieval["prompt"]["sha256"]]
    assert JOIN_RULES in kept[retrieval["prompt"]["sha256"]] and JOIN_RULES not in kept[full["prompt"]["sha256"]]
    assert retrieval["located"]["tree_tables"] == ["store_sales", "store", "date_dim"]
    assert retrieval["recall"]["tree"] == {"matched": 3, "expected": 3, "count": 3}
    assert retrieval["conformance_to_selected"] in ("conforms", "incomplete", "diverged", "not_checked")
    assert full["located"] is None and full["recall"] is None and full["conformance_to_selected"] is None
    assert len(full["prompt"]["tables"]) == 24 and full["prompt"]["has_join_section"] is False


def test_a_decline_is_recorded_and_scored(tmp_path) -> None:
    decline = reply(status="not_answerable")
    result, _, _ = _run(tmp_path, [[Q15, Q1]], [decline, decline], [decline, reply(Q1_SQL)])
    q15_r, q15_f, q1_r, q1_f = result.records
    assert q15_r["score"]["why"] == q15_f["score"]["why"] == "declined as expected"
    assert (q1_r["score"]["tables"], q1_r["score"]["why"]) == ("wrong", "declined")
    assert q1_f["score"]["tables"] == "correct"


def test_a_question_retrieval_declines_without_a_model_call_shows_nothing(tmp_path) -> None:
    some = located("store")
    nowhere = Located(some.tree, some.explanation, True, "anchors_not_connected")
    result, _, _ = _run(tmp_path, [[Q1]], [], [reply(Q1_SQL)], where=nowhere)
    retrieval = result.records[0]
    assert retrieval["attempts"] == [] and retrieval["prompt"] is None
    assert retrieval["score"]["why"] == "declined"
    assert retrieval["recall"]["shown"] == {"matched": 0, "expected": 3, "count": 0}


def test_a_development_question_is_recorded_and_not_scored(tmp_path) -> None:
    sql = "SELECT c.c_birth_country, COUNT(*) FROM customer c GROUP BY c.c_birth_country"
    result, _, _ = _run(tmp_path, [[DEV]], [reply(sql)], [reply(sql)], where=located("customer"))
    assert [r["score"] for r in result.records] == [None, None]
    assert result.records[0]["extraction"]["tables"] == ["customer"]


def test_the_cap_is_checked_before_every_answer_and_stops_the_run_before_a_call(tmp_path) -> None:
    pipelines, models, ledger = _setup(tmp_path, [reply(Q1_SQL)], [reply(Q1_SQL)])
    ledger.record("step10 earlier", ModelReply("m", "m", None, "{}", 1, 0, 1, 0.299, 1))
    result = run_once(1, [[Q1]], pipelines, models, ledger, 0.30, 0.005, SCHEMA, KEYS, lambda d, t: None)
    assert result.records == [] and result.void.startswith("the step cap")
    assert "would pass $0.3;" in result.void


def test_the_cap_counts_only_step10_purposes(tmp_path) -> None:
    pipelines, models, ledger = _setup(tmp_path, [reply(Q1_SQL)], [reply(Q1_SQL)])
    # Over the step cap, under the model's own ceiling of 1.0.
    ledger.record("api", ModelReply("m", "m", None, "{}", 1, 0, 1, 0.5, 1))
    result = run_once(1, [[Q1]], pipelines, models, ledger, 0.30, 0.005, SCHEMA, KEYS, lambda d, t: None)
    assert result.void is None and len(result.records) == 2
    assert result.records[0]["cap_check"] == {"spent_usd": 0.0, "worst_case_usd": 0.005, "cap_usd": 0.30}


def test_the_cap_stops_a_run_part_way(tmp_path) -> None:
    paid = ModelReply("m", "m", None, '{"status": "sql", "sql": "SELECT s.s_store_name FROM store s"}', 1, 0, 1, 0.1, 1)
    result, _, _ = _run(tmp_path, [[Q1, Q1]], [paid, paid], [paid, paid], cap=0.205, worst=0.01)
    assert len(result.records) == 2 and result.void.startswith("the step cap")


def _transport() -> ModelReply:
    return ModelReply("m", None, None, None, 0, 0, 0, 0.0, 1, "transport", "the request did not complete")


def test_a_transport_failure_retried_away_does_not_void_the_run_and_is_recorded(tmp_path) -> None:
    result, _, _ = _run(tmp_path, [[Q1]], [_transport(), reply(Q1_SQL)], [reply(Q1_SQL)])
    assert result.void is None
    assert [e["failure"] for e in result.records[0]["transport_retries"]] == ["transport"]
    assert result.records[1]["transport_retries"] == []


def test_a_transport_failure_that_outlasts_the_retries_voids_the_run(tmp_path) -> None:
    result, _, _ = _run(tmp_path, [[Q1, Q15]], [_transport()] * 3, [reply(Q1_SQL)])
    assert result.void == "transport failure after retries"
    assert len(result.records) == 1 and len(result.records[0]["transport_retries"]) == 2


def test_the_models_ceiling_voids_the_run(tmp_path) -> None:
    ceiling = ModelReply("m", None, None, None, 0, 0, 0, 0.0, 0, "ceiling", "the ceiling")
    result, _, _ = _run(tmp_path, [[Q1]], [ceiling], [])
    assert result.void == "the model's spending ceiling"


def test_a_failure_to_embed_voids_the_run(tmp_path) -> None:
    def fails(question):
        raise EmbeddingFailed("the embedding provider answered 500 (other)")

    result, _, _ = _run(tmp_path, [[Q1]], [], [], locate=fails)
    assert result.void.startswith("embedding failed") and result.records == []


def test_a_cut_off_reply_is_the_methods_and_is_scored_wrong_not_voided(tmp_path) -> None:
    cut = ModelReply("m", "m", None, None, 10, 0, 800, 0.001, 1, "cut_off", "cut off")
    result, _, _ = _run(tmp_path, [[Q1]], [cut], [reply(Q1_SQL)])
    assert result.void is None
    assert (result.records[0]["outcome"], result.records[0]["score"]["tables"]) == ("model_failed", "wrong")


# --------------------------------------------------------------------------
# The report
# --------------------------------------------------------------------------


def _document(records, number, void=None, file=None, ended="2026-10-18T10:00:00+00:00"):
    """As `_execute` writes a run file: no field it does not write."""
    name = file or (f"run-{number:02d}-void-1.json" if void else f"run-{number:02d}.json")
    return {"label": "run", "run": number, "file": name, "void": void, "began": ended, "ended": ended,
            "head": "e4c7a215b71d5786e804a09118d197b02165352a", "manifest": "matches", "records": records,
            "settings": {"model": "m", "reasoning_effort": "none", "temperature": 0.0, "alpha": 0.5, "anchor_cut": 0.5,
                         "anchor_cap": 5, "margin": 0.1, "schema_sha256": "a" * 64, "preferences_hash": "p",
                         "cap_usd": 0.30},
            "spend": {"step10_usd_after": 0.01}}  # fmt: skip


def test_the_report_counts_each_run_and_combines_them(tmp_path) -> None:
    runs = []
    for number, full_sql in ((1, Q1_WRONG), (2, Q1_SQL)):
        result, _, _ = _run(tmp_path, [[Q1, Q14]], [reply(Q1_SQL), reply(Q14_SQL)], [reply(full_sql), reply(Q14_SQL)],
                            number=number)  # fmt: skip
        runs.append(_document(result.records, number))
    text = runs_report(runs, [_document([], 2, void="transport failure after retries", file="run-02-void-1.json")])
    assert "complete runs 2; void runs 1" in text
    assert "void, run-02-void-1.json (run 2): transport failure after retries" in text
    assert "THE HELD-OUT SET (12 questions): the headline" in text
    original = text.split("THE ORIGINAL SET", 1)[1]
    retrieval, full = original.split("  full_schema\n", 1)
    assert "tables correctness: mean 100.0%, range 100.0% to 100.0%, over 2 runs" in retrieval
    assert "run 1: 1 of 1" in retrieval  # question 14 is not counted
    assert "tables correctness: mean 50.0%, range 0.0% to 100.0%, over 2 runs" in full
    assert "SQL taking today's date (item 60): 2" in retrieval
    assert "tables: retrieval better on 1 (1); full schema better on 0 (none); the same on 0" in text
    assert "original 1: retrieval tables 2/2; retrieval path 2/2; retrieval distinct SQL 1; full_schema tables 1/2 UNSTABLE" in text
    assert "original 14 (EXCLUDED)" in text
    assert "QUESTION 14, deleted from scoring (item 54)" in text and "run 1 original 14 retrieval: answered" in text


def test_the_pilot_report_says_nothing_is_scored_and_shows_the_cap_checks(tmp_path) -> None:
    sql = "SELECT c.c_birth_country, COUNT(*) FROM customer c GROUP BY c.c_birth_country"
    result, _, _ = _run(tmp_path, [[DEV]], [reply(sql)], [reply(sql)], where=located("customer"))
    text = pilot_report([_document(result.records, 1)], [])
    assert "nothing is scored" in text
    assert "cap checked before each of 2 answers" in text
    assert "d1 full_schema (position 2): answered" in text and "24 tables" in text


@pytest.mark.parametrize("number", [1, 2])
def test_a_report_of_no_runs_still_reads(number) -> None:
    assert "complete runs 0" in runs_report([], [])


def test_an_execution_failure_keeps_its_word_and_state_and_not_the_databases_message(tmp_path) -> None:
    """The database's own text can quote a value from the data (item 78,
    DR-15): it is not kept. A fixed sentence of the system's is."""
    result, _, _ = _run(tmp_path, [[Q1]], [reply(Q1_SQL)], [reply(Q1_SQL)], executor=Runs(failure="timeout"))
    record = result.records[0]
    assert record["outcome"] == "execution_failed" and record["message"] is None
    assert (record["execution"]["failure"], record["execution"]["sqlstate"]) == ("timeout", "22012")
    declined, _, _ = _run(tmp_path, [[Q1]], [reply(status="not_answerable")], [reply(Q1_SQL)])
    assert declined.records[0]["message"].startswith("This cannot be answered from the tables retrieved")


def test_the_pilot_report_shows_a_cross_join(tmp_path) -> None:
    crossed = "SELECT COUNT(*) FROM store_sales ss CROSS JOIN store s"
    result, _, _ = _run(tmp_path, [[DEV]], [reply(crossed)], [reply(crossed)], where=located("store_sales", "store"))
    text = pilot_report([_document(result.records, 1)], [])
    assert "cross join   store_sales | store" in text


# --------------------------------------------------------------------------
# Nothing is ever overwritten (the owner's ruling 3 at stop 2)
# --------------------------------------------------------------------------


def test_a_file_is_created_once_and_never_overwritten(tmp_path) -> None:
    path = tmp_path / "run-01.json"
    create_exclusively(path, "one")
    with pytest.raises(FileExistsError):
        create_exclusively(path, "two")
    assert path.read_text() == "one"


def test_a_prompt_already_kept_is_left_alone_and_one_that_differs_stops(tmp_path) -> None:
    path = tmp_path / "abc.txt"
    keep_identical(path, "the prompt")
    keep_identical(path, "the prompt")
    with pytest.raises(FileExistsError):
        keep_identical(path, "another prompt")
    assert path.read_text() == "the prompt"


@pytest.mark.parametrize("name", ["pilot-01.json", "pilot-03-void.json", "report.txt"])
def test_the_runner_stops_before_any_paid_call_when_a_file_it_writes_exists(tmp_path, monkeypatch, name) -> None:
    from app.shell import pipeline_session

    out = tmp_path / "pilot"
    out.mkdir()
    (out / name).write_text("earlier")

    def never(*arguments, **keywords):
        raise AssertionError("a pipeline was opened")

    monkeypatch.setattr(pipeline_session, "open_pipeline", never)
    with pytest.raises(SystemExit) as stopped:
        main(["--pilot", "--out", str(out)])
    assert "nothing is overwritten" in str(stopped.value) and name in str(stopped.value)
    assert (out / name).read_text() == "earlier"


def test_the_manifest_is_never_overwritten(tmp_path, monkeypatch) -> None:
    import app.run_step10 as runner

    monkeypatch.setattr(runner, "STEP10", tmp_path)
    monkeypatch.setattr(runner, "MANIFEST", tmp_path / "frozen_code.json")
    (tmp_path / "frozen_code.json").write_text("earlier")
    with pytest.raises(SystemExit) as stopped:
        main(["--write-manifest"])
    assert "nothing is overwritten" in str(stopped.value)
    assert (tmp_path / "frozen_code.json").read_text() == "earlier"


def test_the_report_is_never_overwritten(tmp_path) -> None:
    from app.step10_report import write_report

    (tmp_path / "report.txt").write_text("earlier")
    with pytest.raises(FileExistsError):
        write_report(tmp_path, "pilot")
    assert (tmp_path / "report.txt").read_text() == "earlier"


def test_a_run_file_is_never_overwritten(tmp_path) -> None:
    path = tmp_path / "run-01.json"
    keep_run(path, {"run": 1})
    with pytest.raises(FileExistsError):
        keep_run(path, {"run": 2})
    assert '"run": 1' in path.read_text()


# --------------------------------------------------------------------------
# The owner's rulings at the review of stop 2: tokens per mode, execution
# outcomes beside the score, identical rows, and each run's provenance
# --------------------------------------------------------------------------


def _one_run(tmp_path, retrieval_script, full_script, **setup):
    result, _, _ = _run(tmp_path, [[Q1, Q14, Q15]], retrieval_script, full_script, **setup)
    return _document(result.records, 1)


def _counted(text: str, tokens_in: int, tokens_out: int) -> ModelReply:
    return ModelReply("m", "m", None, text, tokens_in, 0, tokens_out, 0.0001, 1)


def test_the_report_gives_tokens_per_set_and_mode_and_no_dollars_there(tmp_path) -> None:
    run = _one_run(
        tmp_path,
        [_counted(reply(Q1_SQL), 1000, 50), _counted(reply(Q14_SQL), 1300, 70), _counted(reply(status="not_answerable"), 900, 9)],
        [_counted(reply(Q1_SQL), 8000, 60), _counted(reply(Q14_SQL), 8100, 80), _counted(reply(status="not_answerable"), 7900, 9)],
    )  # fmt: skip
    text = runs_report([run], [])
    original = text.split("THE ORIGINAL SET", 1)[1].split("QUESTION 14", 1)[0]
    for mode in ("retrieval", "full_schema"):
        records = [r for r in run["records"] if r["mode"] == mode]
        tin = sum(a["tokens_in"] for r in records for a in r["attempts"])
        tout = sum(a["tokens_out"] for r in records for a in r["attempts"])
        assert tin > 0 and tout > 0
        line = (f"    tokens, every answer of question 14 included: {tin} in, {tout} out, over 3 answers; "
                f"mean per answer {tin / 3:.1f} in, {tout / 3:.1f} out")  # fmt: skip
        blocks = original.split("  retrieval\n", 1)[1].split("  full_schema\n", 1)
        assert line in blocks[0 if mode == "retrieval" else 1]
    head, spend = text.split("SPEND", 1)
    assert "$" not in head


def test_the_report_counts_execution_outcomes_beside_the_score_by_word(tmp_path) -> None:
    run = _one_run(tmp_path, [reply(Q1_SQL), reply(Q14_SQL), reply(status="not_answerable")],
                   [reply(Q1_SQL), reply(Q14_SQL), reply(status="not_answerable")],
                   executor=Runs(failure="timeout"))  # fmt: skip
    text = runs_report([run], [])
    assert text.count("    execution outcomes, beside the score: not executed 1, timeout 1") == 2


def _rows(record, digest, truncated=False):
    record["execution"] = {**record["execution"], "rows_sha256": digest, "truncated": truncated}


def test_identical_rows_compare_the_two_modes_where_both_ran_whole(tmp_path) -> None:
    cases = (("same", False, True), ("same", False, True), ("other", False, True), ("same", True, True),
             ("same", False, False))  # fmt: skip
    runs = []
    for number, (full_digest, truncated, ok) in enumerate(cases, start=1):
        result, _, _ = _run(tmp_path, [[Q1]], [reply(Q1_SQL)], [reply(Q1_SQL)], number=number)
        retrieval, full = sorted(result.records, key=lambda r: r["mode"] != "retrieval")
        _rows(retrieval, "same")
        _rows(full, full_digest, truncated)
        if not ok:
            full["execution"].update(ok=False, failure="timeout", rows_sha256=None)
        runs.append(_document(result.records, number))
    text = runs_report(runs, [])
    assert ("  identical rows, outside the score: whether the two modes returned the same rows. This compares "
            "values returned, not their correctness; a different column order counts as different.") in text
    assert "    where both modes executed ok and neither result was truncated: equal in 2 of 3 question-runs" in text
    assert "    original 1: equal in 2 of 3 runs" in text


def test_identical_rows_say_so_when_nothing_could_be_compared(tmp_path) -> None:
    run = _one_run(tmp_path, [reply(status="not_answerable")] * 3, [reply(status="not_answerable")] * 3)
    text = runs_report([run], [])
    assert "    where both modes executed ok and neither result was truncated: equal in 0 of 0 question-runs" in text


def test_the_pilot_report_gives_tokens_per_mode_and_no_dollars_per_mode(tmp_path) -> None:
    sql = "SELECT c.c_birth_country, COUNT(*) FROM customer c GROUP BY c.c_birth_country"
    result, _, _ = _run(tmp_path, [[DEV]], [reply(sql)], [reply(sql)], where=located("customer"))
    text = pilot_report([_document(result.records, 1)], [])
    tail = text.split("SPEND", 1)[1].split("\n")[3:]
    assert any(line.startswith("    full_schema: 1 answers; ") and "tokens in" in line for line in tail)
    assert not any("$" in line for line in tail)
    assert not any("$" in line for line in text.split("\n") if line.startswith("    attempt"))
    assert "identical rows" in text


@pytest.mark.parametrize("head", [None, "abc", "G" * 40])
def test_the_evaluation_needs_the_head_commit_before_anything_is_opened(tmp_path, monkeypatch, head) -> None:
    from app.shell import pipeline_session

    monkeypatch.setattr(pipeline_session, "open_pipeline", lambda *a, **k: pytest.fail("a pipeline was opened"))
    argv = ["--run", "1", "--out", str(tmp_path)] + ([] if head is None else ["--head", head])
    with pytest.raises(SystemExit) as stopped:
        main(argv)
    assert "--head" in str(stopped.value) or stopped.value.code == 2


def test_each_run_file_records_its_head_and_the_manifest_check() -> None:
    sha = "ab30b4a" + "0" * 33
    assert provenance(sha, []) == {"head": sha, "manifest": "matches"}
    assert provenance(None, None) == {"head": None, "manifest": "no manifest"}
    assert provenance(sha, ["app/x.py changed"]) == {"head": sha, "manifest": "differs: app/x.py changed"}


# --------------------------------------------------------------------------
# One run per command (the owner's ruling 1 after stop 2's review)
# --------------------------------------------------------------------------

SHA = "e4c7a215b71d5786e804a09118d197b02165352a"


def _keep(out: Path, name: str, void: str | None = None, run: int = 1) -> None:
    out.mkdir(parents=True, exist_ok=True)
    keep_run(out / name, {**_document([], run, void=void), "void": void})


def test_run_1_may_start_in_an_empty_folder(tmp_path) -> None:
    assert refusal(tmp_path / "runs", 1) is None


@pytest.mark.parametrize("number", [0, 6, -1])
def test_only_runs_1_to_5_exist(tmp_path, number) -> None:
    assert "1 to 5" in refusal(tmp_path, number)


def test_a_run_needs_every_run_before_it_complete(tmp_path) -> None:
    assert "run 1 has no complete file" in refusal(tmp_path, 2)
    _keep(tmp_path, "run-01-void-1.json", void="transport failure after retries")
    assert "run 1 has no complete file" in refusal(tmp_path, 2)
    _keep(tmp_path, "run-01.json")
    assert refusal(tmp_path, 2) is None
    assert "run 2 has no complete file" in refusal(tmp_path, 3)


def test_a_run_that_is_complete_is_never_run_again(tmp_path) -> None:
    _keep(tmp_path, "run-01.json")
    assert "run 1 is complete" in refusal(tmp_path, 1)


def test_no_run_starts_while_a_later_run_has_a_file(tmp_path) -> None:
    _keep(tmp_path, "run-01.json")
    _keep(tmp_path, "run-03-void-1.json", void="transport failure after retries", run=3)
    assert "run 3 already has files" in refusal(tmp_path, 2)


def test_a_void_run_is_replaced_by_its_own_number_until_three_in_a_row(tmp_path) -> None:
    _keep(tmp_path, "run-01-void-1.json", void="transport failure after retries")
    _keep(tmp_path, "run-01-void-2.json", void="transport failure after retries")
    assert refusal(tmp_path, 1) is None
    _keep(tmp_path, "run-01-void-3.json", void="embedding failed: x")
    assert "three void runs in a row" in refusal(tmp_path, 1)


def test_a_run_stopped_by_the_step_cap_stops_the_step(tmp_path) -> None:
    _keep(tmp_path, "run-01.json")
    _keep(tmp_path, "run-02-void-1.json", void="the step cap: $0.3 spent", run=2)
    assert "the step cap" in refusal(tmp_path, 2)


def test_no_run_starts_once_the_report_exists(tmp_path) -> None:
    tmp_path.mkdir(exist_ok=True)
    (tmp_path / "report.txt").write_text("done")
    assert "report" in refusal(tmp_path, 1)


def test_a_void_file_never_takes_a_name_already_used(tmp_path) -> None:
    assert void_name(tmp_path, 3) == "run-03-void-1.json"
    _keep(tmp_path, "run-03-void-1.json", void="x", run=3)
    assert void_name(tmp_path, 3) == "run-03-void-2.json"
    _keep(tmp_path, "run-03-void-2.json", void="x", run=3)
    assert void_name(tmp_path, 3) == "run-03-void-3.json"


def _no_pipeline(monkeypatch, differences=()):
    import app.run_step10 as runner
    from app.shell import pipeline_session

    monkeypatch.setattr(pipeline_session, "open_pipeline", lambda *a, **k: pytest.fail("a pipeline was opened"))
    monkeypatch.setattr(runner, "_manifest_differences", lambda: list(differences))


def test_a_run_refused_by_its_folder_opens_nothing(tmp_path, monkeypatch) -> None:
    _no_pipeline(monkeypatch)
    with pytest.raises(SystemExit) as stopped:
        main(["--run", "2", "--head", SHA, "--out", str(tmp_path)])
    assert "run 1 has no complete file" in str(stopped.value)


def test_a_run_refused_by_the_manifest_opens_nothing(tmp_path, monkeypatch) -> None:
    _no_pipeline(monkeypatch, differences=["app/orchestrator.py changed"])
    with pytest.raises(SystemExit) as stopped:
        main(["--run", "1", "--head", SHA, "--out", str(tmp_path)])
    assert "app/orchestrator.py changed" in str(stopped.value)


def test_there_is_one_way_to_run_the_evaluation(tmp_path) -> None:
    with pytest.raises(SystemExit) as stopped:
        main(["--runs", "5", "--head", SHA, "--out", str(tmp_path)])
    assert stopped.value.code == 2


def test_the_report_needs_five_complete_runs_and_pays_for_nothing(tmp_path, monkeypatch) -> None:
    _no_pipeline(monkeypatch)
    for number in range(1, 5):
        _keep(tmp_path, f"run-0{number}.json", run=number)
    with pytest.raises(SystemExit) as stopped:
        main(["--report", "--out", str(tmp_path)])
    assert "run 5 has no complete file" in str(stopped.value)
    assert not (tmp_path / "report.txt").exists()

    _keep(tmp_path, "run-05.json", run=5)
    assert main(["--report", "--out", str(tmp_path)]) == 0
    assert "complete runs 5; void runs 0" in (tmp_path / "report.txt").read_text()

    with pytest.raises(SystemExit) as stopped:
        main(["--report", "--out", str(tmp_path)])
    assert "report" in str(stopped.value)


def test_the_spend_line_reads_the_run_that_ended_last(tmp_path) -> None:
    first = {**_document([], 1, ended="2026-10-18T10:00:00+00:00"), "spend": {"step10_usd_after": 0.02}}
    later = {**_document([], 2, ended="2026-10-18T11:00:00+00:00"), "spend": {"step10_usd_after": 0.05}}
    void = {**_document([], 3, void="transport failure after retries", ended="2026-10-18T10:30:00+00:00"),
            "spend": {"step10_usd_after": 0.04}}  # fmt: skip
    text = runs_report([later, first], [void])
    assert "the ledger on step10 purposes after the last run: $0.050000" in text


def _fake_open_pipeline(tmp_path, scripts):
    """open_pipeline as the runner calls it, over the scripted model and the
    stand-in executor: the same orchestrators the tests above build."""
    from types import SimpleNamespace

    ledger = SpendLedger(tmp_path / "ledger.jsonl")
    where = located("store_sales", "store", "date_dim")
    everything = full_schema_located(SNAPSHOT)

    def open_pipeline(name, purpose, mode, wrap_model):
        model = wrap_model(LedgeredModel(FakeModel(list(scripts[mode])), ledger, purpose, 1.0))
        if mode == "retrieval":
            orchestrator = Orchestrator(SNAPSHOT, SCHEMA_REF, lambda q: where, model, Runs())
        else:
            orchestrator = Orchestrator(SNAPSHOT, SCHEMA_REF, lambda q: everything, model, Runs(),
                                        build=lambda q, snap, tables, joins: build_full_prompt(q, snap, GRAPH))  # fmt: skip
        retrieval = SimpleNamespace(settings=SimpleNamespace(alpha=0.5, anchor_cut=0.5, anchor_cap=5, margin=0.1),
                                    stored=SimpleNamespace(hash="h" * 64))  # fmt: skip
        return SimpleNamespace(orchestrator=orchestrator, snapshot=SNAPSHOT, graph=GRAPH, ledger=ledger,
                               retrieval=retrieval)  # fmt: skip

    return open_pipeline


def _placeholder_settings(monkeypatch, runner) -> None:
    """The runner reads its settings; CI has none in its environment. As in
    test_model_client: placeholder URLs, and no key, so nothing can connect
    or be paid for."""
    from app.config import Settings

    settings = Settings(
        app_database_url="postgresql://x/app", warehouse_database_url="postgresql://x/wh", redis_url="redis://x",
        openai_api_key=None,
    )  # fmt: skip
    monkeypatch.setattr(runner, "get_settings", lambda: settings)


def _drive(tmp_path, monkeypatch, scripts, number=1):
    import app.run_step10 as runner
    from app.shell import pipeline_session

    monkeypatch.setattr(pipeline_session, "open_pipeline", _fake_open_pipeline(tmp_path, scripts))
    monkeypatch.setattr(runner, "_manifest_differences", lambda: [])
    monkeypatch.setattr(runner, "evaluation_sets", lambda: [[Q1]])
    monkeypatch.setattr(runner, "RetryingModel", lambda model: RetryingModel(model, sleep=lambda seconds: None))
    _placeholder_settings(monkeypatch, runner)
    return main(["--run", str(number), "--head", SHA, "--out", str(tmp_path / "runs")])


def test_a_run_is_made_end_to_end_and_kept_in_its_own_file(tmp_path, monkeypatch) -> None:
    import json

    assert _drive(tmp_path, monkeypatch, {"retrieval": [reply(Q1_SQL)], "full_schema": [reply(Q1_SQL)]}) == 0
    kept = json.loads((tmp_path / "runs" / "run-01.json").read_text())
    assert (kept["run"], kept["file"], kept["void"], kept["head"], kept["manifest"]) == (
        1, "run-01.json", None, SHA, "matches",
    )  # fmt: skip
    assert [r["mode"] for r in kept["records"]] == ["retrieval", "full_schema"]
    assert kept["settings"]["cap_usd"] == 0.30 and kept["records"][0]["score"]["tables"] == "correct"
    assert len(list((tmp_path / "runs" / "prompts").iterdir())) == 2
    with pytest.raises(SystemExit) as stopped:
        _drive(tmp_path, monkeypatch, {"retrieval": [reply(Q1_SQL)], "full_schema": [reply(Q1_SQL)]})
    assert "run 1 is complete" in str(stopped.value)


def test_run_2_puts_full_schema_first(tmp_path, monkeypatch) -> None:
    import json

    scripts = {"retrieval": [reply(Q1_SQL)], "full_schema": [reply(Q1_SQL)]}
    assert _drive(tmp_path, monkeypatch, scripts, number=1) == 0
    assert _drive(tmp_path, monkeypatch, scripts, number=2) == 0
    kept = json.loads((tmp_path / "runs" / "run-02.json").read_text())
    assert [r["mode"] for r in kept["records"]] == ["full_schema", "retrieval"]


def test_a_void_run_is_kept_under_its_own_name_and_replaced_by_the_same_number(tmp_path, monkeypatch, capsys) -> None:
    import json

    lost = {"retrieval": [_transport()] * 3, "full_schema": []}
    assert _drive(tmp_path, monkeypatch, lost) == 1
    assert "replaced by running --run 1 again" in capsys.readouterr().out
    assert _drive(tmp_path, monkeypatch, lost) == 1
    void = json.loads((tmp_path / "runs" / "run-01-void-2.json").read_text())
    assert void["void"] == "transport failure after retries" and void["file"] == "run-01-void-2.json"
    assert _drive(tmp_path, monkeypatch, lost) == 1
    assert "The step stops" in capsys.readouterr().out
    with pytest.raises(SystemExit) as stopped:
        _drive(tmp_path, monkeypatch, {"retrieval": [reply(Q1_SQL)], "full_schema": [reply(Q1_SQL)]})
    assert "three void runs in a row" in str(stopped.value)
