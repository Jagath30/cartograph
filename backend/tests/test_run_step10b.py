"""Step 10b's runner and report, with scripted models and no database.

Three modes over the real orchestrator: retrieval over a tree the real
JoinTree builds, linked over a scripted linking call and the same tree
machinery, full schema over the stand-in. The sets' loaders are tested on
hand-made files; heldout2.yaml is never read here.
"""

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import pytest
from tracing import GRAPH, SCHEMA_REF, SNAPSHOT, Runs, located, reply

from app.config import ModelPrice
from app.core.eval_set import parse_eval_set
from app.core.eval_step10 import from_original, key_columns
from app.core.full_schema import build_full_prompt, full_schema_located
from app.core.linking import LINK_REPLY_NAME, build_link_prompt, link_reply_schema
from app.core.sql_reading import schema_of
from app.orchestrator import Linker, Orchestrator
from app.run_step10 import Asked
from app.run_step10b import (
    MODES,
    SETS,
    load_set,
    main,
    mode_order,
    record_b,
    run_once_b,
    void_cause_b,
    worst_case_b,
)
from app.shell.model_client import FakeModel, LedgeredModel, ModelReply, ReplyShape, SpendLedger
from app.shell.step10 import RetryingModel
from app.step10b_report import pilot_report_b, runs_report_b

ORIGINAL = {
    str(q.id): from_original(q)
    for q in parse_eval_set((Path(__file__).resolve().parents[1] / "eval" / "questions.yaml").read_text()).questions
}
Q1 = Asked("original", "1", ORIGINAL["1"].question, ORIGINAL["1"])
Q15 = Asked("original", "15", ORIGINAL["15"].question, ORIGINAL["15"])
DEV = Asked("dev", "d1", "How many customers were born in each country?")
Q1_SQL = """SELECT s.s_store_name, SUM(ss.ss_net_paid) FROM store_sales ss JOIN store s ON ss.ss_store_sk = s.s_store_sk
JOIN date_dim d ON ss.ss_sold_date_sk = d.d_date_sk WHERE d.d_year = 2002 GROUP BY s.s_store_name"""
SCHEMA, KEYS = schema_of(SNAPSHOT), key_columns(SNAPSHOT)
SHA = "e4c7a215b71d5786e804a09118d197b02165352a"


def tables(*names: str) -> str:
    return json.dumps({"tables": list(names)})


@dataclass
class _Pipeline:
    orchestrator: Orchestrator


def _setup(tmp_path, scripts, link_script=(), where=None):
    ledger = SpendLedger(tmp_path / "ledger.jsonl")

    def wrapped(script):
        return RetryingModel(LedgeredModel(FakeModel(list(script)), ledger, "step10b test", 1.0), sleep=lambda s: None)

    models = {mode: [wrapped(scripts.get(mode, ()))] for mode in MODES}
    link_model = wrapped(link_script)
    models["linked"].append(link_model)
    where = where or located("store_sales", "store", "date_dim")
    retrieval = Orchestrator(SNAPSHOT, SCHEMA_REF, lambda q: where, models["retrieval"][0], Runs())
    linker = Linker(
        prompt=lambda q: build_link_prompt(q, SNAPSHOT, GRAPH),
        shape=ReplyShape(LINK_REPLY_NAME, link_reply_schema(SNAPSHOT)),
        known=frozenset(t.name for t in SNAPSHOT.tables),
        model=link_model,
        locate=lambda q, named: located(*named),
    )
    linked = Orchestrator(SNAPSHOT, SCHEMA_REF, None, models["linked"][0], Runs(), linker=linker)
    everything = full_schema_located(SNAPSHOT)
    full = Orchestrator(SNAPSHOT, SCHEMA_REF, lambda q: everything, models["full_schema"][0], Runs(),
                        build=lambda q, snap, t, j: build_full_prompt(q, snap, GRAPH))  # fmt: skip
    pipelines = {"retrieval": _Pipeline(retrieval), "linked": _Pipeline(linked), "full_schema": _Pipeline(full)}
    return pipelines, models, ledger


def _worst(usd: float) -> dict[str, float]:
    return {mode: usd for mode in MODES}


def _run(tmp_path, sets, scripts, link_script=(), number=1, cap=0.75, worst=0.01, **setup):
    pipelines, models, ledger = _setup(tmp_path, scripts, link_script, **setup)
    kept: dict[str, str] = {}
    result = run_once_b(number, sets, pipelines, models, ledger, cap, _worst(worst), SCHEMA, KEYS, kept.__setitem__)
    return result, kept, ledger


# --------------------------------------------------------------------------
# Modes and sets
# --------------------------------------------------------------------------


def test_the_three_modes_rotate_with_the_run() -> None:
    assert MODES == ("retrieval", "linked", "full_schema")
    assert mode_order(1) == ("retrieval", "linked", "full_schema")
    assert mode_order(2) == ("linked", "full_schema", "retrieval")
    assert mode_order(3) == ("full_schema", "retrieval", "linked")
    assert mode_order(4) == mode_order(1) and mode_order(5) == mode_order(2)


def test_the_sets_and_their_pinned_hashes() -> None:
    assert list(SETS) == ["heldout2", "original", "heldout1", "dev2"]
    assert SETS["heldout2"][1] == "ffd28657f7fd1c44d519b84fc63f87e02e0f4504dadfe0393c99b275de20f3d1"
    assert SETS["dev2"][1] == "4d6dd232aa75181e79c0efa162bd1ea118c305e5d3899aed91654076046c13da"
    assert SETS["heldout1"][1] == "14e1441e5c9717c519c00410ad27c1bf9b78caf07184141064dffc2377f806fe"
    assert SETS["original"][1] == "a92f27c2f3fee9c1d178e21504eadc87aa9ec6a622e896561064e798d1100440"
    assert SETS["heldout2"][0].name == "heldout2.yaml" and SETS["dev2"][0].name == "dev2.yaml"


HAND_MADE = "provenance: made here\nquestions:\n  - id: H1\n    question: How many items?\n    tables: [item]\n    joins: []\n"


def test_a_held_out_format_set_is_read_only_at_its_pinned_hash_and_named_as_its_set(tmp_path) -> None:
    path = tmp_path / "set.yaml"
    path.write_text(HAND_MADE)
    with pytest.raises(Exception, match="not the file that was frozen"):
        load_set("dev2", path=path, sha="0" * 64)
    asked = load_set("dev2", path=path, sha=hashlib.sha256(HAND_MADE.encode()).hexdigest())
    assert [(a.set, a.id) for a in asked] == [("dev2", "H1")]
    assert asked[0].expectation.set == "dev2" and asked[0].expectation.tables == ("item",)


def test_the_original_set_is_read_only_at_its_pinned_hash(tmp_path) -> None:
    path = tmp_path / "questions.yaml"
    path.write_text((Path(__file__).resolve().parents[1] / "eval" / "questions.yaml").read_text() + "\n# edited\n")
    with pytest.raises(Exception, match="not the file that was frozen"):
        load_set("original", path=path)
    asked = load_set("original")
    assert len(asked) == 16 and asked[13].expectation.excluded


# --------------------------------------------------------------------------
# One run
# --------------------------------------------------------------------------


def test_every_question_is_answered_in_three_modes_in_the_runs_order(tmp_path) -> None:
    scripts = {"retrieval": [reply(Q1_SQL)], "linked": [reply(Q1_SQL)], "full_schema": [reply(Q1_SQL)]}
    result, kept, _ = _run(tmp_path, [[Q1]], scripts, [tables("store_sales", "store", "date_dim")], number=2)
    assert result.void is None
    assert [r["mode"] for r in result.records] == ["linked", "full_schema", "retrieval"]
    assert all(r["score"]["tables"] == "correct" for r in result.records)
    # One linking prompt and the generation prompts. Retrieval's and linked's
    # are the same text here, over the same tree, and are kept by hash.
    assert len(kept) == 3


def test_a_linked_record_holds_the_linking_the_tree_and_three_recalls(tmp_path) -> None:
    linking_reply = ModelReply("m", "m", None, tables("store_sales", "store", "store_returns"), 7900, 0, 14, 0.0008, 5)
    scripts = {"retrieval": [reply(Q1_SQL)], "linked": [reply(Q1_SQL)], "full_schema": [reply(Q1_SQL)]}
    result, _, _ = _run(tmp_path, [[Q1]], scripts, [linking_reply])
    linked = next(r for r in result.records if r["mode"] == "linked")
    assert linked["linking"]["tables"] == ["store_sales", "store", "store_returns"]
    assert [a["outcome"] for a in linked["linking"]["attempts"]] == ["accepted"]
    assert linked["linking"]["attempts"][0]["tokens_in"] == 7900 and linked["linking"]["tokens_in"] == 7900
    assert linked["located"]["linked"] == ["store_sales", "store", "store_returns"]
    assert linked["recall"]["named"] == {"matched": 2, "expected": 3, "count": 3}
    assert linked["recall"]["tree"]["expected"] == 3 and linked["recall"]["shown"]["expected"] == 3
    assert linked["expected"] == {"tables": list(ORIGINAL["1"].tables), "tables_one_of": [], "decline": False}
    assert linked["conformance_to_selected"] in ("conforms", "incomplete", "diverged", "not_checked")
    retrieval = next(r for r in result.records if r["mode"] == "retrieval")
    assert retrieval["linking"] is None and "named" not in retrieval["recall"]
    full = next(r for r in result.records if r["mode"] == "full_schema")
    assert full["linking"] is None and full["recall"] is None


def test_a_decline_at_linking_is_recorded_by_its_stage(tmp_path) -> None:
    scripts = {"retrieval": [reply(status="not_answerable")], "full_schema": [reply(status="not_answerable")]}
    result, _, _ = _run(tmp_path, [[Q15]], scripts, [tables()])
    linked = next(r for r in result.records if r["mode"] == "linked")
    assert linked["outcome"] == "not_answerable" and linked["declined_at"] == "linking"
    assert linked["score"]["why"] == "declined as expected"
    assert linked["recall"] is None
    retrieval = next(r for r in result.records if r["mode"] == "retrieval")
    assert retrieval["declined_at"] == "model"
    full = next(r for r in result.records if r["mode"] == "full_schema")
    assert full["declined_at"] == "model"


def test_a_decline_at_locate_is_recorded_by_its_stage(tmp_path) -> None:
    # store and household_demographics are not joined within the bound.
    apart = located("store", "household_demographics", max_joins=1)
    assert apart.declined
    result, _, _ = _run(tmp_path, [[Q15]], {"full_schema": [reply(status="not_answerable")]}, [tables()], where=apart)
    retrieval = next(r for r in result.records if r["mode"] == "retrieval")
    assert retrieval["outcome"] == "not_answerable" and retrieval["declined_at"] == "locate"
    assert retrieval["attempts"] == []


def test_an_answer_has_no_stage_of_decline(tmp_path) -> None:
    scripts = {m: [reply(Q1_SQL)] for m in ("retrieval", "linked", "full_schema")}
    result, _, _ = _run(tmp_path, [[Q1]], scripts, [tables("store_sales", "store", "date_dim")])
    assert [r["declined_at"] for r in result.records] == [None, None, None]


def test_the_cap_is_checked_before_every_answer(tmp_path) -> None:
    pipelines, models, ledger = _setup(tmp_path, {})
    ledger.record("step10b earlier", ModelReply("m", "m", None, "{}", 1, 0, 1, 0.745, 1))
    result = run_once_b(1, [[Q1]], pipelines, models, ledger, 0.75, _worst(0.01), SCHEMA, KEYS, lambda d, t: None)
    assert result.records == [] and result.void.startswith("the step cap")


def test_the_cap_counts_only_step10b_purposes(tmp_path) -> None:
    scripts = {m: [reply(Q1_SQL)] for m in ("retrieval", "linked", "full_schema")}
    pipelines, models, ledger = _setup(tmp_path, scripts, [tables("store_sales", "store", "date_dim")])
    # Over the step cap, under the model's own ceiling of 1.0.
    ledger.record("step10 run 1", ModelReply("m", "m", None, "{}", 1, 0, 1, 0.5, 1))
    result = run_once_b(1, [[Q1]], pipelines, models, ledger, 0.25, _worst(0.01), SCHEMA, KEYS, lambda d, t: None)
    assert result.void is None and len(result.records) == 3
    assert result.records[0]["cap_check"] == {"spent_usd": 0.0, "worst_case_usd": 0.01, "cap_usd": 0.25}


def _transport() -> ModelReply:
    return ModelReply("m", None, None, None, 0, 0, 0, 0.0, 1, "transport", "the request did not complete")


def test_a_transport_failure_of_the_linking_call_voids_the_run(tmp_path) -> None:
    scripts = {"retrieval": [reply(Q1_SQL)]}
    result, _, _ = _run(tmp_path, [[Q1]], scripts, [_transport()] * 3)
    assert result.void == "transport failure after retries"
    linked = result.records[-1]
    assert linked["mode"] == "linked" and len(linked["transport_retries"]) == 2


def test_a_retried_away_failure_of_either_model_is_recorded_against_its_answer(tmp_path) -> None:
    scripts = {"retrieval": [reply(Q1_SQL)], "linked": [_transport(), reply(Q1_SQL)], "full_schema": [reply(Q1_SQL)]}
    result, _, _ = _run(tmp_path, [[Q1]], scripts, [_transport(), tables("store_sales", "store", "date_dim")])
    assert result.void is None
    linked = next(r for r in result.records if r["mode"] == "linked")
    assert len(linked["transport_retries"]) == 2
    assert all(r["transport_retries"] == [] for r in result.records if r["mode"] != "linked")


def test_the_void_rule_reads_the_stage_that_ended_the_question(tmp_path) -> None:
    from types import SimpleNamespace

    def failed(failure):
        return SimpleNamespace(reply=SimpleNamespace(failure=failure))

    assert void_cause_b(SimpleNamespace(attempts=(), linking=SimpleNamespace(attempts=(failed("transport"),)))) == (
        "transport failure after retries"
    )
    assert void_cause_b(SimpleNamespace(attempts=(), linking=SimpleNamespace(attempts=(failed("ceiling"),)))) == (
        "the model's spending ceiling"
    )
    assert void_cause_b(SimpleNamespace(attempts=(), linking=SimpleNamespace(attempts=(failed("cut_off"),)))) is None
    assert void_cause_b(SimpleNamespace(attempts=(failed("provider"),), linking=None)) == "provider failure after retries"
    assert void_cause_b(SimpleNamespace(attempts=(), linking=None)) is None


def test_the_worst_case_of_a_linked_answer_is_linking_and_generation_together() -> None:
    price = ModelPrice(input=0.10, cached_input=0.01, output=0.50, as_of="2026-10-09")
    from app.shell.step10 import worst_case_usd

    got = worst_case_b(31_000, 31_000, price, 800, 400)
    assert got == pytest.approx(worst_case_usd(31_000, price, 400) + worst_case_usd(31_000, price, 800))
    assert got > worst_case_usd(31_000, price, 800)


# --------------------------------------------------------------------------
# The command: what is read, and when
# --------------------------------------------------------------------------


def test_the_pilot_never_reads_the_fresh_held_out_set(tmp_path, monkeypatch) -> None:
    import app.run_step10b as runner

    read: list[str] = []
    real = runner.load_set

    def watch(name, **keywords):
        read.append(name)
        if name == "heldout2":
            raise AssertionError("heldout2 was read by the pilot")
        return real(name, **keywords)

    monkeypatch.setattr(runner, "load_set", watch)
    sets = runner.pilot_sets()
    assert read == ["dev2"]
    assert [a.set for a in sets[0]] == ["dev2"] * 12 and [a.id for a in sets[1]][:2] == ["d1", "d2"]


def test_a_run_needs_the_head_and_a_matching_manifest_before_anything_is_opened(tmp_path, monkeypatch) -> None:
    import app.run_step10b as runner
    from app.shell import pipeline_session

    monkeypatch.setattr(pipeline_session, "open_pipeline", lambda *a, **k: pytest.fail("a pipeline was opened"))
    monkeypatch.setattr(runner, "evaluation_sets", lambda: pytest.fail("a set was read"))
    with pytest.raises(SystemExit) as stopped:
        main(["--run", "1", "--out", str(tmp_path)])
    assert "--head" in str(stopped.value)
    monkeypatch.setattr(runner, "_manifest_differences", lambda: ["app/orchestrator.py changed"])
    with pytest.raises(SystemExit) as stopped:
        main(["--run", "1", "--head", SHA, "--out", str(tmp_path)])
    assert "app/orchestrator.py changed" in str(stopped.value)
    monkeypatch.setattr(runner, "_manifest_differences", lambda: None)
    with pytest.raises(SystemExit) as stopped:
        main(["--run", "1", "--head", SHA, "--out", str(tmp_path)])
    assert "no manifest" in str(stopped.value)


def test_a_run_refused_by_its_folder_reads_no_set(tmp_path, monkeypatch) -> None:
    import app.run_step10b as runner

    monkeypatch.setattr(runner, "_manifest_differences", lambda: [])
    monkeypatch.setattr(runner, "evaluation_sets", lambda: pytest.fail("a set was read"))
    with pytest.raises(SystemExit) as stopped:
        main(["--run", "2", "--head", SHA, "--out", str(tmp_path)])
    assert "run 1 has no complete file" in str(stopped.value)


def test_the_manifest_never_names_either_new_set() -> None:
    from app.run_step10b import FROZEN_B

    assert not any("heldout2" in p or "dev2" in p for p in FROZEN_B)
    assert "app/*.py" in FROZEN_B and "app/core/*.py" in FROZEN_B


# --------------------------------------------------------------------------
# The report
# --------------------------------------------------------------------------


def _document(records, number, void=None):
    return {"label": "run", "run": number, "file": f"run-{number:02d}.json", "void": void,
            "began": "2026-10-18T10:00:00+00:00", "ended": f"2026-10-18T1{number}:00:00+00:00",
            "head": SHA, "manifest": "matches", "records": records,
            "settings": {"model": "m", "reasoning_effort": "none", "temperature": 0.0, "alpha": 0.5,
                         "anchor_cut": 0.5, "anchor_cap": 5, "margin": 0.1, "schema_sha256": "a" * 64,
                         "preferences_hash": "p", "cap_usd": 0.75, "link_max_output_tokens": 400},
            "spend": {"step10b_usd_after": 0.02}}  # fmt: skip


def _heldout2(record):
    return {**record, "set": "heldout2"}


def test_the_report_leads_with_heldout2_and_its_two_comparisons_in_order(tmp_path) -> None:
    runs = []
    for number in (1, 2):
        scripts = {"retrieval": [reply(status="not_answerable")], "linked": [reply(Q1_SQL)], "full_schema": [reply(Q1_SQL)]}
        result, _, _ = _run(tmp_path, [[Q1]], scripts, [tables("store_sales", "store", "date_dim")], number=number)
        runs.append(_document([_heldout2(r) for r in result.records], number))
    text = runs_report_b(runs, [])
    head = text.split("THE ORIGINAL SET", 1)[0]
    assert head.index("THE FRESH HELD-OUT SET") < head.index("  linked\n")
    first = head.index("linked against full schema")
    second = head.index("linked against retrieval")
    assert first < second
    assert "    tables: linked better on 0 (none); full schema better on 0 (none); the same on 1" in head
    assert "    tables: linked better on 1 (1); retrieval better on 0 (none); the same on 0" in head
    assert "declines by stage: at linking 0; at locate 0; by the model 2" in head  # retrieval's two declines
    # Summed over the runs: linking may name other tables in another run.
    assert "    tables named by linking: recall 6 of 6 (100.0%), precision 6 of 6 (100.0%), over 2 runs" in head
    assert "linking, apart:" in head


def test_the_pilot_report_sets_the_named_tables_beside_the_tree(tmp_path) -> None:
    sql = "SELECT c.c_birth_country, COUNT(*) FROM customer c GROUP BY c.c_birth_country"
    scripts = {"retrieval": [reply(sql)], "linked": [reply(sql)], "full_schema": [reply(sql)]}
    result, _, _ = _run(tmp_path, [[DEV]], scripts, [tables("customer")], where=located("customer"))
    text = pilot_report_b([_document(result.records, 1)], [])
    assert "d1 linked: named customer | tree customer | answered" in text
    assert "$" not in text.split("SPEND", 1)[0].replace("$0.75", "")


def test_the_pilot_report_scores_dev2_and_not_the_development_questions(tmp_path) -> None:
    dev2 = Asked("dev2", "H1", Q1.question, ORIGINAL["1"])
    scripts = {m: [reply(Q1_SQL), reply("SELECT COUNT(*) FROM customer")] for m in ("retrieval", "linked", "full_schema")}
    pipelines, models, ledger = _setup(tmp_path, scripts, [tables("store_sales", "store", "date_dim"), tables("customer")])
    result = run_once_b(1, [[dev2], [DEV]], pipelines, models, ledger, 0.75, _worst(0.01), SCHEMA, KEYS, lambda d, t: None)
    assert [r["score"] is None for r in result.records] == [False] * 3 + [True] * 3
    text = pilot_report_b([_document(result.records, 1)], [])
    assert "DEVELOPMENT SET 2 (dev2), scored" in text
    assert "    tables correctness: mean 100.0%" in text
    assert "H1 linked: named store_sales, store, date_dim | tree " in text
    assert "d1 linked: named customer | tree " in text


# --------------------------------------------------------------------------
# The command end to end, over fake pipelines
# --------------------------------------------------------------------------


def _fake_open_pipeline(tmp_path, scripts, link_script):
    """open_pipeline as the runner calls it: each mode's orchestrator as
    _setup builds it, every model it makes handed to wrap_model, the
    generation model first."""
    from types import SimpleNamespace

    ledger = SpendLedger(tmp_path / "ledger.jsonl")
    where = located("store_sales", "store", "date_dim")
    everything = full_schema_located(SNAPSHOT)

    def open_pipeline(name, purpose, mode, wrap_model):
        model = wrap_model(LedgeredModel(FakeModel(list(scripts[mode])), ledger, purpose, 1.0))
        if mode == "retrieval":
            orchestrator = Orchestrator(SNAPSHOT, SCHEMA_REF, lambda q: where, model, Runs())
        elif mode == "linked":
            link_model = wrap_model(LedgeredModel(FakeModel(list(link_script)), ledger, purpose, 1.0))
            linker = Linker(
                prompt=lambda q: build_link_prompt(q, SNAPSHOT, GRAPH),
                shape=ReplyShape(LINK_REPLY_NAME, link_reply_schema(SNAPSHOT)),
                known=frozenset(t.name for t in SNAPSHOT.tables),
                model=link_model,
                locate=lambda q, named: located(*named),
            )
            orchestrator = Orchestrator(SNAPSHOT, SCHEMA_REF, None, model, Runs(), linker=linker)
        else:
            orchestrator = Orchestrator(SNAPSHOT, SCHEMA_REF, lambda q: everything, model, Runs(),
                                        build=lambda q, snap, t, j: build_full_prompt(q, snap, GRAPH))  # fmt: skip
        retrieval = SimpleNamespace(settings=SimpleNamespace(alpha=0.5, anchor_cut=0.5, anchor_cap=5, margin=0.1),
                                    stored=SimpleNamespace(hash="h" * 64))  # fmt: skip
        return SimpleNamespace(orchestrator=orchestrator, snapshot=SNAPSHOT, graph=GRAPH, ledger=ledger,
                               retrieval=retrieval)  # fmt: skip

    return open_pipeline


ANSWERS = {m: [reply(Q1_SQL)] for m in ("retrieval", "linked", "full_schema")}
LINKS = [tables("store_sales", "store", "date_dim")]


def _drive(tmp_path, monkeypatch, scripts, link_script, number=1):
    import app.run_step10b as runner
    from app.shell import pipeline_session

    monkeypatch.setattr(pipeline_session, "open_pipeline", _fake_open_pipeline(tmp_path, scripts, link_script))
    monkeypatch.setattr(runner, "_manifest_differences", lambda: [])
    monkeypatch.setattr(runner, "evaluation_sets", lambda: [[Q1]])
    monkeypatch.setattr(runner, "RetryingModel", lambda model: RetryingModel(model, sleep=lambda seconds: None))
    return main(["--run", str(number), "--head", SHA, "--out", str(tmp_path / "runs")])


def test_a_run_is_made_end_to_end_and_kept_in_its_own_file(tmp_path, monkeypatch) -> None:
    assert _drive(tmp_path, monkeypatch, ANSWERS, LINKS) == 0
    kept = json.loads((tmp_path / "runs" / "run-01.json").read_text())
    assert (kept["run"], kept["file"], kept["void"], kept["head"], kept["manifest"]) == (
        1, "run-01.json", None, SHA, "matches",
    )  # fmt: skip
    assert [r["mode"] for r in kept["records"]] == ["retrieval", "linked", "full_schema"]
    assert kept["settings"]["cap_usd"] == 0.75 and kept["settings"]["link_max_output_tokens"] == 400
    assert set(kept["settings"]["worst_case_per_answer_usd"]) == set(MODES)
    worst = kept["settings"]["worst_case_per_answer_usd"]
    assert worst["linked"] > worst["full_schema"] == worst["retrieval"]
    assert [r["cap_check"]["worst_case_usd"] for r in kept["records"]] == [worst[m] for m in MODES]
    assert "step10b_usd_after" in kept["spend"]
    # The linking prompt, and the generation prompts: retrieval's and
    # linked's are the same text over the same tree, kept once.
    assert len(list((tmp_path / "runs" / "prompts").iterdir())) == 3
    with pytest.raises(SystemExit) as stopped:
        _drive(tmp_path, monkeypatch, ANSWERS, LINKS)
    assert "run 1 is complete" in str(stopped.value)


def test_run_2_and_run_3_rotate_the_modes(tmp_path, monkeypatch) -> None:
    for number in (1, 2, 3):
        assert _drive(tmp_path, monkeypatch, ANSWERS, LINKS, number=number) == 0
    second = json.loads((tmp_path / "runs" / "run-02.json").read_text())
    third = json.loads((tmp_path / "runs" / "run-03.json").read_text())
    assert [r["mode"] for r in second["records"]] == ["linked", "full_schema", "retrieval"]
    assert [r["mode"] for r in third["records"]] == ["full_schema", "retrieval", "linked"]


def test_a_void_run_is_kept_under_its_own_name_and_replaced_by_the_same_number(tmp_path, monkeypatch, capsys) -> None:
    lost = [_transport()] * 3
    assert _drive(tmp_path, monkeypatch, ANSWERS, lost) == 1
    assert "replaced by running --run 1 again" in capsys.readouterr().out
    assert _drive(tmp_path, monkeypatch, ANSWERS, lost) == 1
    void = json.loads((tmp_path / "runs" / "run-01-void-2.json").read_text())
    assert void["void"] == "transport failure after retries" and void["file"] == "run-01-void-2.json"
    assert _drive(tmp_path, monkeypatch, ANSWERS, lost) == 1
    assert "The step stops" in capsys.readouterr().out
    with pytest.raises(SystemExit) as stopped:
        _drive(tmp_path, monkeypatch, ANSWERS, LINKS)
    assert "three void runs in a row" in str(stopped.value)


@pytest.mark.parametrize("name", ["pilot-01.json", "pilot-01-void.json", "report.txt"])
def test_the_pilot_stops_before_anything_is_opened_when_a_file_it_writes_exists(tmp_path, monkeypatch, name) -> None:
    import app.run_step10b as runner
    from app.shell import pipeline_session

    out = tmp_path / "pilot"
    out.mkdir()
    (out / name).write_text("earlier")
    monkeypatch.setattr(pipeline_session, "open_pipeline", lambda *a, **k: pytest.fail("a pipeline was opened"))
    monkeypatch.setattr(runner, "load_set", lambda *a, **k: pytest.fail("a set was read"))
    with pytest.raises(SystemExit) as stopped:
        main(["--pilot", "--out", str(out)])
    assert "nothing is overwritten" in str(stopped.value) and name in str(stopped.value)
    assert (out / name).read_text() == "earlier"


def test_the_pilot_is_made_end_to_end_with_its_report(tmp_path, monkeypatch) -> None:
    import app.run_step10b as runner
    from app.shell import pipeline_session

    scripts = {m: [reply(Q1_SQL)] * 2 for m in ("retrieval", "linked", "full_schema")}
    monkeypatch.setattr(pipeline_session, "open_pipeline", _fake_open_pipeline(tmp_path, scripts, LINKS * 2))
    monkeypatch.setattr(runner, "pilot_sets", lambda: [[Asked("dev2", "H1", Q1.question, ORIGINAL["1"])], [DEV]])
    monkeypatch.setattr(runner, "RetryingModel", lambda model: RetryingModel(model, sleep=lambda seconds: None))
    assert main(["--pilot", "--out", str(tmp_path / "pilot")]) == 0
    kept = json.loads((tmp_path / "pilot" / "pilot-01.json").read_text())
    assert len(kept["records"]) == 6 and kept["void"] is None
    assert (tmp_path / "pilot" / "report.txt").read_text().startswith("STEP 10B: THE PILOT")


def test_the_manifest_is_its_own_and_never_overwritten(tmp_path, monkeypatch) -> None:
    import app.run_step10b as runner

    assert runner.MANIFEST.parent.name == "step10b" and runner.MANIFEST.name == "frozen_code.json"
    monkeypatch.setattr(runner, "STEP10B", tmp_path)
    monkeypatch.setattr(runner, "MANIFEST", tmp_path / "frozen_code.json")
    (tmp_path / "frozen_code.json").write_text("earlier")
    with pytest.raises(SystemExit) as stopped:
        main(["--write-manifest"])
    assert "nothing is overwritten" in str(stopped.value)
    assert (tmp_path / "frozen_code.json").read_text() == "earlier"


def test_the_report_needs_five_complete_runs_and_is_never_overwritten(tmp_path, monkeypatch) -> None:
    from app.shell import pipeline_session
    from app.step10b_report import write_report_b

    monkeypatch.setattr(pipeline_session, "open_pipeline", lambda *a, **k: pytest.fail("a pipeline was opened"))
    for number in range(1, 5):
        (tmp_path / f"run-{number:02d}.json").write_text(json.dumps(_document([], number)))
    with pytest.raises(SystemExit) as stopped:
        main(["--report", "--out", str(tmp_path)])
    assert "run 5 has no complete file" in str(stopped.value)
    (tmp_path / "run-05.json").write_text(json.dumps(_document([], 5)))
    assert main(["--report", "--out", str(tmp_path)]) == 0
    assert (tmp_path / "report.txt").read_text().startswith("STEP 10B: THE EVALUATION")
    with pytest.raises(FileExistsError):
        write_report_b(tmp_path, "run")


def test_the_report_gives_tokens_per_set_and_mode_with_linking_apart_and_no_dollars_there(tmp_path) -> None:
    linking_reply = ModelReply("m", "m", None, tables("store_sales", "store", "date_dim"), 7900, 0, 14, 0.0008, 5)
    generated = ModelReply("m", "m", None, reply(Q1_SQL), 2000, 0, 60, 0.0003, 5)
    scripts = {"retrieval": [generated], "linked": [generated], "full_schema": [generated]}
    result, _, _ = _run(tmp_path, [[Q1]], scripts, [linking_reply])
    text = runs_report_b([_document([_heldout2(r) for r in result.records], 1)], [])
    head = text.split("THE ORIGINAL SET", 1)[0]
    assert "  linked\n" in head
    linked = head.split("  linked\n", 1)[1]
    assert "    tokens of generation, every answer: 2000 in, 60 out, over 1 answers" in linked
    assert "    linking, apart: 7900 in, 14 out, over 1 calls in 1 answers" in linked
    assert "$" not in text.split("SPEND", 1)[0]


def test_each_comparison_sits_under_its_own_heading_and_names_the_better_mode(tmp_path) -> None:
    runs = []
    for number in (1, 2):
        scripts = {"retrieval": [reply(Q1_SQL)], "linked": [reply(status="not_answerable")], "full_schema": [reply(Q1_SQL)]}
        result, _, _ = _run(tmp_path, [[Q1]], scripts, [tables("store_sales", "store", "date_dim")], number=number)
        runs.append(_document([_heldout2(r) for r in result.records], number))
    head = runs_report_b(runs, []).split("THE ORIGINAL SET", 1)[0]
    against_full = head.split("  linked against full schema", 1)[1].split("  linked against retrieval", 1)[0]
    against_retrieval = head.split("  linked against retrieval", 1)[1].split("  the three modes side by side", 1)[0]
    assert "    tables: linked better on 0 (none); full schema better on 1 (1); the same on 0" in against_full
    assert "    tables: linked better on 0 (none); retrieval better on 1 (1); the same on 0" in against_retrieval


def test_the_report_keeps_the_tables_named_apart_from_the_tree(tmp_path) -> None:
    runs = []
    for number in (1, 2):
        scripts = {m: [reply(Q1_SQL)] for m in ("retrieval", "linked", "full_schema")}
        result, _, _ = _run(tmp_path, [[Q1]], scripts, [tables("store_sales", "store", "date_dim")], number=number)
        for r in result.records:
            if r["mode"] == "linked":
                r["recall"]["named"] = {"matched": 1, "expected": 3, "count": 4}
        runs.append(_document([_heldout2(r) for r in result.records], number))
    head = runs_report_b(runs, []).split("THE ORIGINAL SET", 1)[0]
    assert "    tables named by linking: recall 2 of 6 (33.3%), precision 2 of 8 (25.0%), over 2 runs" in head
    assert "    linked, tables in the selected tree: recall 6 of 6 (100.0%), precision 6 of 6 (100.0%)" in head


def test_the_spend_counts_the_linking_calls(tmp_path) -> None:
    scripts = {m: [reply(Q1_SQL)] for m in ("retrieval", "linked", "full_schema")}
    result, _, _ = _run(tmp_path, [[Q1]], scripts, [reply(), tables("store_sales", "store", "date_dim")])
    linked = next(r for r in result.records if r["mode"] == "linked")
    assert [a["outcome"] for a in linked["linking"]["attempts"]] == ["malformed", "accepted"]
    text = runs_report_b([_document(result.records, 1)], [])
    assert "    model calls 5 (3 generation, 2 linking): " in text.split("SPEND", 1)[1]
