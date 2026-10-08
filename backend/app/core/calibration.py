"""The margin and the floor, computed from the schema alone (rulings a, c
and d of step 6; methods fixed in CHECKPOINTS.md before any embedding
existed).

Pure (DD-01): scores in, two numbers out. Nothing here reads the
evaluation set, and nothing here may: these two values decide which
questions warn and which are declined, and they are fixed before any
evaluation question is embedded.

PSEUDO-QUESTIONS. Both methods need text that stands in for a question
without being one. It is the schema's own readable names: one per table and
one per column. Each is scored against every element exactly as a question
is, by the same code -- except that the elements of its own table are left
out, so it is never compared with itself.

THE MARGIN: a difference in combined score too small to act on.
  Take every pair of sibling keys: two foreign keys from the same table to
  the same table. For each pair, and for every pseudo-question from a table
  that is neither end of the pair, take the absolute difference between
  the two keys' combined scores. Those pseudo-questions are about something
  else, so any difference they produce between the siblings is noise. The
  margin is the 95th percentile of those differences. The combined score
  depends on alpha, so there is one margin per alpha.

THE FLOOR: a best raw similarity no better than something the schema does
not hold would score.
  For each pseudo-question, take its BEST raw similarity over the elements
  of every table that has no foreign key, in either direction, to its own
  table. The floor is the median of those bests. A best compared with
  bests: a question's best over hundreds of elements is not compared with
  single pairs. Known bias: a pseudo-question's best is taken over fewer
  elements than a real question's, which pushes the floor down and makes
  declines rarer. That is the safer direction.

PERCENTILES are by nearest rank: the smallest value with at least that
share of the values at or below it. No interpolation, so the result is
always a value that was actually observed.
"""

from dataclasses import dataclass

from app.core.retriever import RawScore, rank
from app.core.snapshot import SchemaSnapshot

MARGIN_PERCENTILE = 95
FLOOR_PERCENTILE = 50

# One key of a sibling pair: its table and its referencing columns.
Key = tuple[str, tuple[str, ...]]


@dataclass(frozen=True)
class Pseudo:
    """A readable name scored as if it were a question."""

    table: str
    text: str
    # Every element, the pseudo-question's own table included: leaving
    # those out is this module's job, so it cannot be forgotten by a caller.
    scores: tuple[RawScore, ...]


@dataclass(frozen=True)
class Measured:
    value: float
    # How many numbers the value was taken from.
    observations: int


def sibling_pairs(snapshot: SchemaSnapshot) -> tuple[tuple[Key, Key], ...]:
    """Every two foreign keys that run from the same table to the same
    table, in the snapshot's order."""
    keys = snapshot.foreign_keys
    return tuple(
        ((first.from_table, first.from_columns), (second.from_table, second.from_columns))
        for position, first in enumerate(keys)
        for second in keys[position + 1 :]
        if (first.from_table, first.to_table) == (second.from_table, second.to_table)
    )


def linked_tables(snapshot: SchemaSnapshot) -> dict[str, frozenset[str]]:
    """table -> the tables it has a foreign key to or from."""
    linked: dict[str, set[str]] = {table.name: set() for table in snapshot.tables}
    for key in snapshot.foreign_keys:
        linked[key.from_table].add(key.to_table)
        linked[key.to_table].add(key.from_table)
    return {table: frozenset(others) for table, others in linked.items()}


def percentile(values: list[float], share: int) -> float:
    if not values:
        raise ValueError("no values to take a percentile of")
    if not 0 < share <= 100:
        raise ValueError(f"a percentile lies between 1 and 100, got {share}")
    ordered = sorted(values)
    # Nearest rank: ceil(share/100 * n), counted from 1.
    position = -(-share * len(ordered) // 100)
    return ordered[position - 1]


def margin_differences(
    pseudos: tuple[Pseudo, ...], snapshot: SchemaSnapshot, pairs: tuple[tuple[Key, Key], ...], alpha: float
) -> list[float]:
    """One absolute difference for every sibling pair under every
    pseudo-question from a table that is neither end of the pair."""
    targets = {
        (key.from_table, key.from_columns): key.to_table for key in snapshot.foreign_keys
    }
    differences = []
    for pseudo in pseudos:
        combined = {
            candidate.element: candidate.combined
            for candidate in rank(tuple(score for score in pseudo.scores if score.table != pseudo.table), alpha)
        }
        for first, second in pairs:
            if pseudo.table in (first[0], targets[first]):
                continue
            differences.append(abs(_key_score(first, combined) - _key_score(second, combined)))
    return differences


def margin(
    pseudos: tuple[Pseudo, ...], snapshot: SchemaSnapshot, pairs: tuple[tuple[Key, Key], ...], alpha: float
) -> Measured:
    differences = margin_differences(pseudos, snapshot, pairs, alpha)
    return Measured(percentile(differences, MARGIN_PERCENTILE), len(differences))


def floor_bests(pseudos: tuple[Pseudo, ...], linked: dict[str, frozenset[str]]) -> list[float]:
    """Each pseudo-question's best raw similarity over the elements of
    tables with no foreign key to or from its own. A pseudo-question whose
    table is linked to every other has no such element and gives nothing."""
    bests = []
    for pseudo in pseudos:
        unrelated = [
            score.semantic
            for score in pseudo.scores
            if score.table != pseudo.table and score.table not in linked[pseudo.table]
        ]
        if unrelated:
            bests.append(max(unrelated))
    return bests


def floor(pseudos: tuple[Pseudo, ...], linked: dict[str, frozenset[str]]) -> Measured:
    bests = floor_bests(pseudos, linked)
    return Measured(percentile(bests, FLOOR_PERCENTILE), len(bests))


def _key_score(key: Key, combined: dict[str, float]) -> float:
    """A key's score is the mean of its columns' scores, as a tied path's
    is (app.core.path_finder.score_tied)."""
    table, columns = key
    return sum(combined[f"{table}.{column}"] for column in columns) / len(columns)
