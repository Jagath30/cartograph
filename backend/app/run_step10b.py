"""Step 10b: the evaluation of v2, three modes, four sets (the owner's
rulings at stop A, 18 October 2026).

    docker compose exec backend python -m app.run_step10b --pilot --head SHA
    docker compose exec backend python -m app.run_step10b --write-manifest
    docker compose exec backend python -m app.run_step10b --run K --head SHA
    docker compose exec backend python -m app.run_step10b --report

Step 10's harness, imported and never edited: the judge (`eval_step10`),
the record of an answer (`run_step10.record_of`), the transport retries,
the rows hash, the worst case, the manifest, the void rules and the files
created exclusively (`app.shell.step10`). What is new here is a third
mode and the sets.

THE MODES. `retrieval` is v1, run afresh; `linked` is v2: a linking call,
shown every table and key, names the tables, and from those on it is v1;
`full_schema` is step 10's baseline. Their order rotates with the run:
R-L-F, L-F-R, F-R-L, R-L-F, L-F-R.

THE SETS, in this order in every run: heldout2, the fresh held-out set,
the headline; the original set, scored on 15 (Q14's exclusion stands);
the first held-out set, now development; dev2, development. Each loader
checks its own file's sha256 before parsing it. heldout2.yaml is read by
its loader only, and only inside `--run`, after every refusal.

`--pilot` puts dev2 and the eight development questions through the three
modes once, to find faults in this harness. dev2 is scored (it is a
development set); d1 to d8 carry no expectations. Kept in
eval/step10b/pilot/. It never reads heldout2.yaml.

`--write-manifest`, `--run K --head SHA` and `--report` are step 10's,
in eval/step10b/: the manifest written in the protocol's commit and
checked before every run; one run per command, refused before anything
is opened or paid for unless runs 1 to K-1 are complete, run K and every
later run have no file, run K has been void fewer than three times, no
run was stopped by the step cap and there is no report; the report once
five runs are complete, never overwritten, with no paid call.

THE CAP. $0.75 on ledger purposes beginning `step10b`. Before each answer:
what has been spent, plus that answer's worst case, must stay within it,
or the run is void and no run starts again. A linked answer's worst case
is its linking call's and its generation's together, each at three
uncached attempts: more than the shared budget of two retries allows, and
so on the safe side.

WHAT VOIDS A RUN is as at step 10: a transport or provider failure of
either model that survives two retries, the model's spending ceiling, a
failure to embed, the step cap. No run is ever voided, kept or replaced
because of its results.
"""

import argparse
import hashlib
import json
import logging
import re
import sys
from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from app.config import MODEL_PRICES, ModelPrice, get_settings
from app.core.eval_set import parse_eval_set
from app.core.eval_step10 import from_heldout, from_original, key_columns, retrieval_recall
from app.core.full_schema import build_full_prompt
from app.core.linking import LINK_MAX_OUTPUT_TOKENS, build_link_prompt
from app.core.sql_reading import schema_of
from app.core.trace_document import preferences_hash
from app.orchestrator import NOT_ANSWERABLE, Trace, sha256
from app.run_step10 import (
    BACKEND,
    MAX_VOID_IN_A_ROW,
    Asked,
    RunResult,
    _report_refusal,
    development_questions,
    keep_run,
    provenance,
    record_of,
    refusal,
    void_cause,
    void_name,
)
from app.shell.embedder import EmbeddingFailed
from app.shell.model_client import SpendLedger
from app.shell.step10 import (
    FROZEN,
    HeldOutChanged,
    RetryingModel,
    check_manifest,
    code_manifest,
    create_exclusively,
    keep_identical,
    load_heldout,
    worst_case_usd,
)

EVAL = BACKEND / "eval"
STEP10B = EVAL / "step10b"
MANIFEST = STEP10B / "frozen_code.json"
MODES = ("retrieval", "linked", "full_schema")
STEP_PREFIX_B = "step10b"
STEP_CAP_B_USD = 0.75

# The code that answers and measures, as at step 10. Not the sets: each
# has its own pinned hash, checked by its loader.
FROZEN_B = FROZEN

# name -> (file, sha256), in the order every run puts them.
SETS: dict[str, tuple[Path, str]] = {
    "heldout2": (EVAL / "heldout2.yaml", "ffd28657f7fd1c44d519b84fc63f87e02e0f4504dadfe0393c99b275de20f3d1"),
    "original": (EVAL / "questions.yaml", "a92f27c2f3fee9c1d178e21504eadc87aa9ec6a622e896561064e798d1100440"),
    "heldout1": (EVAL / "heldout.yaml", "14e1441e5c9717c519c00410ad27c1bf9b78caf07184141064dffc2377f806fe"),
    "dev2": (EVAL / "dev2.yaml", "4d6dd232aa75181e79c0efa162bd1ea118c305e5d3899aed91654076046c13da"),
}


def mode_order(number: int) -> tuple[str, ...]:
    """R-L-F in run 1, L-F-R in run 2, F-R-L in run 3, and round again."""
    shift = (number - 1) % len(MODES)
    return MODES[shift:] + MODES[:shift]


# --------------------------------------------------------------------------
# The sets
# --------------------------------------------------------------------------


def load_set(name: str, path: Path | None = None, sha: str | None = None) -> list[Asked]:
    """One set, read once and parsed only at its pinned hash. Every
    expectation is named as its set, so heldout1's H1 and heldout2's H1
    stay apart."""
    pinned_path, pinned_sha = SETS[name]
    path, sha = path or pinned_path, sha or pinned_sha
    if name == "original":
        data = path.read_bytes()
        found = hashlib.sha256(data).hexdigest()
        if found != sha:
            raise HeldOutChanged(f"{path.name} is not the file that was frozen: sha256 {found[:12]}, not {sha[:12]}")
        expectations = [from_original(q) for q in parse_eval_set(data.decode()).questions]
    else:
        expectations = [replace(from_heldout(q), set=name) for q in load_heldout(path, sha).questions]
    return [Asked(e.set, e.id, e.question, e) for e in expectations]


def evaluation_sets() -> list[list[Asked]]:
    """Every set, in order. Reads heldout2.yaml: called inside --run only."""
    return [load_set(name) for name in SETS]


def pilot_sets() -> list[list[Asked]]:
    """dev2, scored, and the eight development questions, not scored."""
    return [load_set("dev2"), development_questions()]


# --------------------------------------------------------------------------
# One answer's record
# --------------------------------------------------------------------------


def declined_at(trace: Trace) -> str | None:
    """Where a decline was decided: by linking naming no table, at locate
    (no anchors, or anchors not joined), or by the model shown the tables."""
    if trace.outcome != "not_answerable":
        return None
    if trace.message == NOT_ANSWERABLE["linking"]:
        return "linking"
    return "model" if trace.attempts else "locate"


def _linking(trace: Trace) -> dict | None:
    linking = trace.linking
    if linking is None:
        return None
    attempts = [
        {"number": a.number, "outcome": a.outcome, "tables": list(a.tables), "fault": a.fault,
         "requested_model": a.reply.requested_model, "returned_model": a.reply.returned_model,
         "tokens_in": a.reply.tokens_in, "tokens_cached": a.reply.tokens_cached, "tokens_out": a.reply.tokens_out,
         "cost_usd": a.reply.cost_usd, "duration_ms": a.reply.duration_ms, "failure": a.reply.failure}
        for a in linking.attempts
    ]  # fmt: skip
    return {
        "tables": None if linking.tables is None else list(linking.tables),
        "prompt": {"sha256": sha256(linking.prompt.text), "chars": len(linking.prompt.text)},
        "attempts": attempts,
        "tokens_in": sum(a["tokens_in"] for a in attempts),
        "tokens_out": sum(a["tokens_out"] for a in attempts),
        "cost_usd": sum(a["cost_usd"] for a in attempts),
    }


def record_b(trace: Trace, asked: Asked, mode: str, schema, keys, extra: dict) -> dict:
    """Step 10's record, plus where a decline was decided and, for linked,
    the linking: the tables it named, each attempt's tokens, cost and fault,
    and the recall of the tables named beside those in the tree and shown.

    Linked mode has a tree and a selection as retrieval does, so its record
    is step 10's record of a retrieval answer, under its own mode."""
    record = record_of(trace, asked, "retrieval" if mode == "linked" else mode, schema, keys, extra)
    record["mode"] = mode
    record["declined_at"] = declined_at(trace)
    record["linking"] = _linking(trace) if mode == "linked" else None
    e = asked.expectation
    record["expected"] = None if e is None else {
        "tables": list(e.tables), "tables_one_of": list(e.tables_one_of), "decline": e.decline,
    }  # fmt: skip
    if mode == "linked":
        named = tuple(trace.linking.tables or ()) if trace.linking else ()
        if record["located"] is not None:
            record["located"]["linked"] = list(named)
        recall = retrieval_recall(asked.expectation, named) if asked.expectation is not None else None
        if recall is None:
            record["recall"] = None
        else:
            record["recall"] = record["recall"] or {}
            record["recall"]["named"] = {"matched": recall.matched, "expected": recall.expected, "count": recall.shown}
    return record


def void_cause_b(trace) -> str | None:
    """Step 10's rule, read on the stage that ended the question: generation
    when it was reached, linking when it was not."""
    attempts = trace.attempts or (trace.linking.attempts if trace.linking is not None else ())
    return void_cause(SimpleNamespace(attempts=attempts))


# --------------------------------------------------------------------------
# One run
# --------------------------------------------------------------------------


def worst_case_b(prompt_chars: int, link_chars: int, price: ModelPrice, max_output_tokens: int,
                 link_max_output_tokens: int) -> float:  # fmt: skip
    """A linked answer at most: its linking call's worst case and its
    generation's, together."""
    return worst_case_usd(link_chars, price, link_max_output_tokens) + worst_case_usd(
        prompt_chars, price, max_output_tokens
    )


def run_once_b(
    number: int,
    sets: list[list[Asked]],
    pipelines: dict,
    models: dict[str, list[RetryingModel]],
    ledger: SpendLedger,
    cap_usd: float,
    worst_usd: Mapping[str, float],
    schema,
    keys,
    keep_prompt: Callable[[str, str], None],
) -> RunResult:
    """One run: every question of every set, each in the three modes."""
    result = RunResult()
    position = 0
    for questions in sets:
        for asked in questions:
            for mode in mode_order(number):
                position += 1
                spent = ledger.total(STEP_PREFIX_B)
                worst = worst_usd[mode]
                cap_check = {"spent_usd": round(spent, 8), "worst_case_usd": round(worst, 8), "cap_usd": cap_usd}
                if spent + worst > cap_usd:
                    result.void = (
                        f"the step cap: ${spent:.6f} spent on {STEP_PREFIX_B} purposes and up to ${worst:.6f} "
                        f"for the next answer would pass ${cap_usd}; nothing more was run"
                    )
                    return result
                began = datetime.now(UTC).isoformat(timespec="seconds")
                try:
                    trace = pipelines[mode].orchestrator.answer(asked.question)
                except EmbeddingFailed as failed:
                    result.void = f"embedding failed: {failed}"
                    return result
                if trace.linking is not None:
                    keep_prompt(sha256(trace.linking.prompt.text), trace.linking.prompt.text)
                if trace.prompt is not None:
                    keep_prompt(sha256(trace.prompt.text), trace.prompt.text)
                retried = [event for model in models[mode] for event in model.take_events()]
                extra = {"run": number, "position": position, "began": began, "cap_check": cap_check,
                         "transport_retries": retried}  # fmt: skip
                result.records.append(record_b(trace, asked, mode, schema, keys, extra))
                cause = void_cause_b(trace)
                if cause:
                    result.void = cause
                    return result
    return result


# --------------------------------------------------------------------------
# The command
# --------------------------------------------------------------------------


def _write_manifest() -> int:
    STEP10B.mkdir(parents=True, exist_ok=True)
    if MANIFEST.exists():
        sys.exit(f"run_step10b: {MANIFEST.name} exists; nothing is overwritten")
    manifest = code_manifest(BACKEND, FROZEN_B)
    create_exclusively(MANIFEST, json.dumps({"patterns": list(FROZEN_B), "files": manifest}, indent=1) + "\n")
    print(f"run_step10b: {len(manifest)} files recorded in {MANIFEST.relative_to(BACKEND)}")
    return 0


def _manifest_differences() -> list[str] | None:
    if not MANIFEST.exists():
        return None
    recorded = json.loads(MANIFEST.read_text())
    return check_manifest(BACKEND, tuple(recorded["patterns"]), recorded["files"])


def _pilot_voids(out: Path) -> list[Path]:
    """Every void pilot, under either name: pilot-01-void.json, as the first
    was kept, or pilot-01-void-N.json."""
    return sorted(out.glob("pilot-01-void*.json"))


def pilot_void_name(out: Path) -> str:
    """A name no file of this folder has: the next void pilot."""
    taken = len(_pilot_voids(out))
    while (out / f"pilot-01-void-{taken + 1}.json").exists():
        taken += 1
    return f"pilot-01-void-{taken + 1}.json"


def pilot_refusal(out: Path) -> str | None:
    """Why the pilot may not start in `out`, or None. As a run: a void pilot
    is replaced by a fresh one, never overwritten, until three in a row or
    one stopped by the step cap."""
    taken = [name for name in ("pilot-01.json", "report.txt") if (out / name).exists()]
    if taken:
        return f"{out} already holds {', '.join(taken)}; nothing is overwritten"
    voids = _pilot_voids(out)
    for void in voids:
        if (json.loads(void.read_text())["void"] or "").startswith("the step cap"):
            return f"{void.name} was stopped by the step cap; no pilot starts again"
    if len(voids) >= MAX_VOID_IN_A_ROW:
        return f"the pilot has been void {MAX_VOID_IN_A_ROW} times: three void runs in a row stop the step"
    return None


def _execute(label: str, number: int, sets: list[list[Asked]], out: Path, head: str | None,
             differences: list[str] | None, cap: float, purpose: str) -> RunResult:
    """One run, three modes, kept in its own file."""
    from app.shell.pipeline_session import open_pipeline

    settings = get_settings()
    price = MODEL_PRICES[settings.sql_model]
    out.mkdir(parents=True, exist_ok=True)
    (out / "prompts").mkdir(exist_ok=True)

    def keep_prompt(digest: str, text: str) -> None:
        keep_identical(out / "prompts" / f"{digest}.txt", text)

    models: dict[str, list[RetryingModel]] = {mode: [] for mode in MODES}

    def wrap(model, mode):
        models[mode].append(RetryingModel(model))
        return models[mode][-1]

    pipelines = {
        mode: open_pipeline("run_step10b", purpose, mode=mode, wrap_model=lambda m, mode=mode: wrap(m, mode))
        for mode in MODES
    }
    some = pipelines["full_schema"]
    schema, keys = schema_of(some.snapshot), key_columns(some.snapshot)
    # The largest prompt each call can send, with room for the question.
    full_chars = len(build_full_prompt("", some.snapshot, some.graph).text) + 2000
    link_chars = len(build_link_prompt("", some.snapshot, some.graph).text) + 2000
    plain = worst_case_usd(full_chars, price, settings.sql_model_max_output_tokens)
    worst = {
        "retrieval": plain,
        "full_schema": plain,
        "linked": worst_case_b(full_chars, link_chars, price, settings.sql_model_max_output_tokens,
                               LINK_MAX_OUTPUT_TOKENS),
    }  # fmt: skip
    retrieval = pipelines["retrieval"].retrieval
    began = datetime.now(UTC).isoformat(timespec="seconds")
    result = run_once_b(number, sets, pipelines, models, some.ledger, cap, worst, schema, keys, keep_prompt)
    name = (f"{label}-{number:02d}.json" if not result.void else
            void_name(out, number) if label == "run" else pilot_void_name(out))  # fmt: skip
    document = {
        "label": label, "run": number, "file": name, "void": result.void, "began": began,
        "ended": datetime.now(UTC).isoformat(timespec="seconds"),
        **provenance(head, differences),
        "modes": list(mode_order(number)), "sets": [questions[0].set for questions in sets if questions],
        "settings": {"model": settings.sql_model, "reasoning_effort": settings.sql_model_reasoning_effort,
                     "temperature": settings.sql_model_temperature,
                     "max_output_tokens": settings.sql_model_max_output_tokens,
                     "link_max_output_tokens": LINK_MAX_OUTPUT_TOKENS, "price": vars(price),
                     "alpha": retrieval.settings.alpha, "anchor_cut": retrieval.settings.anchor_cut,
                     "anchor_cap": retrieval.settings.anchor_cap, "margin": retrieval.settings.margin,
                     "schema_sha256": retrieval.stored.hash, "preferences_hash": preferences_hash(some.snapshot),
                     "cap_usd": cap, "worst_case_per_answer_usd": {m: round(w, 8) for m, w in worst.items()}},
        "spend": {"step10b_usd_after": round(some.ledger.total(STEP_PREFIX_B), 8),
                  "this_purpose_usd": round(some.ledger.total(purpose), 8)},
        "records": result.records,
    }  # fmt: skip
    keep_run(out / name, document)
    print(f"run_step10b: {name}: {len(result.records)} answers" + (f"; VOID: {result.void}" if result.void else ""))
    return result


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Step 10b's evaluation: three modes, four sets.")
    which = parser.add_mutually_exclusive_group(required=True)
    which.add_argument("--pilot", action="store_true", help="dev2 and the development questions, three modes, once")
    which.add_argument("--run", type=int, help="the evaluation: run K, from 1 to 5, and only that run")
    which.add_argument("--report", action="store_true", help="the report, once five runs are complete")
    which.add_argument("--write-manifest", action="store_true", help="record the frozen code's hashes")
    parser.add_argument("--head", help="the commit the run runs on: 40 hex characters (git rev-parse HEAD)")
    parser.add_argument("--cap", type=float, default=STEP_CAP_B_USD, help="the step cap, in dollars")
    parser.add_argument("--out", type=Path, help="where the files are kept (default: eval/step10b/pilot or runs)")
    arguments = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(message)s", stream=sys.stderr)

    if arguments.write_manifest:
        return _write_manifest()

    if arguments.report:
        from app.step10b_report import write_report_b

        out = arguments.out or STEP10B / "runs"
        refused = _report_refusal(out)
        if refused:
            sys.exit(f"run_step10b: {refused}; no report was made")
        print(f"run_step10b: report in {write_report_b(out, 'run')}")
        return 0

    if arguments.pilot:
        out = arguments.out or STEP10B / "pilot"
        # Before anything is opened or paid for: a run never overwrites a run.
        refused = pilot_refusal(out)
        if refused:
            sys.exit(f"run_step10b: {refused}; nothing was run")
        result = _execute("pilot", 1, pilot_sets(), out, arguments.head, _manifest_differences(), arguments.cap,
                          f"{STEP_PREFIX_B} pilot")  # fmt: skip
        if result.void:
            again = pilot_refusal(out)
            print("run_step10b: the pilot is void. " + (f"The pilot stops: {again}." if again else
                  "It is replaced by running --pilot again."))  # fmt: skip
            return 1
        from app.step10b_report import write_report_b

        print(f"run_step10b: report in {write_report_b(out, 'pilot')}")
        return 0

    if not re.fullmatch(r"[0-9a-f]{40}", arguments.head or ""):
        sys.exit("run_step10b: the evaluation needs --head, the full sha of the commit it runs on (git rev-parse HEAD)")
    differences = _manifest_differences()
    if differences is None:
        sys.exit("run_step10b: no manifest. The protocol is committed with one: python -m app.run_step10b --write-manifest")
    if differences:
        sys.exit("run_step10b: the frozen code has changed since the protocol: " + "; ".join(differences))
    out = arguments.out or STEP10B / "runs"
    refused = refusal(out, arguments.run)
    if refused:
        sys.exit(f"run_step10b: {refused}; nothing is overwritten and nothing was run")
    number = arguments.run
    result = _execute("run", number, evaluation_sets(), out, arguments.head, differences, arguments.cap,
                      f"{STEP_PREFIX_B} run {number}")  # fmt: skip
    if result.void:
        again = refusal(out, number)
        print(f"run_step10b: run {number} is void. " + (f"The step stops: {again}." if again else
              f"It is replaced by running --run {number} again."))  # fmt: skip
        return 1
    return 0

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
