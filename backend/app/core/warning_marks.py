"""Each warning set against the joins the executed SQL actually made (item
64; DD-21 as amended at step 8, the owner's ruling 7).

Pure (DD-01): a tree, the warnings the Explainer raised about it, and what
the SQL that gave the answer joined; a mark per warning out.

WHY. The warnings are raised about the tree retrieval built, and the SQL
may use a fraction of it: a question that joined nothing once carried
three. DD-21's principle, one level further: the alarm is about the answer
given. Only an arbitrary choice that touched the answer is loud.

WHAT A WARNING IS ABOUT.
  arbitrary_choice   the DECIDING joins of the route chosen: those the
                     routes it tied with, at the same table, do not all
                     share. A join on every tied route says nothing about
                     which was meant.
  multi_anchor       the same, against the routes that would have attached
                     at another table.
  preference_not_applied
                     the joins of the route chosen for that attachment.
  many_to_many       the joins through the pivot, one from each many side.
  no_path            nothing: no tree was built.

THE FIVE STATES.
  followed           a deciding join is among the SQL's joins. LOUD.
                     For many_to_many: at least two of the joins through
                     the pivot are, so the rows did multiply.
  other_route_taken  none is, and a join of a route it tied with is: the
                     SQL took the other one. LOUD: the choice was not
                     obeyed and the ambiguity touched the answer all the
                     same.
  not_used           neither. Quiet.
  unknown            neither was found, and part of the SQL could not be
                     read, so it may be in that part. LOUD: a checker that
                     cannot read the SQL must not silence an alarm (T-02).
  not_applicable     no SQL gave an answer. Quiet.

"AMONG THE SQL'S JOINS" is the frozen ConformanceCheck's own reading,
called here and not changed: by equivalence class, an outer join's
condition directly. A two-column key of which one pair is there counts as
touched: the SQL went that way, wrongly.

`had_ambiguity` (DD-07's extracted column, ruling 4): some arbitrary_choice
or multi_anchor is loud. many_to_many is loud when it happened and is not
an ambiguity: nothing was chosen.

NOT TUNED TO ANY QUESTION. d7 of the development runs is loud about its
date table, which could equally have hung on `customer`. A person finds
that fussy; it is the rule's answer and it stands (ruling 7).
"""

from dataclasses import dataclass
from typing import Literal

from app.core.conformance import Column, Edge, Equality, Extraction, compare, edges_of
from app.core.explainer import PathWarning
from app.core.join_tree import Attachment, JoinTree
from app.core.path_finder import Path

State = Literal["followed", "other_route_taken", "not_used", "unknown", "not_applicable"]

LOUD: frozenset[str] = frozenset({"followed", "other_route_taken", "unknown"})
ARBITRARY: frozenset[str] = frozenset({"arbitrary_choice", "multi_anchor"})


@dataclass(frozen=True)
class Ran:
    """What the SQL that gave the answer joined, as the ConformanceCheck
    read it."""

    equalities: tuple[Equality, ...]
    classes: tuple[frozenset[Column], ...]
    # Part of the SQL could not be read with confidence.
    unread: bool


@dataclass(frozen=True)
class Mark:
    code: str
    text: str
    about: tuple[str, ...]
    # The joins the warning is about ...
    joins: tuple[Edge, ...]
    # ... and those of the routes it was tied with that the chosen one
    # does not make.
    other_joins: tuple[Edge, ...]
    state: State

    @property
    def loud(self) -> bool:
        return self.state in LOUD


def had_ambiguity(marks: tuple[Mark, ...]) -> bool:
    return any(mark.loud and mark.code in ARBITRARY for mark in marks)


def mark_warnings(tree: JoinTree, warnings: tuple[PathWarning, ...], ran: Ran | None) -> tuple[Mark, ...]:
    """`ran` is None when no SQL gave an answer. The warnings must be the
    Explainer's for this tree, in its order."""
    about = _subjects(tree)
    if [code for code, _, _ in about] != [warning.code for warning in warnings]:
        raise ValueError(
            f"these are not this tree's warnings: the tree raises {[code for code, _, _ in about]} "
            f"and was given {[warning.code for warning in warnings]}"
        )
    return tuple(
        Mark(warning.code, warning.text, warning.about, joins, others, _state(code, joins, others, ran))
        for warning, (code, joins, others) in zip(warnings, about)
    )


def _subjects(tree: JoinTree) -> list[tuple[str, tuple[Edge, ...], tuple[Edge, ...]]]:
    """(code, the joins it is about, the other routes' joins), one for each
    warning the Explainer raises about this tree, in the Explainer's order."""
    if tree.declined:
        return [("no_path", (), ())] if tree.unconnected else []
    subjects = []
    for attachment in tree.attachments:
        if attachment.path is None:
            continue
        if attachment.arbitrary and attachment.tied_at_the_same_table:
            subjects.append(("arbitrary_choice", *_deciding(attachment, attachment.tied_at_the_same_table)))
        if attachment.arbitrary and attachment.tied_at_another_table:
            subjects.append(("multi_anchor", *_deciding(attachment, attachment.tied_at_another_table)))
        if attachment.preference_not_applied is not None:
            subjects.append(("preference_not_applied", edges_of(attachment.path.joins), ()))
    for pivot, _ in tree.pivots:
        subjects.append(("many_to_many", edges_of(join for join in tree.joins if join.pk_table == pivot), ()))
    return subjects


def _deciding(attachment: Attachment, others: tuple[Path, ...]) -> tuple[tuple[Edge, ...], tuple[Edge, ...]]:
    chosen = edges_of(attachment.path.joins)
    rivals = [edges_of(path.joins) for path in others]
    deciding = tuple(edge for edge in chosen if not all(edge in rival for rival in rivals))
    theirs: list[Edge] = []
    for rival in rivals:
        theirs += [edge for edge in rival if edge not in chosen and edge not in theirs]
    return deciding, tuple(theirs)


def _touched(edges: tuple[Edge, ...], ran: Ran) -> int:
    """How many of these joins the SQL makes, whole or in part."""
    found = compare(Extraction(ran.equalities, ran.classes, (), (), ()), edges)
    return len(found.present) + len(found.partial)


def _state(code: str, joins: tuple[Edge, ...], others: tuple[Edge, ...], ran: Ran | None) -> State:
    if ran is None or code == "no_path":
        return "not_applicable"
    needed = 2 if code == "many_to_many" else 1
    if _touched(joins, ran) >= needed:
        return "followed"
    if others and _touched(others, ran):
        return "other_route_taken"
    return "unknown" if ran.unread else "not_used"
