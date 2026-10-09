"""Print one trace, and reduce one to plain data.

No logic lives here: everything printed was decided by a stage and is read
off the trace the orchestrator assembled.
"""

from app.orchestrator import Trace
from app.retrieval_report import joins_of, line

FIRST_ROWS = 5


def _edge(edge) -> str:
    return " and ".join(f"{a[0]}.{a[1]} = {b[0]}.{b[1]}" for a, b in edge)


def _equality(equality) -> str:
    how = (" (outer join)" if equality.outer else "") + (" (through a UNION)" if equality.union else "")
    return f"{equality.left[0]}.{equality.left[1]} = {equality.right[0]}.{equality.right[1]}{how}"


def print_trace(trace: Trace) -> None:
    line("question", trace.question)
    line("outcome", trace.outcome + (f"  [{trace.code}]" if trace.code else ""))
    if trace.message:
        line("", trace.message)

    located = trace.located
    tree = located.tree
    line("anchors", ", ".join(tree.anchors) or "none")
    if trace.prompt is not None:
        line("in prompt", ", ".join(trace.prompt.tables) + f"  ({len(trace.prompt.text)} characters)")
        for position, join in enumerate(joins_of(tree.edges)):
            line("selected" if position == 0 else "", join)
        if not tree.edges:
            line("selected", "no joins: one table")
    codes = sorted(located.warning_codes)
    line("warnings", ", ".join(codes) or "none")
    if not located.declined:
        for warning in located.explanation.warnings:
            line("", f"{warning.code}: {warning.text[:300]}")
        if located.explanation.close_calls:
            line("close calls", str(len(located.explanation.close_calls)))

    for attempt in trace.attempts:
        reply = attempt.reply
        line(
            f"attempt {attempt.number}",
            f"{attempt.outcome}; asked {reply.requested_model}, answered {reply.returned_model}; "
            f"{reply.tokens_in} in ({reply.tokens_cached} cached), {reply.tokens_out} out; "
            f"${reply.cost_usd:.6f}; {reply.duration_ms} ms",
        )
        for finding in attempt.findings:
            line("", f"finding: {finding}")
        if attempt.tables_outside_prompt:
            line("", f"tables not in the prompt: {', '.join(attempt.tables_outside_prompt)}")
        if attempt.sql is not None and attempt.outcome != "accepted":
            line("", f"sql: {' '.join(attempt.sql.split())}")

    if trace.validation is not None:
        validation = trace.validation
        line("validation", f"syntax {validation.syntax}; read_only {validation.read_only}; references {validation.references}")
    if trace.conformance is not None:
        line("conformance", trace.conformance.outcome)
        for finding in trace.conformance.findings:
            line("", finding)
        for position, equality in enumerate(trace.actual_edges):
            line("actual" if position == 0 else "", _equality(equality))
        if not trace.actual_edges:
            line("actual", "no joins")
    if trace.execution is not None:
        execution = trace.execution
        print("    sql")
        for text in execution.executed.splitlines():
            print(f"               {text}")
        line("hashes", f"validated {trace.validated_sha256[:16]}, executed {trace.executed_sha256[:16]}: "
             + ("the same text" if trace.validated_sha256 == trace.executed_sha256 else "DIFFERENT TEXT"))  # fmt: skip
        if execution.ok:
            line("rows", f"{execution.row_count}" + (f", TRUNCATED at the cap of {execution.row_cap}" if execution.truncated else "")
                 + f"; {execution.duration_ms} ms; statement_timeout {execution.statement_timeout}")  # fmt: skip
            line("", " | ".join(execution.columns))
            for row in execution.rows[:FIRST_ROWS]:
                line("", " | ".join("NULL" if value is None else str(value) for value in row))
        else:
            line("error", f"{execution.failure} {execution.sqlstate or ''}: {execution.message}")
    line("timings", "; ".join(f"{name} {ms} ms" for name, ms in trace.timings.items()))
    line("cost", f"{trace.tokens_in} tokens in, {trace.tokens_out} out, ${trace.cost_usd:.6f}, over {len(trace.attempts)} model calls")


def summary(trace: Trace) -> dict:
    """What a script needs of a trace, as plain data."""
    execution = trace.execution
    return {
        "trace_version": trace.trace_version,
        "question": trace.question,
        "outcome": trace.outcome,
        "code": trace.code,
        "message": trace.message,
        "schema_hash": trace.schema_ref[1],
        "anchors": list(trace.located.tree.anchors),
        "prompt_tables": list(trace.prompt.tables) if trace.prompt else [],
        "warnings": sorted(trace.located.warning_codes),
        "selected_edges": [_edge(edge) for edge in trace.selected_edges],
        "actual_edges": [
            {"left": list(e.left), "right": list(e.right), "outer": e.outer, "union": e.union} for e in trace.actual_edges
        ],
        "actual_classes": [sorted(list(column) for column in members) for members in trace.actual_classes],
        "diverged": trace.diverged,
        "attempts": [
            {
                "number": attempt.number,
                "outcome": attempt.outcome,
                "requested_model": attempt.reply.requested_model,
                "returned_model": attempt.reply.returned_model,
                "tokens_in": attempt.reply.tokens_in,
                "tokens_out": attempt.reply.tokens_out,
                "cost_usd": attempt.reply.cost_usd,
                "findings": list(attempt.findings),
                "tables_outside_prompt": list(attempt.tables_outside_prompt),
                "sql": attempt.sql,
            }
            for attempt in trace.attempts
        ],
        "validation": None
        if trace.validation is None
        else {
            "syntax": trace.validation.syntax,
            "read_only": trace.validation.read_only,
            "references": trace.validation.references,
        },
        "conformance": trace.conformance.outcome if trace.conformance else None,
        "conformance_findings": list(trace.conformance.findings) if trace.conformance else [],
        "sql": execution.executed if execution else None,
        "validated_sha256": trace.validated_sha256,
        "executed_sha256": trace.executed_sha256,
        "row_count": execution.row_count if execution else None,
        "truncated": execution.truncated if execution else None,
        "columns": list(execution.columns) if execution else [],
        "first_rows": [[None if v is None else str(v) for v in row] for row in execution.rows[:FIRST_ROWS]] if execution else [],
        "timings_ms": trace.timings,
        "tokens_in": trace.tokens_in,
        "tokens_out": trace.tokens_out,
        "cost_usd": trace.cost_usd,
    }

