"""Step 10's report, made from the kept run files and nothing else.

Everything in it is counted from eval/step10/<pilot|runs>/*.json, so it
can be made again from the committed files by anyone. Plain lines, no
tables.

For the evaluation, in this order (the owner's rulings at stop 1):

  the held-out set, the headline, for both modes;
  the original set beside it, labelled as the set the system was tuned and
    developed against (steps 6 and 7b), scored on 15: question 14 is
    deleted from scoring and its raw results are shown apart;
  for each set and mode, table and path correctness: each run's count
    over its measured denominator, and the mean of the runs' fractions
    with their range;
  each question's stability over the runs, both modes side by side;
  what left a denominator and why; declines and failures by cause;
  retrieval recall and precision, retrieval mode only, a diagnostic;
  the attribute joins (item 66), counted beside and never in;
  relative dates and empty results (item 60), outside the score;
  void runs, with their causes; spend.

The structural claim -- that Cartograph says when a choice had no basis --
is a property, and is never put here as a measurement (DD-20).
"""

import json
from pathlib import Path

from app.core.eval_step10 import CORRECT, NOT_MEASURED, Tally, combine, stability, tally

MODES = ("retrieval", "full_schema")
SETS = (
    ("heldout", "THE HELD-OUT SET (12 questions): the headline"),
    ("original", "THE ORIGINAL SET, scored on 15: the set the system was tuned and developed against (steps 6 and 7b)"),
)


def load(out: Path, label: str) -> tuple[list[dict], list[dict]]:
    """The complete runs, in order, and the void ones."""
    documents = [json.loads(path.read_text()) for path in sorted(out.glob(f"{label}-*.json"))]
    return [d for d in documents if not d["void"]], [d for d in documents if d["void"]]


def _pct(value: float | None) -> str:
    return "none measured" if value is None else f"{value * 100:.1f}%"


def _tally_line(t: Tally) -> str:
    return f"{t.correct} of {t.measured}" + (f" ({t.not_measured} not measured)" if t.not_measured else "")


def _scored(records: list[dict], set_name: str, mode: str) -> list[dict]:
    return [r for r in records if r["set"] == set_name and r["mode"] == mode and r["score"] and not r["excluded"]]


def measures(runs: list[dict], set_name: str, mode: str) -> list[str]:
    lines = []
    for measure in ("tables", "path"):
        tallies = [tally([r["score"][measure] for r in _scored(run["records"], set_name, mode)]) for run in runs]
        combined = combine(tallies)
        lines.append(f"    {measure} correctness: mean {_pct(combined.mean)}, range {_pct(combined.low)} to "
                     f"{_pct(combined.high)}, over {combined.runs} runs")  # fmt: skip
        lines += [f"      run {run['run']}: {_tally_line(t)}" for run, t in zip(runs, tallies)]
    return lines


def _by_question(runs: list[dict], set_name: str, mode: str) -> dict[str, list[dict]]:
    found: dict[str, list[dict]] = {}
    for run in runs:
        for r in run["records"]:
            if r["set"] == set_name and r["mode"] == mode:
                found.setdefault(r["id"], []).append(r)
    return found


def stabilities(runs: list[dict], set_name: str) -> list[str]:
    lines = []
    per_mode = {mode: _by_question(runs, set_name, mode) for mode in MODES}
    for qid in per_mode["retrieval"]:
        excluded = per_mode["retrieval"][qid][0]["excluded"]
        parts = []
        for mode in MODES:
            records = per_mode[mode].get(qid, [])
            for measure in ("tables", "path"):
                s = stability([r["score"][measure] for r in records])
                parts.append(f"{mode} {measure} {s.correct}/{s.measured}" + ("" if s.stable else " UNSTABLE"))
            sqls = {r["sql"] for r in records}
            parts.append(f"{mode} distinct SQL {len(sqls)}")
        lines.append(f"    {set_name} {qid}{' (EXCLUDED)' if excluded else ''}: " + "; ".join(parts))
    return lines


def comparison(runs: list[dict], set_name: str) -> list[str]:
    """Per question, how many runs each mode got right, measure by measure:
    which mode did better, descriptively. No significance is claimed."""
    lines = []
    per_mode = {mode: _by_question(runs, set_name, mode) for mode in MODES}
    for measure in ("tables", "path"):
        better = {"retrieval": [], "full_schema": [], "same": []}
        for qid, records in per_mode["retrieval"].items():
            if records[0]["excluded"]:
                continue
            k = {mode: sum(r["score"][measure] == CORRECT for r in per_mode[mode].get(qid, [])) for mode in MODES}
            key = "same" if k["retrieval"] == k["full_schema"] else max(MODES, key=lambda m: k[m])
            better[key].append(qid)
        lines.append(f"    {measure}: retrieval better on {len(better['retrieval'])} "
                     f"({', '.join(better['retrieval']) or 'none'}); full schema better on "
                     f"{len(better['full_schema'])} ({', '.join(better['full_schema']) or 'none'}); "
                     f"the same on {len(better['same'])}")  # fmt: skip
    return lines


def _count(items: list[str]) -> str:
    counted: dict[str, int] = {}
    for item in items:
        counted[item] = counted.get(item, 0) + 1
    return ", ".join(f"{key} {value}" for key, value in sorted(counted.items())) or "none"


def causes(runs: list[dict], set_name: str, mode: str) -> list[str]:
    records = [r for run in runs for r in _scored(run["records"], set_name, mode)]
    not_path = [u.split(":")[0] for r in records if r["score"]["path"] == NOT_MEASURED
                for u in (r["extraction"] or {}).get("unchecked", [])]  # fmt: skip
    not_tables = [reason for r in records if r["score"]["tables"] == NOT_MEASURED
                  for reason in r["score"]["tables_unresolved"]]  # fmt: skip
    why = [r["score"]["why"] for r in records if r["score"]["why"] != "read"]
    return [
        f"    path not measured, by the instrument's reason: {_count(not_path)}",
        f"    tables not measured, by reason: {_count(not_tables)}",
        f"    decided before any SQL was read: {_count(why)}",
    ]


def recall_lines(runs: list[dict], set_name: str) -> list[str]:
    lines = []
    for which in ("shown", "tree"):
        matched = expected = count = 0
        for r in _scored(runs[0]["records"], set_name, "retrieval") if runs else []:
            if r["recall"]:
                matched += r["recall"][which]["matched"]
                expected += r["recall"][which]["expected"]
                count += r["recall"][which]["count"]
        recall = matched / expected if expected else None
        precision = matched / count if count else None
        lines.append(f"    tables {'shown to the model' if which == 'shown' else 'in the selected tree'}: "
                     f"recall {matched} of {expected} ({_pct(recall)}), precision {matched} of {count} "
                     f"({_pct(precision)})")  # fmt: skip
    same = all(
        [r["recall"] for r in _scored(run["records"], set_name, "retrieval")]
        == [r["recall"] for r in _scored(runs[0]["records"], set_name, "retrieval")]
        for run in runs
    )
    lines.append(f"    the same in every run: {'yes' if same else 'NO'}")
    return lines


def apart(runs: list[dict], set_name: str, mode: str) -> list[str]:
    records = [r for run in runs for r in run["records"] if r["set"] == set_name and r["mode"] == mode]
    attribute = [f"{r['id']} (run {r['run']}, path {r['score']['path']})" for r in records
                 if r["score"] and r["score"]["attribute_joins"]]  # fmt: skip
    today = [f"{r['id']} (run {r['run']})" for r in records if r["uses_current_date"]]
    empty = [f"{r['id']} (run {r['run']})" for r in records if r["empty_result"]]
    return [
        f"    attribute joins (item 66), beside the score: {len(attribute)}" + (f": {', '.join(attribute)}" if attribute else ""),
        f"    SQL taking today's date (item 60): {len(today)}" + (f": {', '.join(today)}" if today else ""),
        f"    empty results (item 60, outside the score): {len(empty)}" + (f": {', '.join(empty)}" if empty else ""),
    ]  # fmt: skip


def excluded_lines(runs: list[dict]) -> list[str]:
    lines = []
    for run in runs:
        for r in run["records"]:
            if r["excluded"]:
                s = r["score"]
                lines.append(f"    run {r['run']} {r['set']} {r['id']} {r['mode']}: {r['outcome']}; tables {s['tables']}, "
                             f"path {s['path']}; SQL tables {', '.join(s['sql_tables']) or 'none'}")  # fmt: skip
    return lines


def _spend(runs: list[dict], void: list[dict]) -> list[str]:
    every = runs + void
    if not every:
        return []
    last = max(every, key=lambda d: d["attempt"])
    calls = sum(len(r["attempts"]) for d in every for r in d["records"])
    tokens_in = sum(a["tokens_in"] for d in every for r in d["records"] for a in r["attempts"])
    cached = sum(a["tokens_cached"] for d in every for r in d["records"] for a in r["attempts"])
    tokens_out = sum(a["tokens_out"] for d in every for r in d["records"] for a in r["attempts"])
    cost = sum(r["cost_usd"] for d in every for r in d["records"])
    return [
        f"    model calls {calls}: {tokens_in} tokens in ({cached} cached), {tokens_out} out, ${cost:.6f}",
        f"    the ledger on step10 purposes after the last run: ${last['spend']['step10_usd_after']:.6f}"
        f" of a cap of ${last['settings']['cap_usd']:.2f}",
    ]


def runs_report(runs: list[dict], void: list[dict]) -> str:
    lines = ["STEP 10: THE EVALUATION", ""]
    lines.append(f"complete runs {len(runs)}; void runs {len(void)}")
    lines += [f"    void, attempt {d['attempt']} (run {d['run']}): {d['void']}" for d in void]
    if runs:
        s = runs[0]["settings"]
        lines.append(f"model {s['model']}, effort {s['reasoning_effort']}, temperature {s['temperature']}; "
                     f"retrieval alpha {s['alpha']}, cut {s['anchor_cut']}, cap {s['anchor_cap']}, margin {s['margin']}; "
                     f"schema {s['schema_sha256'][:12]}, preferences {s['preferences_hash']}")  # fmt: skip
    lines.append("Measured: whether the generated SQL read the expected tables and joined along the expected path.")
    lines.append("Not measured: whether the numbers it returned were right (FR-37's scope).")
    for set_name, title in SETS:
        lines += ["", title]
        for mode in MODES:
            lines.append(f"  {mode}")
            lines += measures(runs, set_name, mode)
            lines += causes(runs, set_name, mode)
            lines += apart(runs, set_name, mode)
        lines.append("  the two modes, question by question (runs correct)")
        lines += comparison(runs, set_name)
        lines.append("  retrieval recall and precision, a diagnostic outside the headline")
        lines += recall_lines(runs, set_name)
        lines.append("  each question over the runs (correct/measured)")
        lines += stabilities(runs, set_name)
    lines += ["", "QUESTION 14, deleted from scoring (item 54); its raw results, apart"]
    lines += excluded_lines(runs)
    lines += ["", "SPEND"] + _spend(runs, void)
    return "\n".join(lines) + "\n"


def pilot_report(runs: list[dict], void: list[dict]) -> str:
    lines = ["STEP 10: THE PILOT", "",
             "The eight development questions, both modes, once. They carry no expectations: nothing is scored.",
             f"complete runs {len(runs)}; void runs {len(void)}"]  # fmt: skip
    lines += [f"    void, attempt {d['attempt']}: {d['void']}" for d in void]
    for document in runs + void:
        checks = [r["cap_check"] for r in document["records"]]
        if checks:
            lines.append(f"cap checked before each of {len(checks)} answers; the largest spent before one "
                         f"${max(c['spent_usd'] for c in checks):.6f}, worst case per question "
                         f"${checks[0]['worst_case_usd']:.6f}, cap ${checks[0]['cap_usd']:.2f}")  # fmt: skip
        for r in document["records"]:
            prompt = r["prompt"]
            lines.append("")
            lines.append(f"{r['id']} {r['mode']} (position {r['position']}): {r['outcome']}")
            lines.append(f"    question     {r['question']}")
            if r["located"]:
                lines.append(f"    tree         {', '.join(r['located']['tree_tables']) or 'none'}; "
                             f"warnings {', '.join(r['located']['warnings']) or 'none'}")  # fmt: skip
            if prompt:
                lines.append(f"    prompt       {len(prompt['tables'])} tables, {prompt['chars']} characters, "
                             f"sha256 {prompt['sha256'][:12]}; join section {prompt['has_join_section']}")  # fmt: skip
            for a in r["attempts"]:
                lines.append(f"    attempt {a['number']}    {a['outcome']}; {a['tokens_in']} in ({a['tokens_cached']} "
                             f"cached), {a['tokens_out']} out, ${a['cost_usd']:.6f}, {a['duration_ms']} ms; "
                             f"returned model {a['returned_model']}")  # fmt: skip
            if r["transport_retries"]:
                lines.append(f"    retried      {len(r['transport_retries'])} transport or provider failures")
            if r["extraction"]:
                lines.append(f"    SQL tables   {', '.join(r['extraction']['tables']) or 'none'}")
                lines.append(f"    SQL joins    {'; '.join(r['extraction']['equalities']) or 'none'}")
                for group in r["extraction"]["cross_joins"]:
                    lines.append(f"    cross join   {' | '.join(group)}")
                if r["extraction"]["unchecked"]:
                    lines.append(f"    unchecked    {'; '.join(r['extraction']['unchecked'])}")
            if r["conformance_to_selected"]:
                lines.append(f"    against the joins selected: {r['conformance_to_selected']}")
            if r["execution"]:
                e = r["execution"]
                lines.append(f"    executed     {'ok' if e['ok'] else e['failure']}; {e['row_count']} rows"
                             f"{' (truncated)' if e['truncated'] else ''}, {e['duration_ms']} ms; rows sha256 "
                             f"{(e['rows_sha256'] or '')[:12]}; today's date used {r['uses_current_date']}")  # fmt: skip
    lines += ["", "SPEND"] + _spend(runs, void)
    by_mode = {}
    for d in runs + void:
        for r in d["records"]:
            m = by_mode.setdefault(r["mode"], [0, 0, 0, 0.0])
            m[0] += 1
            m[1] += sum(a["tokens_in"] for a in r["attempts"])
            m[2] += sum(a["tokens_out"] for a in r["attempts"])
            m[3] += r["cost_usd"]
    for mode, (n, tin, tout, cost) in sorted(by_mode.items()):
        lines.append(f"    {mode}: {n} answers, {tin} tokens in, {tout} out, ${cost:.6f}")
    return "\n".join(lines) + "\n"


def write_report(out: Path, label: str) -> Path:
    runs, void = load(out, label)
    text = pilot_report(runs, void) if label == "pilot" else runs_report(runs, void)
    path = out / "report.txt"
    path.write_text(text)
    return path
