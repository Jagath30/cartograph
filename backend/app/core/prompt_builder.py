"""PromptBuilder: the exact text sent to the model, and the reading of
what it sends back (FR-15, FR-17, FR-42, DD-14 as amended at step 7).

Pure (DD-01): tables, joins and a question in, text out. What it returns
is stored in the trace whole, so that what was sent can be audited.

THE PROMPT HAS THREE PARTS, AND THEY ARE KEPT APART SO THAT STEP 10 CAN
USE THE FIRST UNCHANGED IN BOTH ITS MODES:

    GENERAL_RULES   the system message. One SELECT, qualified columns,
                    JSON only, and how to say the question cannot be
                    answered. Nothing in it mentions joins or a path.
    the tables      CREATE TABLE statements, every column including the
                    keys, the readable name of each as a comment (DD-08).
                    No REFERENCES clauses: the join section is the only
                    statement of how tables join.
    the joins       the selected tree as explicit conditions, a two-column
                    key on one line. Everything said about joins is said
                    here, with the joins, and is absent when there is no
                    selected path (full-schema mode).

THE MODEL REPLIES IN JSON WITH TWO FIELDS AND EXPLAINS NOTHING (DD-14):
`status`, which is "sql" or "not_answerable", and `sql`. There is no field
for a reason: an explanation the system cannot check is worse than none.
A reply that contradicts itself -- SQL beside "not_answerable", or "sql"
with none -- is malformed.

WHAT "NOT ANSWERABLE" MEANS HERE. The model sees the tables retrieved,
not the warehouse. Its "not_answerable" says the question cannot be
answered from the tables it was shown, and the system never words it as
"from this schema".
"""

import json
from dataclasses import dataclass
from typing import Literal

from app.core.path_finder import Join
from app.core.snapshot import SchemaSnapshot

GENERAL_RULES = """\
You write one PostgreSQL query that answers a question about a data warehouse.

You are given the tables you may use, as CREATE TABLE statements. The comment above each table and after each column says what it holds.

Rules:
- Write exactly one SELECT statement. It may begin with WITH. Never write anything that changes data.
- Use only the tables and columns given. Do not use any other table or column, even one you know of.
- Qualify every column with its table name or its alias.
- Do not put comments in the SQL.
- If the question cannot be answered from the tables given, do not guess and do not answer a different question.

Reply with one JSON object and nothing else:
  {"status": "sql", "sql": "<the query>"}
or, when the question cannot be answered from the tables given:
  {"status": "not_answerable", "sql": ""}
Do not explain."""

JOIN_RULES = """\
Join conditions. These are the joins selected for this question. Wherever the query joins two of these tables, join them by exactly these conditions; a line with two conditions joined by AND is one join and needs both. Use only the tables the question needs: a table listed above need not appear in the query."""

# The shape the provider is asked to hold the reply to.
REPLY_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["sql", "not_answerable"]},
        "sql": {"type": "string"},
    },
    "required": ["status", "sql"],
    "additionalProperties": False,
}
REPLY_NAME = "sql_reply"


@dataclass(frozen=True)
class Prompt:
    system: str
    user: str
    # What went into it, for the trace.
    tables: tuple[str, ...]
    has_join_section: bool

    @property
    def text(self) -> str:
        """The whole of what was sent, as one text (FR-17)."""
        return f"{self.system}\n\n{self.user}"


@dataclass(frozen=True)
class Reply:
    kind: Literal["sql", "not_answerable", "malformed"]
    sql: str | None = None
    # For a malformed reply: what was wrong with it, in fixed words.
    fault: str | None = None


def render_tables(snapshot: SchemaSnapshot, tables: tuple[str, ...]) -> str:
    """CREATE TABLE for each named table, in the order given."""
    known = {table.name: table for table in snapshot.tables}
    keys = {key.table: key.columns for key in snapshot.primary_keys}
    blocks = []
    for name in tables:
        table = known[name]
        columns = [column for column in snapshot.columns if column.table == name]
        lines = []
        for position, column in enumerate(columns):
            last = position == len(columns) - 1 and name not in keys
            said = (column.readable or column.name).split(" — ", 1)[-1]
            lines.append(f"  {column.name} {column.data_type}{'' if last else ','}  -- {said}")
        if name in keys:
            lines.append(f"  PRIMARY KEY ({', '.join(keys[name])})")
        blocks.append(f"-- {table.readable or name}\nCREATE TABLE {name} (\n" + "\n".join(lines) + "\n);")
    return "\n\n".join(blocks)


def render_joins(joins: tuple[Join, ...]) -> str:
    """The selected joins as conditions, one join to a line."""
    lines = [
        "  " + " AND ".join(
            f"{join.fk_table}.{fk} = {join.pk_table}.{pk}" for fk, pk in zip(join.fk_columns, join.pk_columns)
        )
        for join in joins
    ]  # fmt: skip
    return JOIN_RULES + "\n" + "\n".join(sorted(lines))


def build_prompt(
    question: str,
    snapshot: SchemaSnapshot,
    tables: tuple[str, ...],
    joins: tuple[Join, ...] | None,
) -> Prompt:
    """`joins` is the selected tree's joins, or None when there is no
    selected path at all (step 10's full-schema mode): then the prompt has
    no join section and says nothing about joins."""
    parts = ["Tables:\n\n" + render_tables(snapshot, tables)]
    if joins:
        parts.append(render_joins(joins))
    parts.append(f"Question: {question.strip()}")
    return Prompt(GENERAL_RULES, "\n\n".join(parts), tables, bool(joins))


def retry_message(feedback: str) -> str:
    """What follows a reply that validation refused for a fixable fault."""
    return f"{feedback}\n\nReply again in the same JSON form, with a corrected query."


def parse_reply(text: str) -> Reply:
    """The model's reply, read strictly. Nothing is repaired: no code
    fence stripped, no SQL dug out of prose."""
    try:
        reply = json.loads(text)
    except ValueError:
        return Reply("malformed", fault="the reply is not JSON")
    if not isinstance(reply, dict) or set(reply) != {"status", "sql"}:
        return Reply("malformed", fault="the reply does not have exactly the fields status and sql")
    status, sql = reply["status"], reply["sql"]
    if not isinstance(sql, str) or status not in ("sql", "not_answerable"):
        return Reply("malformed", fault="status or sql is not of the form asked for")
    if status == "sql":
        if not sql.strip():
            return Reply("malformed", fault="status is sql and there is no SQL")
        return Reply("sql", sql=sql)
    if sql.strip():
        return Reply("malformed", fault="status is not_answerable and SQL was given all the same")
    return Reply("not_answerable")
