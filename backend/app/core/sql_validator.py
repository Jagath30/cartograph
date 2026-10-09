"""SqlValidator: is this text one read-only SELECT over things the schema
holds? (FR-18 to FR-21, NFR-08, criterion 4.)

Pure (DD-01): it parses and it judges, and it never executes anything.
It was written, tested on hand-written SQL and committed before any model
had produced SQL for it to judge.

THREE CHECKS, in the trace's words (Design section 04):

    syntax       the text parses, as exactly one statement
    read_only    that statement is a SELECT and nothing in it writes,
                 locks, or calls a function known for a side effect
    references   every table and column it names is in the schema, and
                 every function it calls is on the allowlist

WHAT ENDS THE QUESTION AND WHAT MAY BE TRIED AGAIN (DD-15, and the owner's
ruling at step 7). Everything that fails `read_only` is TERMINAL: the
model is not asked again, because asking again would be coaching it past a
safety check. A syntax error, an unknown table or column, and a function
that is merely not on the allowlist are RETRYABLE: slips, each with a
message the model can act on.

THE READ-ONLY ROLE IS THE SECOND LINE, NEVER THE FIRST (NFR-07). It would
refuse a write. It would not refuse pg_sleep.

THE ONLY WAY TO A ValidatedSql IS THROUGH validate(). The executor accepts
nothing else (NFR-08), and the text inside is the text that was handed in,
unchanged by a byte: this module reads a parse of it and never writes one
back.
"""

from dataclasses import dataclass
from typing import Literal

from sqlglot import exp

from app.core.sql_reading import (
    Reading,
    Schema,
    SqlNotParsed,
    own_nodes,
    parse_statements,
    read,
    resolve,
)

Check = Literal["syntax", "read_only", "references"]
Verdict = Literal["pass", "fail", "not_run"]

_SEAL = object()


@dataclass(frozen=True)
class ValidatedSql:
    """SQL that passed every check. Made by validate() and by nothing else."""

    text: str
    _seal: object = None

    def __post_init__(self) -> None:
        if self._seal is not _SEAL:
            raise TypeError("a ValidatedSql is made by validate() and by nothing else (NFR-08)")


@dataclass(frozen=True)
class Finding:
    check: Check
    code: str
    # True: the question ends here and the model is not asked again.
    terminal: bool
    message: str


@dataclass(frozen=True)
class Validation:
    syntax: Verdict
    read_only: Verdict
    references: Verdict
    findings: tuple[Finding, ...]
    validated: ValidatedSql | None

    @property
    def passed(self) -> bool:
        return self.validated is not None

    @property
    def terminal(self) -> bool:
        return any(finding.terminal for finding in self.findings)

    @property
    def retryable(self) -> bool:
        return bool(self.findings) and not self.terminal


# Functions called for what they do and not for what they return: they
# wait, write, read files, reach other servers, take locks, signal other
# sessions, change settings, advance sequences, or run SQL handed to them
# as text. Calling one ends the question, as a write does.
DANGEROUS_FUNCTIONS = frozenset(
    {
        "set_config",
        "nextval",
        "setval",
        "pg_notify",
        "pg_export_snapshot",
        "pg_promote",
    }
)
DANGEROUS_PREFIXES = (
    "pg_sleep",
    "pg_read_",
    "pg_ls_",
    "pg_stat_file",
    "pg_file_",
    "lo_",
    "dblink",
    "pg_advisory_",
    "pg_try_advisory_",
    "pg_terminate_",
    "pg_cancel_",
    "pg_reload_",
    "pg_rotate_",
    "pg_switch_",
    "pg_create_",
    "pg_drop_",
    "pg_replication_",
    "pg_logical_",
    "pg_wal_",
    "pg_backup_",
    "pg_stat_reset",
    "query_to_xml",
    "table_to_xml",
    "cursor_to_xml",
    "schema_to_xml",
    "database_to_xml",
)

# What a question about a warehouse needs: aggregates, window functions,
# arithmetic, text, dates, conditions and casts. Named by the class sqlglot
# parses each into, which is the same for every spelling of it (`ceil` and
# `ceiling`, `substr` and `substring`); tests/core/test_sql_validator.py
# holds the Postgres spellings. A function not here is refused with a
# message naming it, and the model may try again.
ALLOWED_FUNCTIONS = frozenset(
    {
        # aggregates
        "Count", "Sum", "Avg", "Min", "Max", "Stddev", "StddevPop", "StddevSamp", "Variance",
        "VariancePop", "Corr", "CovarPop", "CovarSamp", "GroupConcat", "ArrayAgg", "LogicalAnd",
        "LogicalOr", "PercentileCont", "PercentileDisc", "Mode", "AnyValue", "Grouping", "Median",
        # window
        "RowNumber", "Rank", "DenseRank", "PercentRank", "CumeDist", "Ntile", "Lag", "Lead",
        "FirstValue", "LastValue", "NthValue",
        # arithmetic
        "Abs", "Ceil", "Floor", "Round", "Trunc", "Sqrt", "Cbrt", "Pow", "Exp", "Ln", "Log", "Sign",
        "Greatest", "Least", "WidthBucket", "Pi",
        # conditions and casts
        "Case", "If", "Coalesce", "Nullif", "Cast", "TryCast", "Exists", "Array",
        # operators that sqlglot happens to class as functions
        "And", "Or", "Xor", "RegexpILike",
        # text
        "Upper", "Lower", "Length", "Substring", "Trim", "Concat", "ConcatWs", "Left", "Right",
        "Replace", "Initcap", "StrPosition", "Pad", "SplitPart", "Reverse", "Repeat", "Translate",
        "RegexpLike", "RegexpReplace", "ToNumber",
        # dates
        "Extract", "TimestampTrunc", "DateTrunc", "CurrentDate", "CurrentTimestamp", "TimeToStr",
        "StrToDate", "StrToTime", "Date",
    }
)  # fmt: skip
# `all` is the ALL of `= ALL (...)`, which sqlglot reads as a call.
ALLOWED_BY_NAME = frozenset({"age", "make_date", "every", "all"})


def validate(sql: str, schema: Schema) -> Validation:
    """Judge one text. The schema is the whole snapshot, not the part of
    it a prompt showed (FR-20 says "the schema")."""
    try:
        statements = parse_statements(sql)
    except SqlNotParsed as error:
        finding = Finding("syntax", "not_parsed", False, f"The SQL could not be parsed: {error}")
        return Validation("fail", "not_run", "not_run", (finding,), None)
    if not statements:
        finding = Finding("syntax", "empty", False, "There is no SQL statement in the reply.")
        return Validation("fail", "not_run", "not_run", (finding,), None)

    writes = _read_only(statements)
    if writes:
        # Nothing further is said about a statement that tried to write:
        # its references are not this module's to help with.
        return Validation("pass", "fail", "not_run", writes, None)

    statement = statements[0]
    reading = read(statement, schema)
    dangerous, unlisted = _functions(statement)
    if dangerous:
        return Validation("pass", "fail", "not_run", dangerous, None)

    faults = _references(reading, schema) + unlisted
    if faults:
        return Validation("pass", "pass", "fail", faults, None)
    return Validation("pass", "pass", "pass", (), ValidatedSql(sql, _SEAL))


def feedback(validation: Validation) -> str:
    """What the model is told before it is asked again. Only ever built
    from a retryable validation."""
    lines = [finding.message for finding in validation.findings]
    return "The SQL was not accepted:\n" + "\n".join(f"- {line}" for line in lines)


# --------------------------------------------------------------------------
# read_only
# --------------------------------------------------------------------------


def _terminal(code: str, message: str) -> Finding:
    return Finding("read_only", code, True, message)


# Statements, and the forms inside one, that are not a read. sqlglot parses
# anything it does not recognise as a Command, which is refused with them.
_NOT_A_READ = (
    exp.DML, exp.DDL, exp.Command, exp.Copy, exp.Set, exp.Drop, exp.Alter, exp.TruncateTable,
    exp.Transaction, exp.Commit, exp.Rollback, exp.Grant, exp.Use, exp.Into, exp.Lock,
)  # fmt: skip


def _is_read(node: exp.Expression) -> bool:
    while isinstance(node, exp.Subquery | exp.Paren) and node.this is not None:
        node = node.this
    if isinstance(node, exp.SetOperation):
        return _is_read(node.left) and _is_read(node.right)
    return isinstance(node, exp.Select)


def _read_only(statements: tuple[exp.Expression, ...]) -> tuple[Finding, ...]:
    if len(statements) > 1:
        return (
            _terminal(
                "multiple_statements",
                f"The text holds {len(statements)} statements. Exactly one SELECT is accepted.",
            ),
        )
    statement = statements[0]
    findings: list[Finding] = []
    if not _is_read(statement):
        findings.append(
            _terminal("not_a_select", f"The statement is a {_kind(statement)}, not a SELECT. Only a SELECT is accepted.")
        )
    for node in statement.walk():
        if node is statement:
            continue
        if isinstance(node, exp.Into):
            findings.append(_terminal("select_into", "SELECT ... INTO creates a table. Only a read is accepted."))
        elif isinstance(node, exp.Lock):
            findings.append(_terminal("locking", "FOR UPDATE and FOR SHARE take row locks. Only a read is accepted."))
        elif isinstance(node, _NOT_A_READ) or (isinstance(node, exp.CTE) and not _is_read(node.this)):
            inner = node.this if isinstance(node, exp.CTE) else node
            findings.append(
                _terminal("write", f"The statement contains a {_kind(inner)}. Nothing that writes is accepted, in a CTE or anywhere.")
            )
    return _once(findings)


def _kind(node: exp.Expression) -> str:
    return type(node).__name__.upper()


def _once(findings: list[Finding]) -> tuple[Finding, ...]:
    seen: dict[tuple[str, str], Finding] = {}
    for finding in findings:
        seen.setdefault((finding.code, finding.message), finding)
    return tuple(seen.values())


# --------------------------------------------------------------------------
# functions
# --------------------------------------------------------------------------


def _functions(statement: exp.Expression) -> tuple[tuple[Finding, ...], tuple[Finding, ...]]:
    dangerous: list[Finding] = []
    unlisted: list[Finding] = []
    for node in statement.find_all(exp.Func):
        if isinstance(node, exp.Anonymous):
            name = node.name.lower()
            if name in DANGEROUS_FUNCTIONS or name.startswith(DANGEROUS_PREFIXES):
                dangerous.append(
                    _terminal(
                        "dangerous_function",
                        f"The function {name}() is called for its side effect or to pass time. It is never accepted.",
                    )
                )
                continue
            allowed = name in ALLOWED_BY_NAME
        else:
            name = node.sql_name().lower()
            allowed = type(node).__name__ in ALLOWED_FUNCTIONS
        if not allowed:
            unlisted.append(
                Finding(
                    "references",
                    "function_not_allowed",
                    False,
                    f"The function {name}() is not one this system accepts. Use ordinary aggregate, "
                    "arithmetic, text, date and conditional functions.",
                )
            )
    return _once(dangerous), _once(unlisted)


# --------------------------------------------------------------------------
# references
# --------------------------------------------------------------------------


def _fault(code: str, message: str) -> Finding:
    return Finding("references", code, False, message)


def _references(reading: Reading, schema: Schema) -> tuple[Finding, ...]:
    findings: list[Finding] = []
    for select in reading.selects:
        for source in select.sources:
            if source.kind != "table":
                continue
            if source.qualifier not in (None, "public"):
                findings.append(
                    _fault(
                        "schema_not_allowed",
                        f"{source.qualifier}.{source.table} is outside the warehouse's schema. Only its own tables may be read.",
                    )
                )
            elif source.table not in schema:
                findings.append(_fault("unknown_table", f"There is no table named {source.table}."))

        named = {(projection.alias or "").lower() for projection in select.node.expressions} - {""}
        for node in own_nodes(select.node):
            if not isinstance(node, exp.Column):
                continue
            resolved = resolve(node, select, schema)
            if resolved.kind in ("found", "opaque"):
                continue
            written = node.sql(dialect="postgres")
            if resolved.kind == "ambiguous":
                findings.append(
                    _fault(
                        "ambiguous_column",
                        f"The column {written} is in more than one table here ({', '.join(resolved.candidates)}). Qualify it.",
                    )
                )
            elif not node.table and node.name.lower() in named and node.find_ancestor(exp.Order, exp.Group, exp.Having):
                # ORDER BY and GROUP BY may name an output column.
                continue
            elif _table_is_unknown(node, select, schema):
                # Already reported as the table; not twice.
                continue
            else:
                findings.append(_fault("unknown_column", f"There is no column {written} in the tables this query reads."))
    return _once(findings)


def _table_is_unknown(column: exp.Column, select, schema: Schema) -> bool:
    """True when the column is qualified by a source whose table the schema
    does not hold, so that one mistake is one finding."""
    if not column.table:
        return False
    scope = select
    while scope is not None:
        for source in scope.sources:
            if source.alias == column.table.lower():
                return source.kind == "table" and (
                    source.table not in schema or source.qualifier not in (None, "public")
                )
        scope = scope.parent
    return False
