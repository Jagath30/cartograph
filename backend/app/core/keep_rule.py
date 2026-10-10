"""The keep-rule of the retrieval pass between steps 7 and 8: what a
question HOLDS, and whether a change may be kept.

Pure (DD-01). It reads the frozen evaluation set and the frozen step 6
judgement and changes neither.

THE RULE, as the owner gave it on 10 October 2026: a change is kept only if
no question loses anything it held at the baseline (a table, a join, its
expected warning, a decline), and at least one question gains something.

WHAT A QUESTION HOLDS, as read before any run and approved at stop 1:

  declined as expected   the system declined, or did not, as the set says
  table X                an expected table that is in the tree; for a
                         `one_of`, "one of ..." when at least one is
  join ...               an expected join that is in the tree, by any of its
                         alternatives
  warning CODE           the expected code, when it is among those raised
  no warning             a question that expects none and raises none
  exact tables           the step 6 judge's tables part: the expected tables
  exact joins            and no other; likewise its joins part. Question 8
                         holds both at the baseline, and they are held.

After a decline on either side nothing but the decline is held, as in the
step 6 judgement. A surplus table is not something lost: it costs a
question only what it breaks, an exact part or "no warning".
"""

from dataclasses import dataclass

from app.core.eval_set import Question
from app.core.eval_step6 import DECLINE, NONE, SEE_NOTE, Produced, joins_as_expected, tables_as_expected

DECLINED_AS_EXPECTED = "declined as expected"
NO_WARNING = "no warning"
EXACT_TABLES = "exact tables"
EXACT_JOINS = "exact joins"


def held(question: Question, produced: Produced) -> frozenset[str]:
    expects_decline = question.warning == DECLINE
    items: set[str] = set()
    if produced.declined == expects_decline:
        items.add(DECLINED_AS_EXPECTED)
    if produced.declined or expects_decline:
        return frozenset(items)

    items |= {f"table {table}" for table in question.tables if table in produced.tree_tables}
    if produced.tree_tables & set(question.tables_one_of):
        items.add(f"table one of {', '.join(question.tables_one_of)}")

    for alternatives in question.joins:
        if any(frozenset(join.edges) in produced.tree_joins for join in alternatives):
            items.add("join " + " and ".join(f"{start} = {end}" for start, end in sorted(alternatives[0].edges)))

    if question.warning == NONE:
        if not produced.warnings:
            items.add(NO_WARNING)
    elif question.warning != SEE_NOTE and question.warning in produced.warnings:
        items.add(f"warning {question.warning}")

    if tables_as_expected(question, produced.tree_tables):
        items.add(EXACT_TABLES)
    if joins_as_expected(question, produced.tree_joins):
        items.add(EXACT_JOINS)
    return frozenset(items)


@dataclass(frozen=True)
class Kept:
    kept: bool
    # Question id -> what it held at the baseline and holds no longer.
    lost: dict[int, tuple[str, ...]]
    # Question id -> what it holds now and did not at the baseline.
    gained: dict[int, tuple[str, ...]]
    # Gains outside the sixteen questions: a table the step 7 diagnosis
    # named as missing for a development question, now shown.
    other_gains: tuple[str, ...] = ()


def keep(
    baseline: dict[int, frozenset[str]], changed: dict[int, frozenset[str]], other_gains: tuple[str, ...] = ()
) -> Kept:
    """Kept only when nothing is lost anywhere and something is gained."""
    if set(baseline) != set(changed):
        raise ValueError("the baseline and the change were not run over the same questions")
    lost = {q: tuple(sorted(baseline[q] - changed[q])) for q in sorted(baseline) if baseline[q] - changed[q]}
    gained = {q: tuple(sorted(changed[q] - baseline[q])) for q in sorted(baseline) if changed[q] - baseline[q]}
    return Kept(not lost and bool(gained or other_gains), lost, gained, tuple(other_gains))
