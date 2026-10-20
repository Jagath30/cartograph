"""Step 10b's report, made from the kept run files and nothing else.

Everything in it is counted from eval/step10b/<pilot|runs>/*.json. Plain
lines, no tables. Step 10's report functions are imported wherever they
take the mode as given (`measures`, `causes`, `executions`, `apart`,
`excluded_lines`); where they are written for two modes, the three-mode
forms are here.

For the evaluation, in this order (the owner's ruling 9 at stop A):

  the fresh held-out set first, the headline: linked against full
    schema, then linked against retrieval, question by question; then
    the three modes side by side;
  then the original set (scored on 15), the first held-out set and dev2,
    each labelled for what it is, in the same form;
  for each set and mode: table and path correctness, each run's count and
    the mean of the runs with their range; declines by stage (linking,
    locate, the model); what left a denominator; execution outcomes;
    attribute joins, today's date and empty results; tokens of generation,
    and linking's tokens apart;
  recall and precision of the tables named by linking, in the tree and
    shown, a diagnostic outside the headline;
  each question's stability over the runs, the three modes side by side;
  question 14 apart; void runs; spend.

Tokens, not dollars, per set and mode: dollars depend on the provider's
cache and so on run order, and appear only as spend.
"""

from pathlib import Path

from app.core.eval_step10 import CORRECT, stability
from app.shell.step10 import create_exclusively
from app.step10_report import _by_question, _count, _pct, _scored, apart, causes, excluded_lines, executions, load, measures

# The order the modes are shown in: the headline's.
SHOWN = ("linked", "full_schema", "retrieval")
NAMES = {"linked": "linked", "full_schema": "full schema", "retrieval": "retrieval"}
SETS_B = (
    ("heldout2", "THE FRESH HELD-OUT SET (heldout2, 12 questions): the headline"),
    ("original", "THE ORIGINAL SET, scored on 15: the set the system was tuned and developed against (steps 6 and 7b)"),
    ("heldout1", "THE FIRST HELD-OUT SET (heldout1, 12 questions): run at step 10, now a development set"),
    ("dev2", "DEVELOPMENT SET 2 (dev2, 12 questions): seen by the planning chat, a development set"),
)


def _counted(runs: list[dict], set_name: str, mode: str) -> list[dict]:
    """Every answer of the set in this mode, question 14 left out."""
    return [r for run in runs for r in run["records"] if r["set"] == set_name and r["mode"] == mode and not r["excluded"]]


def versus(runs: list[dict], set_name: str, first: str, second: str) -> list[str]:
    """Per question, how many runs each mode got right, measure by measure:
    which did better, descriptively. No significance is claimed."""
    lines = []
    per_mode = {mode: _by_question(runs, set_name, mode) for mode in (first, second)}
    for measure in ("tables", "path"):
        better: dict[str, list[str]] = {first: [], second: [], "same": []}
        for qid, records in per_mode[first].items():
            if records[0]["excluded"]:
                continue
            k = {mode: sum(r["score"][measure] == CORRECT for r in per_mode[mode].get(qid, [])) for mode in better
                 if mode != "same"}  # fmt: skip
            key = "same" if k[first] == k[second] else max(k, key=lambda m: k[m])
            better[key].append(qid)
        lines.append(f"    {measure}: {NAMES[first]} better on {len(better[first])} "
                     f"({', '.join(better[first]) or 'none'}); {NAMES[second]} better on {len(better[second])} "
                     f"({', '.join(better[second]) or 'none'}); the same on {len(better['same'])}")  # fmt: skip
    return lines


def declines(records: list[dict]) -> list[str]:
    stages = [r["declined_at"] for r in records if r["declined_at"]]
    return [f"    declines by stage: at linking {stages.count('linking')}; at locate {stages.count('locate')}; "
            f"by the model {stages.count('model')}"]  # fmt: skip


def tokens_b(records: list[dict], mode: str) -> list[str]:
    """Generation's tokens; linking's apart, in linked mode."""
    n = len(records)
    if not n:
        return ["    tokens: no answers"]
    tin = sum(a["tokens_in"] for r in records for a in r["attempts"])
    tout = sum(a["tokens_out"] for r in records for a in r["attempts"])
    lines = [f"    tokens of generation, every answer: {tin} in, {tout} out, over {n} answers; "
             f"mean per answer {tin / n:.1f} in, {tout / n:.1f} out"]  # fmt: skip
    if mode == "linked":
        calls = [a for r in records if r["linking"] for a in r["linking"]["attempts"]]
        lin, lout = sum(a["tokens_in"] for a in calls), sum(a["tokens_out"] for a in calls)
        lines.append(f"    linking, apart: {lin} in, {lout} out, over {len(calls)} calls in {n} answers; "
                     f"mean per answer {lin / n:.1f} in, {lout / n:.1f} out")  # fmt: skip
    return lines


def recall_b(runs: list[dict], set_name: str) -> list[str]:
    """Summed over every run: linking may name other tables in another run."""
    lines = []
    whats = (("linked", "named", "tables named by linking"), ("linked", "tree", "linked, tables in the selected tree"),
             ("linked", "shown", "linked, tables shown to the model"),
             ("retrieval", "tree", "retrieval, tables in the selected tree"),
             ("retrieval", "shown", "retrieval, tables shown to the model"))  # fmt: skip
    for mode, which, label in whats:
        matched = expected = count = 0
        for r in _counted(runs, set_name, mode):
            if r["recall"] and which in r["recall"]:
                matched += r["recall"][which]["matched"]
                expected += r["recall"][which]["expected"]
                count += r["recall"][which]["count"]
        lines.append(f"    {label}: recall {matched} of {expected} ({_pct(matched / expected if expected else None)}), "
                     f"precision {matched} of {count} ({_pct(matched / count if count else None)}), "
                     f"over {len(runs)} runs")  # fmt: skip
    return lines


def identical_rows_b(runs: list[dict], set_name: str, first: str, second: str) -> list[str]:
    """Where both modes executed ok and neither result was truncated,
    whether the two rows hashes are equal. Question 14 left out."""
    equal = total = 0
    for run in runs:
        pair: dict[str, dict[str, dict]] = {}
        for r in run["records"]:
            if r["set"] == set_name and not r["excluded"]:
                pair.setdefault(r["id"], {})[r["mode"]] = r
        for modes in pair.values():
            executed = [modes.get(mode, {}).get("execution") for mode in (first, second)]
            if all(e and e["ok"] and not e["truncated"] for e in executed):
                total += 1
                equal += executed[0]["rows_sha256"] == executed[1]["rows_sha256"]
    return [f"    {NAMES[first]} against {NAMES[second]}: equal in {equal} of {total} question-runs where both "
            f"executed ok and neither was truncated"]  # fmt: skip


def stabilities_b(runs: list[dict], set_name: str) -> list[str]:
    lines = []
    per_mode = {mode: _by_question(runs, set_name, mode) for mode in SHOWN}
    for qid, first in per_mode["linked"].items():
        parts = []
        for mode in SHOWN:
            records = per_mode[mode].get(qid, [])
            for measure in ("tables", "path"):
                s = stability([r["score"][measure] for r in records])
                parts.append(f"{NAMES[mode]} {measure} {s.correct}/{s.measured}" + ("" if s.stable else " UNSTABLE"))
            parts.append(f"{NAMES[mode]} distinct SQL {len({r['sql'] for r in records})}")
        lines.append(f"    {set_name} {qid}{' (EXCLUDED)' if first[0]['excluded'] else ''}: " + "; ".join(parts))
    return lines


def mode_lines(runs: list[dict], set_name: str, mode: str) -> list[str]:
    lines = [f"  {mode}"]
    lines += measures(runs, set_name, mode)
    lines += declines(_counted(runs, set_name, mode))
    lines += causes(runs, set_name, mode)
    lines += executions(runs, set_name, mode)
    lines += apart(runs, set_name, mode)
    lines += tokens_b(_counted(runs, set_name, mode), mode)
    return lines


def set_lines(runs: list[dict], set_name: str, title: str) -> list[str]:
    lines = ["", title]
    lines.append("  linked against full schema, question by question (runs correct)")
    lines += versus(runs, set_name, "linked", "full_schema")
    lines.append("  linked against retrieval, question by question (runs correct)")
    lines += versus(runs, set_name, "linked", "retrieval")
    lines.append("  the three modes side by side")
    for mode in SHOWN:
        lines += mode_lines(runs, set_name, mode)
    lines.append("  identical rows, outside the score: whether two modes returned the same rows. This compares values "
                 "returned, not their correctness; a different column order counts as different.")  # fmt: skip
    lines += identical_rows_b(runs, set_name, "linked", "full_schema")
    lines += identical_rows_b(runs, set_name, "linked", "retrieval")
    lines.append("  recall and precision of tables, a diagnostic outside the headline")
    lines += recall_b(runs, set_name)
    lines.append("  each question over the runs (correct/measured)")
    lines += stabilities_b(runs, set_name)
    return lines


def _settings(runs: list[dict]) -> list[str]:
    if not runs:
        return []
    s = runs[0]["settings"]
    return [f"model {s['model']}, effort {s['reasoning_effort']}, temperature {s['temperature']}; linking's output cap "
            f"{s['link_max_output_tokens']} tokens; retrieval alpha {s['alpha']}, cut {s['anchor_cut']}, cap "
            f"{s['anchor_cap']}, margin {s['margin']}; schema {s['schema_sha256'][:12]}, preferences "
            f"{s['preferences_hash']}"]  # fmt: skip


def _spend(runs: list[dict], void: list[dict]) -> list[str]:
    every = runs + void
    if not every:
        return []
    last = max(every, key=lambda d: d["ended"])
    records = [r for d in every for r in d["records"]]
    generation = [a for r in records for a in r["attempts"]]
    linking = [a for r in records if r.get("linking") for a in r["linking"]["attempts"]]
    calls = generation + linking
    cost = sum(r["cost_usd"] for r in records)
    lines = [
        f"    model calls {len(calls)} ({len(generation)} generation, {len(linking)} linking): "
        f"{sum(a['tokens_in'] for a in calls)} tokens in ({sum(a['tokens_cached'] for a in calls)} cached), "
        f"{sum(a['tokens_out'] for a in calls)} out, ${cost:.6f}",
        f"    the ledger on step10b purposes after the last run: ${last['spend']['step10b_usd_after']:.6f}"
        f" of a cap of ${last['settings']['cap_usd']:.2f}",
    ]
    for document in every:
        checks = [r["cap_check"] for r in document["records"]]
        if checks:
            worst = max(c["worst_case_usd"] for c in checks)
            lines.append(f"    {document['file']}: the cap checked before each of {len(checks)} answers; the most "
                         f"spent before one ${max(c['spent_usd'] for c in checks):.6f}, the largest worst case "
                         f"${worst:.6f}")  # fmt: skip
    return lines


def runs_report_b(runs: list[dict], void: list[dict]) -> str:
    lines = ["STEP 10B: THE EVALUATION", ""]
    lines.append(f"complete runs {len(runs)}; void runs {len(void)}")
    lines += [f"    void, {d['file']} (run {d['run']}): {d['void']}" for d in void]
    lines += _settings(runs)
    lines.append("Measured: whether the generated SQL read the expected tables and joined along the expected path.")
    lines.append("Not measured: whether the numbers it returned were right (FR-37's scope).")
    for set_name, title in SETS_B:
        lines += set_lines(runs, set_name, title)
    lines += ["", "QUESTION 14, deleted from scoring (item 54); its raw results, apart"]
    lines += excluded_lines(runs)
    lines += ["", "SPEND"] + _spend(runs, void)
    return "\n".join(lines) + "\n"


def _names(tables) -> str:
    return ", ".join(tables) if tables else "none"


def linked_line(r: dict) -> str:
    """One linked answer: the tables named, the tree, the outcome, and for
    a scored question what was expected and how it scored."""
    named = r["linking"]["tables"] if r["linking"] else None
    tree = r["located"]["tree_tables"] if r["located"] else None
    outcome = r["outcome"] + (f" (at {r['declined_at']})" if r["declined_at"] else "")
    line = f"{r['id']} linked: named {_names(named)} | tree {_names(tree)} | {outcome}"
    if r["score"]:
        e = r["expected"]
        wanted = "a decline" if e["decline"] else _names(e["tables"]) + (
            f" and one of {', '.join(e['tables_one_of'])}" if e["tables_one_of"] else "")
        line += f" | expected {wanted}; tables {r['score']['tables']}, path {r['score']['path']}"
    return line


def _answer_lines(r: dict) -> list[str]:
    lines = ["", f"{r['set']} {r['id']} {r['mode']} (position {r['position']}): {r['outcome']}"
             + (f", declined at {r['declined_at']}" if r["declined_at"] else "")]  # fmt: skip
    lines.append(f"    question     {r['question']}")
    if r["linking"]:
        lines.append(f"    linking      named {_names(r['linking']['tables'])}; prompt {r['linking']['prompt']['chars']} "
                     f"characters, sha256 {r['linking']['prompt']['sha256'][:12]}")  # fmt: skip
        for a in r["linking"]["attempts"]:
            lines.append(f"    link {a['number']}       {a['outcome']}" + (f" ({a['fault']})" if a["fault"] else "")
                         + f"; {a['tokens_in']} in ({a['tokens_cached']} cached), {a['tokens_out']} out, "
                         f"{a['duration_ms']} ms; returned model {a['returned_model']}")  # fmt: skip
    if r["located"]:
        lines.append(f"    tree         {_names(r['located']['tree_tables'])}; joins "
                     f"{'; '.join(r['located']['tree_joins']) or 'none'}; warnings "
                     f"{', '.join(r['located']['warnings']) or 'none'}")  # fmt: skip
    if r["prompt"]:
        p = r["prompt"]
        lines.append(f"    prompt       {len(p['tables'])} tables, {p['chars']} characters, sha256 {p['sha256'][:12]}; "
                     f"join section {p['has_join_section']}")  # fmt: skip
    for a in r["attempts"]:
        lines.append(f"    attempt {a['number']}    {a['outcome']}; {a['tokens_in']} in ({a['tokens_cached']} cached), "
                     f"{a['tokens_out']} out, {a['duration_ms']} ms; returned model {a['returned_model']}")  # fmt: skip
    if r["transport_retries"]:
        lines.append(f"    retried      {len(r['transport_retries'])} transport or provider failures")
    if r["extraction"]:
        lines.append(f"    SQL tables   {_names(r['extraction']['tables'])}")
        lines.append(f"    SQL joins    {'; '.join(r['extraction']['equalities']) or 'none'}")
        lines += [f"    cross join   {' | '.join(group)}" for group in r["extraction"]["cross_joins"]]
        if r["extraction"]["unchecked"]:
            lines.append(f"    unchecked    {'; '.join(r['extraction']['unchecked'])}")
    if r["conformance_to_selected"]:
        lines.append(f"    against the joins selected: {r['conformance_to_selected']}")
    if r["score"]:
        lines.append(f"    score        tables {r['score']['tables']}, path {r['score']['path']} ({r['score']['why']})")
    if r["execution"]:
        e = r["execution"]
        lines.append(f"    executed     {'ok' if e['ok'] else e['failure']}; {e['row_count']} rows"
                     f"{' (truncated)' if e['truncated'] else ''}, {e['duration_ms']} ms; rows sha256 "
                     f"{(e['rows_sha256'] or '')[:12]}; today's date used {r['uses_current_date']}")  # fmt: skip
    return lines


def pilot_report_b(runs: list[dict], void: list[dict]) -> str:
    lines = ["STEP 10B: THE PILOT", "",
             "dev2 and the eight development questions, three modes, once. dev2 is scored, as a development set; "
             "d1 to d8 carry no expectations and are not scored.",
             f"complete runs {len(runs)}; void runs {len(void)}"]  # fmt: skip
    lines += [f"    void, {d['file']}: {d['void']}" for d in void]
    lines += _settings(runs + void)
    # A void pilot's answers are named by its file and counted in the spend,
    # and in nothing else: the complete pilot replaces it.
    complete = runs
    records = [r for d in complete for r in d["records"]]

    lines += ["", "LINKED MODE, QUESTION BY QUESTION: the tables linking named, the tables in the tree, the outcome"]
    lines += [linked_line(r) for r in records if r["mode"] == "linked"]

    lines += ["", "DEVELOPMENT SET 2 (dev2), scored"]
    lines.append("  linked against full schema, question by question (runs correct)")
    lines += versus(complete, "dev2", "linked", "full_schema")
    lines.append("  linked against retrieval, question by question (runs correct)")
    lines += versus(complete, "dev2", "linked", "retrieval")
    for mode in SHOWN:
        lines += mode_lines(complete, "dev2", mode)
    lines.append("  recall and precision of tables, a diagnostic")
    lines += recall_b(complete, "dev2")

    lines += ["", "THE DEVELOPMENT QUESTIONS d1 to d8, not scored"]
    for mode in SHOWN:
        dev = _counted(complete, "dev", mode)
        lines.append(f"  {mode}: outcomes {_count([r['outcome'] for r in dev])}")
        lines += declines(dev)
        lines += tokens_b(dev, mode)

    lines += ["", "EACH ANSWER"]
    for r in records:
        lines += _answer_lines(r)
    lines += ["", "SPEND"] + _spend(runs, void)
    return "\n".join(lines) + "\n"


def write_report_b(out: Path, label: str) -> Path:
    runs, void = load(out, label)
    text = pilot_report_b(runs, void) if label == "pilot" else runs_report_b(runs, void)
    path = out / "report.txt"
    create_exclusively(path, text)
    return path
