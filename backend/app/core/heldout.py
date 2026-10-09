"""The held-out evaluation set, parsed (DR-16, SRS threat T-04).

  parse_heldout   YAML text -> a HeldOutSet value. Reading the file is the
                  caller's job; this takes the text it read. Pure (DD-01).

THE RULE. backend/eval/heldout.yaml is never run, embedded, sent to a model
or used to choose any setting before step 10. Retrieval and prompt work may
not open it. Nothing in the application imports this module before then; it
exists now so that the file can be checked against the schema and frozen.

The set judges tables, joins and declines only, so a question here is
smaller than one of backend/eval/questions.yaml: no warning, no checks, no
step. A join is written exactly as it is there, and is the same value.

The parser is strict, as that one is. A field it does not understand, a
join outside the question's own tables, a decline that names a table: each
raises. A set that is quietly missing an expectation would report a better
score, and nothing downstream would say so.
"""

from dataclasses import dataclass

import yaml

from app.core.eval_set import ExpectedJoin

_TOP = {"provenance", "questions"}
_QUESTION = {"id", "question", "tables", "joins"}
_QUESTION_OPTIONAL = {"decline"}


@dataclass(frozen=True)
class HeldOutQuestion:
    id: str
    question: str
    tables: tuple[str, ...]
    joins: tuple[ExpectedJoin, ...]
    # The warehouse cannot answer it: a correct system declines.
    decline: bool


@dataclass(frozen=True)
class HeldOutSet:
    provenance: str
    questions: tuple[HeldOutQuestion, ...]


def parse_heldout(text: str) -> HeldOutSet:
    document = yaml.safe_load(text)
    if not isinstance(document, dict):
        raise ValueError("held-out set: the file must be a mapping")
    _exactly(document, _TOP, set(), "held-out set")

    entries = document["questions"]
    if not isinstance(entries, list) or not entries:
        raise ValueError("held-out set: questions must be a list")
    questions = tuple(_question(entry) for entry in entries)

    ids = [question.id for question in questions]
    if ids != [f"H{number}" for number in range(1, len(ids) + 1)]:
        raise ValueError(f"held-out set: question ids must run H1, H2, H3, ... in order, got {ids}")
    return HeldOutSet(_text(document["provenance"], "provenance"), questions)


def _question(entry: object) -> HeldOutQuestion:
    if not isinstance(entry, dict):
        raise ValueError(f"held-out set: a question must be a mapping, got {entry!r}")
    where = f"question {entry.get('id')!r}"
    _exactly(entry, _QUESTION, _QUESTION_OPTIONAL, where)

    if not isinstance(entry["id"], str):
        raise ValueError(f"held-out set: {where}: id must be text")
    # `decline` is written only where it is true, so a false one is a
    # mistake and not a second way of saying nothing.
    if entry.get("decline", True) is not True:
        raise ValueError(f"held-out set: {where}: decline, where present, must be true")
    joins = entry["joins"]
    if not isinstance(joins, list):
        raise ValueError(f"held-out set: {where}: joins must be a list")

    question = HeldOutQuestion(
        id=entry["id"],
        question=_text(entry["question"], f"{where}: question"),
        tables=_names(entry["tables"], f"{where}: tables"),
        joins=tuple(_join(join, where) for join in joins),
        decline="decline" in entry,
    )

    # A declined question has nothing to locate, and an answered one has
    # something. Either contradiction would misreport the question.
    if question.decline != (not question.tables):
        raise ValueError(
            f"held-out set: {where}: decline is {question.decline} but it names {len(question.tables)} tables"
        )
    outside = sorted(
        {table for join in question.joins for table in (join.from_table, join.to_table)} - set(question.tables)
    )
    if outside:
        raise ValueError(f"held-out set: {where}: a join uses {outside}, not among the question's tables")
    return question


def _join(entry: object, where: str) -> ExpectedJoin:
    if not isinstance(entry, dict) or set(entry) != {"from", "to"}:
        raise ValueError(f"held-out set: {where}: a join needs exactly `from` and `to`, got {entry!r}")

    sides = []
    for side in ("from", "to"):
        value = entry[side]
        names = [value] if isinstance(value, str) else value
        if not isinstance(names, list) or not names:
            raise ValueError(f"held-out set: {where}: `{side}` must be table.column or a list of them, got {value!r}")
        tables, columns = set(), []
        for name in names:
            parts = name.split(".") if isinstance(name, str) else []
            if len(parts) != 2 or not all(parts):
                raise ValueError(f"held-out set: {where}: `{side}` must be table.column, got {name!r}")
            tables.add(parts[0])
            columns.append(parts[1])
        if len(tables) != 1 or len(set(columns)) != len(columns):
            raise ValueError(
                f"held-out set: {where}: the columns of `{side}` must be different columns of one table, got {value!r}"
            )
        sides.append((tables.pop(), tuple(columns)))

    (from_table, from_columns), (to_table, to_columns) = sides
    if len(from_columns) != len(to_columns):
        raise ValueError(f"held-out set: {where}: `from` and `to` must list the same number of columns, got {entry!r}")
    return ExpectedJoin(from_table, from_columns, to_table, to_columns)


def _names(value: object, where: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(name, str) and name for name in value):
        raise ValueError(f"held-out set: {where} must be a list of names, got {value!r}")
    if len(set(value)) != len(value):
        raise ValueError(f"held-out set: {where} names something twice: {value!r}")
    return tuple(value)


def _text(value: object, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"held-out set: {where} must be text, got {value!r}")
    return " ".join(value.split())


def _exactly(mapping: dict, required: set[str], optional: set[str], where: str) -> None:
    missing = sorted(required - set(mapping))
    unknown = sorted(str(key) for key in mapping if key not in required | optional)
    if missing or unknown:
        raise ValueError(f"held-out set: {where}: missing {missing}, not understood {unknown}")
