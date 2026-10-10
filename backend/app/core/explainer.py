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

A JOIN TREE (step 6) is explained by `explain_tree`, attachment by
attachment, with the same sentences and the same four warnings, and one
more that only exists once a question has been read:

  multi_anchor            an anchor could attach to the tree at two
                          different tables equally well, and only the
                          alphabet chose where.

A CLOSE CALL IS NOT A WARNING. Where a word of the question could as well
have meant another table, which was set aside and is not part of the
answer, the explanation records it in `close_calls`, with the word, both
tables and both scores (FR-41). It was a warning, `anchor_ambiguity`, as
first built. Run 1 raised it on 15 questions of 16 and run 2, after
joined tables stopped being rivals, on 12: a warning that fires almost
everywhere tells the reader nothing (DD-21). The owner revised ruling c at
the fourth review stop: until "alternatives" has a structural definition,
close calls are information, shown quietly, and the loud warnings are the
ones the Design names. No code path here raises `anchor_ambiguity`.

`route_codes` says which warnings concern the path between two tables
within a tree: those raised about any join on it, and many_to_many when
that path itself pivots.
"""

from dataclasses import dataclass
from typing import Literal

import networkx as nx

from app.core.join_tree import ANCHORS_NOT_CONNECTED, Attachment, JoinTree
from app.core.path_finder import MANY_TO_ONE, Join, Path, PathResult, Rule
from app.core.snapshot import Source

WarningCode = Literal["arbitrary_choice", "preference_not_applied", "many_to_many", "no_path", "multi_anchor"]


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
        reason += _outranked(result.preference_outranked)
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


def describe_path(path: Path, graph: nx.DiGraph) -> ExplainedPath:
    """One route the chosen one was weighed against, in the same words:
    for the trace, which records what a declared preference withdrew."""
    return _path(path, graph, tied=True)


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


# --------------------------------------------------------------------------
# A join tree
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ExplainedAttachment:
    anchor: str
    order: int
    # None for the seed.
    attached_to: str | None
    path: ExplainedPath | None
    # The candidates it was tied with, wherever they would have attached.
    alternatives: tuple[ExplainedPath, ...]
    reason: Reason


@dataclass(frozen=True)
class CloseCall:
    """A word of the question that could as well have meant another table,
    which is not part of the answer. Information, not a warning."""

    term: str
    chosen: str
    rival: str
    chosen_score: float
    rival_score: float
    text: str


@dataclass(frozen=True)
class TreeExplanation:
    declined: bool
    # In the order the anchors were attached.
    attachments: tuple[ExplainedAttachment, ...]
    tables: tuple[str, ...]
    reason: str
    warnings: tuple[PathWarning, ...]
    close_calls: tuple[CloseCall, ...] = ()

    @property
    def codes(self) -> frozenset[str]:
        return frozenset(warning.code for warning in self.warnings)


def explain_tree(tree: JoinTree, graph: nx.DiGraph) -> TreeExplanation:
    if tree.declined:
        if tree.decline_reason == ANCHORS_NOT_CONNECTED:
            names = ", ".join(_table(graph, table) for table in tree.unconnected)
            text = (
                f"No route of {_joins(tree.max_joins)} or fewer connects {names} to the other tables the "
                "question is about. Nothing was joined: this question cannot be answered from this schema."
            )
            return TreeExplanation(True, (), (), text, (PathWarning("no_path", text, tree.unconnected),))
        return TreeExplanation(True, (), (), "No table matched the question closely enough to start from.", ())

    warnings: list[PathWarning] = []
    attachments = []
    for attachment in tree.attachments:
        if attachment.path is None:
            reason = Reason(None, f"Started from {_table(graph, attachment.anchor)}, the table the question matched best.")
            attachments.append(ExplainedAttachment(attachment.anchor, attachment.order, None, None, (), reason))
            continue

        chosen = _path(attachment.path, graph, tied=False)
        alternatives = tuple(_path(path, graph, tied=True) for path in attachment.tied if path is not attachment.path)
        attachments.append(
            ExplainedAttachment(
                attachment.anchor, attachment.order, attachment.attached_to, chosen, alternatives,
                Reason(attachment.rule, _attachment_reason(attachment, chosen, alternatives, graph)),
            )
        )  # fmt: skip
        warnings += _attachment_warnings(attachment, chosen, graph)

    for pivot, many_sides in tree.pivots:
        sides = [_table(graph, table) for table in many_sides]
        name = _table(graph, pivot)
        warnings.append(
            PathWarning(
                "many_to_many",
                f"This answer joins {' and '.join(sides)} through {name}. One {name} row has many rows of each, "
                f"so every row of one is paired with every row of the other that shares it: rows multiply, and "
                "sums and counts over them are inflated.",
                about=(pivot,),
            )
        )

    close_calls = []
    for entry in tree.ambiguities:
        chosen, rival = _table(graph, entry.chosen), _table(graph, entry.rival)
        close_calls.append(
            CloseCall(
                entry.term, entry.chosen, entry.rival, entry.chosen_score, entry.rival_score,
                f'"{entry.term}" in the question could as well mean {rival}. {chosen} was used: it scored '
                f"{entry.chosen_score:.3f} against {entry.rival_score:.3f}, too close to tell apart. "
                f"{rival} is not part of this answer.",
            )
        )  # fmt: skip

    names = ", ".join(_table(graph, table) for table in tree.tables)
    return TreeExplanation(
        False, tuple(attachments), tree.tables, f"Joined: {names}.", tuple(warnings), tuple(close_calls)
    )


def route_codes(tree: JoinTree, start: str, end: str) -> tuple[str, ...]:
    """The warning codes that concern the path between two tables within
    the tree: those raised about any join on it, and many_to_many when the
    path itself pivots. Empty when the tree does not hold both tables."""
    route = tree.route(start, end)
    if route is None:
        return ()
    codes = set()
    for attachment in tree.attachments_on(route):
        if attachment.arbitrary and attachment.tied_at_the_same_table:
            codes.add("arbitrary_choice")
        if attachment.arbitrary and attachment.tied_at_another_table:
            codes.add("multi_anchor")
        if attachment.preference_not_applied is not None:
            codes.add("preference_not_applied")
    if route.many_to_many_at:
        codes.add("many_to_many")
    return tuple(sorted(codes))


def _attachment_reason(attachment: Attachment, chosen: ExplainedPath, alternatives, graph: nx.DiGraph) -> str:
    anchor = _table(graph, attachment.anchor)
    count = len(attachment.tied)
    tie = f"{count} routes of {_joins(chosen.length)} connect {anchor} to the tables already joined"

    if attachment.rule == "only_path":
        return f"Only one route of {_joins(attachment.path.length)} or fewer connects {anchor} to the tables already joined."
    if attachment.rule == "shortest":
        return f"{attachment.discovered} routes existed; the shortest was used."
    narrowed = ""
    if attachment.attach_preferences or attachment.route_preferences:
        count = len(attachment.tied) + len(attachment.withdrawn)
        tie = f"{count} routes of {_joins(chosen.length)} connect {anchor} to the tables already joined"
        if attachment.attach_preferences:
            declared = "; ".join(
                f"{_table(graph, p.attach_to)} rather than {_table(graph, p.rather_than)}, because: {p.because}"
                for p in attachment.attach_preferences
            )
            narrowed += f" The overlay declares where a table that could join either is attached: {declared}."
        for p in attachment.route_preferences:
            first, second = (_table(graph, table) for table in p.between)
            narrowed += f" The overlay declares which route between {first} and {second} is meant, because: {p.because}."
        gone = " ".join(f"Not taken: {_path(path, graph, tied=True).description}" for path in attachment.withdrawn)
        narrowed += f" {gone}"
    if attachment.rule == "preference" and narrowed:
        return f"{tie}.{narrowed}"
    if attachment.rule == "preference":
        return f"{tie}. The overlay declares which is meant, because: {attachment.preference_applied.because}."
    if attachment.rule == "question_evidence":
        (_, best), (_, next_best) = attachment.evidence[:2]
        others = " ".join(f"Not taken: {path.description}" for path in alternatives)
        return (
            f"{tie}. The wording of the question points to this one: it scores {best:.3f} against "
            f"{next_best:.3f} for the next, more than the {attachment.margin:.3f} that could be chance. {others}"
        ) + _outranked(attachment.preference_outranked)
    reason = f"{tie}.{narrowed}"
    if narrowed:
        reason += f" {len(attachment.tied)} routes were left."
    reason += " The tie was broken alphabetically, so the choice is arbitrary."
    if attachment.evidence:
        (_, best), (_, next_best) = attachment.evidence[:2]
        reason += (
            f" The wording of the question did not separate them: {best:.3f} against {next_best:.3f}, "
            f"within the {attachment.margin:.3f} that could be chance."
        )
    return reason


def _outranked(preference) -> str:
    """A declared route preference the question's wording overruled. Said
    in the reason; it is not a fault and raises nothing."""
    if preference is None:
        return ""
    named = " / ".join(sorted(f"{a}={b}" for a, b in preference.prefer))
    return f" The overlay prefers {named} by default; the wording of the question outranks it."


def _attachment_warnings(attachment: Attachment, chosen: ExplainedPath, graph: nx.DiGraph) -> list[PathWarning]:
    warnings = []
    anchor = _table(graph, attachment.anchor)

    if attachment.arbitrary and attachment.tied_at_the_same_table:
        same = [_path(path, graph, tied=True) for path in attachment.tied_at_the_same_table]
        others = " ".join(f"Equally valid: {path.description}" for path in same)
        warnings.append(
            PathWarning(
                "arbitrary_choice",
                f"I had no basis for this choice. {len(same) + 1} routes of {_joins(chosen.length)} connect "
                f"{anchor} and {_table(graph, attachment.attached_to)}, and nothing declares which is meant. "
                f"Used: {chosen.description} {others}",
                about=(chosen.id, *(path.id for path in same)),
            )
        )

    if attachment.arbitrary and attachment.tied_at_another_table:
        elsewhere = [_path(path, graph, tied=True) for path in attachment.tied_at_another_table]
        places = sorted({_table(graph, path.tables[-1]) for path in attachment.tied_at_another_table})
        others = " ".join(f"Equally valid: {path.description}" for path in elsewhere)
        warnings.append(
            PathWarning(
                "multi_anchor",
                f"I had no basis for where to join {anchor}. It is {_joins(chosen.length)} from "
                f"{_table(graph, attachment.attached_to)} and equally from {' and '.join(places)}, and nothing "
                f"declares which is meant. Used: {chosen.description} {others}",
                about=(chosen.id, *(path.id for path in elsewhere)),
            )
        )

    if attachment.preference_not_applied is not None:
        declared = attachment.preference_not_applied
        named = " / ".join(sorted(f"{a}={b}" for a, b in declared.prefer))
        warnings.append(
            PathWarning(
                "preference_not_applied",
                f"The overlay declares a preference between {' and '.join(declared.between)} ({named}) "
                "that was not applied.",
                about=(named,),
            )
        )
    return warnings
