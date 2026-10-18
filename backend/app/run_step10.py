"""Step 10: the evaluation, both modes, both sets (FR-37, FR-38, DD-20).

    docker compose exec backend python -m app.run_step10 --pilot
    docker compose exec backend python -m app.run_step10 --write-manifest
    docker compose exec backend python -m app.run_step10 --run K --head SHA
    docker compose exec backend python -m app.run_step10 --report

`--pilot` puts the eight development questions through both modes once,
to find faults in this harness on questions that decide nothing. They
carry no expectations and nothing is scored. Kept in eval/step10/pilot/.

`--write-manifest` records the sha256 of every file of the code that
answers and measures (app.shell.step10.FROZEN), in
eval/step10/frozen_code.json. It is written in the protocol's commit.

`--run K --head SHA` is the evaluation, one run per command, K from 1 to
5, so that each run's files can be committed before the next starts and
a stopped run can be replaced. SHA is the commit it runs on, read by the
session with its own `git rev-parse HEAD` beforehand (the container has
no repository) and recorded in the run's file with the manifest check's
result; the five may name different commits, and the manifest shows the
code was the same. Before anything is opened or paid for it refuses
unless the manifest matches, runs 1 to K-1 each have a complete file, run
K has none, no later run has any file, run K has fewer than three void
runs, no run was stopped by the step cap, and there is no report. It is
the only place that reads eval/heldout.yaml, through its loader, which
checks the sha256. In run K: the held-out set, then the original set; for
each question both modes, retrieval first when K is odd and full schema
first when it is even. Kept in eval/step10/runs/: run-0K.json, or
run-0K-void-N.json for the Nth void run of that number.

`--report` makes eval/step10/runs/report.txt from those files, once five
runs are complete. It never overwrites and calls nothing that is paid.

WHAT VOIDS A RUN (the owner's ruling 5 at stop 1). A transport or
provider failure that survives two retries, a failure to embed, or the
model's own spending ceiling: none is the method's behaviour. The run is
recorded with its cause and replaced by a complete fresh run of the same
number, by running `--run K` again. The step cap is checked before every
question: if what has been spent on purposes beginning `step10`, plus
that question's worst case, would pass it, the run is void and no run
starts again. Three void runs of one number, which are three in a row,
also stop the step. No run is ever voided, kept or replaced because of
its results.

Every model call and embedding goes to the spend ledger under the run's
purpose.
"""

import argparse
import json
import logging
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import yaml

from app.config import MODEL_PRICES, get_settings
from app.core.eval_set import parse_eval_set
from app.core.eval_step10 import (
    Answer,
    Expectation,
    from_heldout,
    from_original,
    key_columns,
    retrieval_recall,
    score,
)
from app.core.full_schema import build_full_prompt
from app.core.sql_reading import schema_of
from app.core.trace_document import preferences_hash
from app.orchestrator import Trace, sha256
from app.shell.embedder import EmbeddingFailed
from app.shell.model_client import SpendLedger
from app.shell.step10 import (
    FROZEN,
    RETRIED,
    STEP_CAP_USD,
    STEP_PREFIX,
    RetryingModel,
    check_manifest,
    code_manifest,
    create_exclusively,
    keep_identical,
    load_heldout,
    rows_sha256,
    uses_current_date,
    worst_case_usd,
)

BACKEND = Path(__file__).resolve().parents[1]
EVAL = BACKEND / "eval"
STEP10 = EVAL / "step10"
MANIFEST = STEP10 / "frozen_code.json"
DEV_QUESTIONS = EVAL / "dev_questions.yaml"
ORIGINAL_SET = EVAL / "questions.yaml"
HELDOUT_SET = EVAL / "heldout.yaml"
MODES = ("retrieval", "full_schema")
RUNS = 5
MAX_VOID_IN_A_ROW = 3


@dataclass(frozen=True)
class Asked:
    """One question as the runner puts it: its set, id and text, and its
    expectation where it has one (the development questions have none)."""

    set: str
    id: str
    question: str
    expectation: Expectation | None = None


@dataclass
class RunResult:
    records: list[dict] = field(default_factory=list)
    void: str | None = None


def mode_order(number: int) -> tuple[str, ...]:
    """Retrieval first in odd runs, full schema first in even ones."""
    return MODES if number % 2 else tuple(reversed(MODES))


def _join(join) -> str:
    return " AND ".join(f"{join.fk_table}.{a} = {join.pk_table}.{b}" for a, b in zip(join.fk_columns, join.pk_columns))


def _edge(edge) -> str:
    return " AND ".join(f"{a[0]}.{a[1]} = {b[0]}.{b[1]}" for a, b in edge)


def _equality(e) -> str:
    how = (" (outer)" if e.outer else "") + (" (union)" if e.union else "")
    return f"{e.left[0]}.{e.left[1]} = {e.right[0]}.{e.right[1]}{how}"


def _located(trace: Trace, mode: str) -> dict | None:
    if mode != "retrieval" or trace.located is None:
        return None
    located = trace.located
    tree = located.tree
    codes = getattr(located, "warning_codes", None)
    if codes is None:
        codes = () if located.declined else located.explanation.codes
    return {
        "anchors": list(tree.anchors),
        "tree_tables": list(tree.tables),
        "tree_joins": sorted(_join(join) for join in tree.joins),
        "subgraph": list(tree.subgraph),
        "warnings": sorted(codes),
        "declined": located.declined,
        "decline_reason": located.decline_reason or tree.decline_reason,
    }


def record_of(trace: Trace, asked: Asked, mode: str, schema, keys, extra: dict) -> dict:
    """Everything kept of one answer. No row value: the rows' hash only."""
    sql = trace.sql
    execution = trace.execution
    record = {
        "set": asked.set,
        "id": asked.id,
        "question": asked.question,
        "mode": mode,
        **extra,
        "outcome": trace.outcome,
        "code": trace.code,
        # The database's own words on an execution failure can quote a value
        # from the data (item 78, DR-15): kept are its failure word and
        # SQLSTATE, under "execution". Every other message is the system's.
        "message": None if trace.outcome == "execution_failed" else trace.message,
        "located": _located(trace, mode),
        "prompt": None if trace.prompt is None else {
            "sha256": sha256(trace.prompt.text), "chars": len(trace.prompt.text),
            "tables": list(trace.prompt.tables), "has_join_section": trace.prompt.has_join_section,
        },
        "attempts": [
            {"number": a.number, "outcome": a.outcome, "requested_model": a.reply.requested_model,
             "returned_model": a.reply.returned_model, "tokens_in": a.reply.tokens_in,
             "tokens_cached": a.reply.tokens_cached, "tokens_out": a.reply.tokens_out, "cost_usd": a.reply.cost_usd,
             "duration_ms": a.reply.duration_ms, "failure": a.reply.failure, "sql": a.sql,
             "findings": list(a.findings), "tables_outside_prompt": list(a.tables_outside_prompt)}
            for a in trace.attempts
        ],
        "sql": sql,
        "validated_sha256": trace.validated_sha256,
        "executed_sha256": trace.executed_sha256,
        "extraction": None if trace.conformance is None else {
            "equalities": [_equality(e) for e in trace.actual_edges],
            "tables": list(trace.sql_tables),
            "unchecked": [f"{u.reason}: {u.detail}" for u in trace.conformance.unchecked],
            "cross_joins": [list(group) for group in trace.conformance.cross_joins],
        },
        # Retrieval mode: against the joins it selected. Full schema has no
        # selection, so this says nothing there.
        "conformance_to_selected": trace.conformance.outcome if (trace.conformance and mode == "retrieval") else None,
        "execution": None if execution is None else {
            "ok": execution.ok, "failure": execution.failure, "sqlstate": execution.sqlstate,
            "row_count": execution.row_count, "truncated": execution.truncated,
            "rows_sha256": rows_sha256(execution.rows) if execution.ok else None,
            "duration_ms": execution.duration_ms, "columns": len(execution.columns),
        },
        "uses_current_date": uses_current_date(sql),
        "empty_result": bool(execution and execution.ok and execution.row_count == 0),
        "timings": trace.timings,
        "tokens_in": trace.tokens_in,
        "tokens_out": trace.tokens_out,
        "cost_usd": trace.cost_usd,
        "score": None,
        "recall": None,
        "excluded": None,
    }  # fmt: skip
    expectation = asked.expectation
    if expectation is not None:
        sql_for_score = sql if trace.outcome in ("answered", "execution_failed") else None
        got = score(expectation, Answer(trace.outcome, sql_for_score), schema, keys)
        record["score"] = {
            "tables": got.tables, "path": got.path, "why": got.why, "sql_tables": list(got.sql_tables),
            "tables_unresolved": list(got.tables_unresolved), "attribute_joins": list(got.attribute_joins),
            "choices": [{"edges": [_edge(edge) for edge in c.edges], "outcome": c.conformance.outcome,
                         "findings": list(c.conformance.findings)} for c in got.choices],
        }  # fmt: skip
        record["excluded"] = expectation.excluded
        if mode == "retrieval":
            shown = trace.prompt.tables if trace.prompt is not None else ()
            recall = retrieval_recall(expectation, tuple(shown))
            tree = trace.located.tree.tables if trace.located is not None and not trace.located.declined else ()
            in_tree = retrieval_recall(expectation, tuple(tree))
            record["recall"] = None if recall is None else {
                "shown": {"matched": recall.matched, "expected": recall.expected, "count": recall.shown},
                "tree": {"matched": in_tree.matched, "expected": in_tree.expected, "count": in_tree.shown},
            }  # fmt: skip
    return record


def void_cause(trace: Trace) -> str | None:
    """A failure that is not the method's behaviour, still there after the
    transport retries."""
    if not trace.attempts:
        return None
    failure = trace.attempts[-1].reply.failure
    if failure in RETRIED:
        return f"{failure} failure after retries"
    if failure == "ceiling":
        return "the model's spending ceiling"
    return None


def run_once(
    number: int,
    sets: list[list[Asked]],
    pipelines: dict,
    models: dict[str, RetryingModel],
    ledger: SpendLedger,
    cap_usd: float,
    worst_usd: float,
    schema,
    keys,
    keep_prompt: Callable[[str, str], None],
) -> RunResult:
    """One run: every question of every set, each in both modes."""
    result = RunResult()
    position = 0
    for questions in sets:
        for asked in questions:
            for mode in mode_order(number):
                position += 1
                spent = ledger.total(STEP_PREFIX)
                cap_check = {"spent_usd": round(spent, 8), "worst_case_usd": round(worst_usd, 8), "cap_usd": cap_usd}
                if spent + worst_usd > cap_usd:
                    result.void = (
                        f"the step cap: ${spent:.6f} spent on {STEP_PREFIX} purposes and up to ${worst_usd:.6f} "
                        f"for the next question would pass ${cap_usd}; nothing more was run"
                    )
                    return result
                began = datetime.now(UTC).isoformat(timespec="seconds")
                try:
                    trace = pipelines[mode].orchestrator.answer(asked.question)
                except EmbeddingFailed as failed:
                    result.void = f"embedding failed: {failed}"
                    return result
                if trace.prompt is not None:
                    keep_prompt(sha256(trace.prompt.text), trace.prompt.text)
                extra = {"run": number, "position": position, "began": began, "cap_check": cap_check,
                         "transport_retries": models[mode].take_events()}  # fmt: skip
                result.records.append(record_of(trace, asked, mode, schema, keys, extra))
                cause = void_cause(trace)
                if cause:
                    result.void = cause
                    return result
    return result


def provenance(head: str | None, differences: list[str] | None) -> dict:
    """What a run file says it ran on: the commit, and the manifest check."""
    if differences is None:
        manifest = "no manifest"
    elif differences:
        manifest = "differs: " + "; ".join(differences)
    else:
        manifest = "matches"
    return {"head": head, "manifest": manifest}


def keep_run(path: Path, document: dict) -> None:
    """One run's file, created once; an existing one is never overwritten."""
    create_exclusively(path, json.dumps(document, indent=1, default=str) + "\n")


# --------------------------------------------------------------------------
# The sets
# --------------------------------------------------------------------------


def development_questions() -> list[Asked]:
    entries = yaml.safe_load(DEV_QUESTIONS.read_text())["questions"]
    return [Asked("dev", entry["id"], entry["question"]) for entry in entries]


def evaluation_sets() -> list[list[Asked]]:
    """The held-out set first, then the original. Reads heldout.yaml."""
    heldout = [from_heldout(q) for q in load_heldout(HELDOUT_SET).questions]
    original = [from_original(q) for q in parse_eval_set(ORIGINAL_SET.read_text()).questions]
    return [[Asked(e.set, e.id, e.question, e) for e in heldout], [Asked(e.set, e.id, e.question, e) for e in original]]


# --------------------------------------------------------------------------
# The command
# --------------------------------------------------------------------------


def _write_manifest() -> int:
    STEP10.mkdir(parents=True, exist_ok=True)
    if MANIFEST.exists():
        sys.exit(f"run_step10: {MANIFEST.name} exists; nothing is overwritten")
    manifest = code_manifest(BACKEND, FROZEN)
    create_exclusively(MANIFEST, json.dumps({"patterns": list(FROZEN), "files": manifest}, indent=1) + "\n")
    print(f"run_step10: {len(manifest)} files recorded in {MANIFEST.relative_to(BACKEND)}")
    return 0


def _manifest_differences() -> list[str] | None:
    if not MANIFEST.exists():
        return None
    recorded = json.loads(MANIFEST.read_text())
    return check_manifest(BACKEND, tuple(recorded["patterns"]), recorded["files"])


def _run_file(number: int) -> str:
    return f"run-{number:02d}.json"


def _voids(out: Path, number: int) -> list[Path]:
    return sorted(out.glob(f"run-{number:02d}-void-*.json"))


def void_name(out: Path, number: int) -> str:
    """A name no file of this folder has: the next void of this number."""
    taken = len(_voids(out, number))
    while (out / f"run-{number:02d}-void-{taken + 1}.json").exists():
        taken += 1
    return f"run-{number:02d}-void-{taken + 1}.json"


def refusal(out: Path, number: int) -> str | None:
    """Why run `number` may not start in `out`, or None when it may."""
    if not 1 <= number <= RUNS:
        return f"runs are numbered 1 to {RUNS}, not {number}"
    if (out / "report.txt").exists():
        return "the report already exists; the runs are over"
    for void in sorted(out.glob("run-*-void-*.json")):
        if (json.loads(void.read_text())["void"] or "").startswith("the step cap"):
            return f"{void.name} was stopped by the step cap; no run starts again"
    for earlier in range(1, number):
        if not (out / _run_file(earlier)).exists():
            return f"run {earlier} has no complete file"
    if (out / _run_file(number)).exists():
        return f"run {number} is complete; it is never run again"
    for later in range(number + 1, RUNS + 1):
        if (out / _run_file(later)).exists() or _voids(out, later):
            return f"run {later} already has files"
    if len(_voids(out, number)) >= MAX_VOID_IN_A_ROW:
        return f"run {number} has been void {MAX_VOID_IN_A_ROW} times: three void runs in a row stop the step"
    return None


def _report_refusal(out: Path) -> str | None:
    if (out / "report.txt").exists():
        return "the report already exists; nothing is overwritten"
    for number in range(1, RUNS + 1):
        if not (out / _run_file(number)).exists():
            return f"run {number} has no complete file"
    return None


def _execute(label: str, number: int, sets: list[list[Asked]], out: Path, head: str | None,
             differences: list[str] | None, cap: float, purpose: str) -> RunResult:
    """One run, both modes, kept in its own file."""
    from app.shell.pipeline_session import open_pipeline

    settings = get_settings()
    price = MODEL_PRICES[settings.sql_model]
    out.mkdir(parents=True, exist_ok=True)
    (out / "prompts").mkdir(exist_ok=True)

    def keep_prompt(digest: str, text: str) -> None:
        keep_identical(out / "prompts" / f"{digest}.txt", text)

    models: dict[str, RetryingModel] = {}

    def wrap(model, mode):
        models[mode] = RetryingModel(model)
        return models[mode]

    pipelines = {
        mode: open_pipeline("run_step10", purpose, mode=mode, wrap_model=lambda m, mode=mode: wrap(m, mode))
        for mode in MODES
    }
    some = pipelines["full_schema"]
    schema, keys = schema_of(some.snapshot), key_columns(some.snapshot)
    # The largest prompt either mode can send, with room for the question.
    worst = worst_case_usd(len(build_full_prompt("", some.snapshot, some.graph).text) + 2000, price,
                           settings.sql_model_max_output_tokens)  # fmt: skip
    retrieval = pipelines["retrieval"].retrieval
    began = datetime.now(UTC).isoformat(timespec="seconds")
    result = run_once(number, sets, pipelines, models, some.ledger, cap, worst, schema, keys, keep_prompt)
    name = (f"{label}-{number:02d}.json" if not result.void else
            void_name(out, number) if label == "run" else f"{label}-{number:02d}-void.json")  # fmt: skip
    document = {
        "label": label, "run": number, "file": name, "void": result.void, "began": began,
        "ended": datetime.now(UTC).isoformat(timespec="seconds"),
        **provenance(head, differences),
        "settings": {"model": settings.sql_model, "reasoning_effort": settings.sql_model_reasoning_effort,
                     "temperature": settings.sql_model_temperature,
                     "max_output_tokens": settings.sql_model_max_output_tokens, "price": vars(price),
                     "alpha": retrieval.settings.alpha, "anchor_cut": retrieval.settings.anchor_cut,
                     "anchor_cap": retrieval.settings.anchor_cap, "margin": retrieval.settings.margin,
                     "schema_sha256": retrieval.stored.hash, "preferences_hash": preferences_hash(some.snapshot),
                     "cap_usd": cap, "worst_case_per_question_usd": round(worst, 8)},
        "spend": {"step10_usd_after": round(some.ledger.total(STEP_PREFIX), 8),
                  "this_purpose_usd": round(some.ledger.total(purpose), 8)},
        "records": result.records,
    }  # fmt: skip
    keep_run(out / name, document)
    print(f"run_step10: {name}: {len(result.records)} answers" + (f"; VOID: {result.void}" if result.void else ""))
    return result


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Step 10's evaluation: both modes, both sets.")
    which = parser.add_mutually_exclusive_group(required=True)
    which.add_argument("--pilot", action="store_true", help="the development questions, both modes, once")
    which.add_argument("--run", type=int, help=f"the evaluation: run K, from 1 to {RUNS}, and only that run")
    which.add_argument("--report", action="store_true", help="the report, once five runs are complete")
    which.add_argument("--write-manifest", action="store_true", help="record the frozen code's hashes")
    parser.add_argument("--head", help="the commit the run runs on: 40 hex characters (git rev-parse HEAD)")
    parser.add_argument("--cap", type=float, default=STEP_CAP_USD, help="the step cap, in dollars")
    parser.add_argument("--out", type=Path, help="where the files are kept (default: eval/step10/pilot or runs)")
    arguments = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(message)s", stream=sys.stderr)

    if arguments.write_manifest:
        return _write_manifest()

    if arguments.report:
        from app.step10_report import write_report

        out = arguments.out or STEP10 / "runs"
        refused = _report_refusal(out)
        if refused:
            sys.exit(f"run_step10: {refused}; no report was made")
        report = write_report(out, "run")
        print(f"run_step10: report in {report}")
        return 0

    if arguments.pilot:
        out = arguments.out or STEP10 / "pilot"
        # Before anything is opened or paid for: a run never overwrites a run.
        taken = sorted(path.name for path in out.glob("pilot-*.json")) + (
            ["report.txt"] if (out / "report.txt").exists() else []
        )
        if taken:
            sys.exit(f"run_step10: {out} already holds {', '.join(taken)}; nothing is overwritten and nothing was run")
        result = _execute("pilot", 1, [development_questions()], out, arguments.head, _manifest_differences(),
                          arguments.cap, f"{STEP_PREFIX} pilot")  # fmt: skip
        if result.void:
            return 1
        from app.step10_report import write_report

        print(f"run_step10: report in {write_report(out, 'pilot')}")
        return 0

    if not re.fullmatch(r"[0-9a-f]{40}", arguments.head or ""):
        sys.exit("run_step10: the evaluation needs --head, the full sha of the commit it runs on (git rev-parse HEAD)")
    differences = _manifest_differences()
    if differences is None:
        sys.exit("run_step10: no manifest. The protocol is committed with one: python -m app.run_step10 --write-manifest")
    if differences:
        sys.exit("run_step10: the frozen code has changed since the protocol: " + "; ".join(differences))
    out = arguments.out or STEP10 / "runs"
    refused = refusal(out, arguments.run)
    if refused:
        sys.exit(f"run_step10: {refused}; nothing is overwritten and nothing was run")
    number = arguments.run
    result = _execute("run", number, evaluation_sets(), out, arguments.head, differences, arguments.cap,
                      f"{STEP_PREFIX} run {number}")  # fmt: skip
    if result.void:
        again = refusal(out, number)
        print(f"run_step10: run {number} is void. " + (f"The step stops: {again}." if again else
              f"It is replaced by running --run {number} again."))  # fmt: skip
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
