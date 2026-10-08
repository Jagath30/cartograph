"""Explainer: what the PathFinder found, said so a person can check it
(FR-40, FR-45, DD-06, DD-21).

Pure (DD-01): a PathResult and the graph it came from in, an Explanation out.
The graph is needed only for readable names (DD-08).

The result is structured data -- chosen path, alternatives, reason,
warnings, and where every edge came from -- and each path also carries one
plain-English sentence, composed here and nowhere else. DD-06: the sentence
is written once in the core and stored, so that no display has to
understand what a foreign key is.

Everything said here is derived from facts this system holds: the edges,
their direction, the rule that fired. Nothing is narrated by a model
(DD-14).

WHEN A WARNING FIRES (DD-21):
  arbitrary_choice        several shortest routes tied and nothing declared
                          which was meant. The warning names the routes NOT
                          taken, in words: a valid, short, wrong path must be
                          visible, not merely absent from a list.
  preference_not_applied  the overlay declares a preference for this pair
                          that did not single out one of the tied routes. It
                          never silences arbitrary_choice.
  many_to_many            the chosen route joins two "many" sides through one
                          table. Names the table and what happens to the rows.
  no_path                 nothing connects the two tables within the limit.
                          Reported, not repaired (DD-10).

A tie the question's own wording decided (DD-12 as amended) does not warn
either: that selection had a basis. Its reason gives the scores and names
the routes not taken, so a reader can still say "no, I meant the other".

Alternatives that merely exist do not warn. If the shortest path stood
alone, the reason says how many routes there were and that is all.
"""

from dataclasses import dataclass
from typing import Literal

import networkx as nx

from app.core.path_finder import MANY_TO_ONE, Join, Path, PathResult, Rule
from app.core.snapshot import Source

WarningCode = Literal["arbitrary_choice", "preference_not_applied", "many_to_many", "no_path"]


@dataclass(frozen=True)
class ExplainedJoin:
    fk_table: str
    fk_columns: tuple[str, ...]
    pk_table: str
    pk_columns: tuple[str, ...]
    walked: str
    # Provenance (FR-43): declared by the database, or asserted in the overlay.
    source: Source
    constraint: str | None
    # For an overlay edge, the reason its author gave for asserting it.
    note: str | None
    description: str


@dataclass(frozen=True)
class ExplainedPath:
    id: str
    tables: tuple[str, ...]
    length: int
    joins: tuple[ExplainedJoin, ...]
    many_to_many_at: tuple[str, ...]
    # True for an alternative exactly as short as the chosen path: one the
    # rule could not tell apart from it.
    tied_with_chosen: bool
    description: str


@dataclass(frozen=True)
class Reason:
    rule: Rule | None
    text: str


@dataclass(frozen=True)
class PathWarning:
    code: WarningCode
    text: str
    # The tables or path ids the warning is about, for a display to point at.
    about: tuple[str, ...] = ()


@dataclass(frozen=True)
class Explanation:
    start: str
    end: str
    chosen: ExplainedPath | None
    # Every other path found, shortest first. Never trimmed.
    alternatives: tuple[ExplainedPath, ...]
    reason: Reason
    warnings: tuple[PathWarning, ...]

    @property
    def provenance(self) -> tuple[tuple[str, str, Source], ...]:
        """Every edge the chosen path used, with its source:
        (referencing column, referenced column, catalog | overlay)."""
        if self.chosen is None:
            return ()
        return tuple(
            (f"{join.fk_table}.{fk}", f"{join.pk_table}.{pk}", join.source)
            for join in self.chosen.joins
            for fk, pk in zip(join.fk_columns, join.pk_columns)
        )


def explain(result: PathResult, graph: nx.DiGraph) -> Explanation:
    start, end = _table(graph, result.start), _table(graph, result.end)

    if result.selected is None:
        text = f"No route of {_joins(result.max_joins)} or fewer connects {start} and {end}."
        return Explanation(
            result.start, result.end, None, (),
            Reason(None, text),
            (PathWarning("no_path", text + " Nothing was joined.", (result.start, result.end)),),
        )  # fmt: skip

    tied_ids = {path.id for path in result.tied}
    chosen = _path(result.selected, graph, tied=False)
    alternatives = tuple(
        _path(path, graph, tied=path.id in tied_ids) for path in result.discovered if path is not result.selected
    )
    tied_alternatives = [path for path in alternatives if path.tied_with_chosen]
    count = len(result.discovered)
    tie = f"{len(result.tied)} routes of {_joins(chosen.length)} connect {start} and {end}"

    warnings: list[PathWarning] = []

    if result.rule == "only_path":
        reason = f"Only one route of {_joins(result.max_joins)} or fewer connects {start} and {end}."
    elif result.rule == "shortest":
        reason = f"{count} routes existed; the shortest was used."
    elif result.rule == "preference":
        reason = (
            f"{tie}. The overlay declares which is meant, because: {result.preference_applied.because}."
        )
    elif result.rule == "question_evidence":
        (_, best), (_, next_best) = result.evidence[:2]
        others = " ".join(f"Not taken: {path.description}" for path in tied_alternatives)
        reason = (
            f"{tie}. The wording of the question points to this one: it scores {best:.3f} against "
            f"{next_best:.3f} for the next, more than the {result.margin:.3f} that could be chance. {others}"
        )
    else:
        reason = f"{tie}. The tie was broken alphabetically, so the choice is arbitrary."
        if result.evidence:
            (_, best), (_, next_best) = result.evidence[:2]
            reason += (
                f" The wording of the question did not separate them: {best:.3f} against {next_best:.3f}, "
                f"within the {result.margin:.3f} that could be chance."
            )
        others = " ".join(f"Equally valid: {path.description}" for path in tied_alternatives)
        warnings.append(
            PathWarning(
                "arbitrary_choice",
                f"I had no basis for this choice. {tie}, and nothing declares which is meant. "
                f"Used: {chosen.description} {others}",
                about=(chosen.id, *(path.id for path in tied_alternatives)),
            )
        )

    if result.preference_not_applied is not None:
        declared = result.preference_not_applied
        named = " / ".join(sorted(f"{a}={b}" for a, b in declared.prefer))
        why = (
            "it does not name one of the tied routes"
            if result.tied
            else "the route it names is not the shortest, and a preference only breaks ties"
        )
        warnings.append(
            PathWarning(
                "preference_not_applied",
                f"The overlay declares a preference between {start} and {end} ({named}) "
                f"that was not applied: {why}.",
                about=(named,),
            )
        )

    for table in chosen.many_to_many_at:
        position = result.selected.tables.index(table)
        before = _table(graph, result.selected.tables[position - 1])
        after = _table(graph, result.selected.tables[position + 1])
        pivot = _table(graph, table)
        warnings.append(
            PathWarning(
                "many_to_many",
                f"This route joins {before} to {after} through {pivot}. One {pivot} row has many {before} rows "
                f"and many {after} rows, so every {before} row is paired with every {after} row that shares it: "
                "rows multiply, and sums and counts over them are inflated.",
                about=(table,),
            )
        )

    return Explanation(result.start, result.end, chosen, alternatives, Reason(result.rule, reason), tuple(warnings))


def _path(path: Path, graph: nx.DiGraph, tied: bool) -> ExplainedPath:
    joins = tuple(_join(join, graph) for join in path.joins)
    sentence = "; then ".join(join.description for join in joins)
    return ExplainedPath(
        id=path.id,
        tables=path.tables,
        length=path.length,
        joins=joins,
        many_to_many_at=path.many_to_many_at,
        tied_with_chosen=tied,
        description=sentence[0].upper() + sentence[1:] + ".",
    )


def _join(join: Join, graph: nx.DiGraph) -> ExplainedJoin:
    """One clause, read in the direction the path walked. The direction is
    what tells a reader which side has one row and which has many."""
    fk_table, pk_table = _table(graph, join.fk_table), _table(graph, join.pk_table)
    columns = " and ".join(_column(graph, join.fk_table, column) for column in join.fk_columns)

    if join.walked == MANY_TO_ONE:
        clause = f"each {fk_table} row has one {pk_table}, through its {columns}"
    else:
        clause = f"each {pk_table} row has many {fk_table} rows, through their {columns}"
    if join.source == "overlay":
        clause += " (asserted in the overlay, not declared by the database)"

    return ExplainedJoin(
        fk_table=join.fk_table,
        fk_columns=join.fk_columns,
        pk_table=join.pk_table,
        pk_columns=join.pk_columns,
        walked=join.walked,
        source=join.source,
        constraint=join.constraint,
        note=join.note,
        description=clause,
    )


def _table(graph: nx.DiGraph, table: str) -> str:
    return graph.nodes[table]["readable"] or table


def _column(graph: nx.DiGraph, table: str, column: str) -> str:
    """The column's readable name without its table: "catalog sales — bill
    address surrogate key" becomes "bill address surrogate key", since the
    sentence has already named the table."""
    readable = graph.nodes[f"{table}.{column}"]["readable"] or column
    return readable.split(" — ", 1)[-1]


def _joins(count: int) -> str:
    return "1 join" if count == 1 else f"{count} joins"
