"""Retriever: scored matches in, a bounded set of anchor tables out
(FR-08, FR-09, FR-10, FR-41, FR-42, DD-09, DD-11).

Pure (DD-01). It receives similarity results; it does not fetch them. The
shell embeds the question, runs the two searches and splits the question
into words; everything that is a decision happens here, on plain numbers.

TWO KINDS OF SCORE, BOTH KEPT (DD-09, ruling i).

  raw         what the searches returned: a cosine similarity and a
              text-search rank. Kept on every candidate. Any absolute
              decision would have to use these and only these; since the
              floor was withdrawn (below) nothing here makes one.
  normalised  each kind stretched to 0..1 across the candidates of one
              query (min-max), then combined:

                  combined = alpha * semantic + (1 - alpha) * keyword

              Ranking uses these and only these.

  The bug DD-09 names lives in the stretch. If every candidate has the same
  score of one kind -- no keyword matched anything -- that signal carries no
  information and normalises to 0 for all of them: never to 1, never to
  NaN. Otherwise a signal that said nothing would outvote one that did.

WHAT IS SCORED. The whole question, once, against every element: each
column, and each table's own row (ruling f). A table's score is the better
of its own row and its best column. And each TERM of the question -- a
content word, or two adjacent content words (ruling h) -- is scored the
same way, by the same code, on its own.

ANCHORS (DD-11, the anchor bound). A table is an anchor when its score for
the whole question is at least the cut, up to the cap, best first. The
bound records what the cap excluded.

SET ASIDE ONLY FOR AN ANCHOR (the owner's ruling at the fourth review
stop). A table is set aside only for a winner that is still an anchor
after the cap. As first built the rivals were set aside and the cap
applied afterwards, so a table could be set aside for a winner the cap
then cut: run 2 lost store_sales that way, kept neither table, and said
nothing. The cap and the rivals are now settled together (see `retrieve`).

RIVALS (ruling c, FR-41). Every term nominates one table: the one holding
its best-scoring element, by the term's own combined score, equal scores
going to the name that sorts first. No threshold is involved: a term
always has a best element, and that element's table is its nominee. Any
other table within the margin of the nominee, for that term, AND NOT
JOINED TO IT, is a rival: the term could as well have meant it. A table
that is in contention only as a rival -- no term nominates it -- is not
made an anchor beside the table that beat it, and the bound records that
too. Whether the reader must be warned is not decided here: it depends on
whether the rival ends up in the join tree anyway, and the tree is not
this component's (DD-01). Every rival is handed on.

PARTNERS, NEVER RIVALS (the owner's correction at the third review stop).
Two tables directly joined by a foreign key, in either direction, are
partners. One refers to the other, so an answer about one routinely needs
both: they are not alternatives for the same role, and neither is ever set
aside for the other. As first built, any two tables close in score for a
term were rivals; run 1 set aside a sale for its own store, and the
ambiguity warning fired on 15 questions of 16. The rule is structural: it
uses nothing about the kind of table and nothing from any question. This
component has no graph (DD-01), so which tables are joined is handed in,
as plain data, like the scores. A partner within the margin is recorded on
the term beside its rivals; it is protected from that term's choice only,
and may still be the rival of a table it is not joined to.

NO DECLINE HAPPENS HERE. As first built, a question whose best raw
similarity was at or below a floor was declined here, and a term at or
below it nominated nothing. The floor was computed from the schema alone
by a method fixed in advance, and the step 6 baseline falsified it: every
question, answerable or not, fell below it. It was withdrawn by the
owner's ruling at the second review stop, and no number replaced it. A
question is declined at step 6 only when its anchors cannot be connected
(DD-10), which is the tree builder's to find. The question's best raw
similarity is still recorded, as information.

Nothing is dropped silently. Every candidate keeps both raw scores, both
normalised scores, the combined score and its rank.
"""

from dataclasses import dataclass
from typing import Literal

TermKind = Literal["word", "bigram"]

# How many of a term's best elements are recorded as considered (FR-41).
CONSIDERED = 5


@dataclass(frozen=True)
class RawScore:
    """One schema element scored against one piece of text."""

    table: str
    # None for the table's own row.
    column: str | None
    # Cosine similarity between the text and the element's description.
    semantic: float
    # Text-search rank of the element's description for the text's words.
    # 0 when none of them occurs in it.
    keyword: float

    @property
    def element(self) -> str:
        return self.table if self.column is None else f"{self.table}.{self.column}"


@dataclass(frozen=True)
class Token:
    """One piece of the question, in order. `content` is False for a
    stopword and for punctuation: both break a pair of adjacent words."""

    text: str
    content: bool


@dataclass(frozen=True)
class Term:
    text: str
    kind: TermKind


@dataclass(frozen=True)
class Settings:
    alpha: float
    anchor_cut: float
    anchor_cap: int
    # A difference in combined score too small to act on (rulings a, c).
    margin: float

    def __post_init__(self) -> None:
        if not 0 <= self.alpha <= 1:
            raise ValueError(f"alpha must lie between 0 and 1, got {self.alpha}")
        if not 0 <= self.anchor_cut <= 1:
            raise ValueError(f"the anchor cut must lie between 0 and 1, got {self.anchor_cut}")
        if self.anchor_cap < 1:
            raise ValueError(f"the anchor cap must be at least 1, got {self.anchor_cap}")
        if self.margin < 0:
            raise ValueError(f"the margin cannot be negative, got {self.margin}")


@dataclass(frozen=True)
class Candidate:
    """One element, with everything that was known about it (FR-09)."""

    table: str
    column: str | None
    semantic_raw: float
    keyword_raw: float
    semantic: float
    keyword: float
    combined: float
    # 1 is best. Equal combined scores are ordered by name.
    rank: int

    @property
    def kind(self) -> str:
        return "table" if self.column is None else "column"

    @property
    def element(self) -> str:
        return self.table if self.column is None else f"{self.table}.{self.column}"


@dataclass(frozen=True)
class TableScore:
    table: str
    # The better of the table's own row and its best column.
    score: float
    # The element that gave it: the table's own name, or table.column.
    best: str


@dataclass(frozen=True)
class Rival:
    """Another table one term could as well have meant."""

    term: str
    chosen: str
    chosen_element: str
    chosen_score: float
    rival: str
    rival_element: str
    rival_score: float


@dataclass(frozen=True)
class TermResult:
    term: Term
    # The term's best raw similarity to any element. Information only.
    best_raw: float
    # The table the term nominates, and the element that won it.
    chosen_table: str
    chosen_element: str
    # The term's best elements, best first: (element, combined score).
    considered: tuple[tuple[str, float], ...]
    # Tables within the margin of the choice and not joined to it.
    rivals: tuple[Rival, ...]
    # Tables within the margin of the choice and joined to it by a foreign
    # key: never rivals. Recorded so that nothing is left out silently.
    partners: tuple[Rival, ...] = ()


@dataclass(frozen=True)
class AnchorBound:
    cut: float
    cap: int
    # Tables at or above the cut that the cap left out, best first.
    excluded_by_cap: tuple[str, ...]
    # Tables at or above the cut that were in contention only as a term's
    # rival, and were not made anchors beside the table that beat them.
    # Each was set aside for at least one table that is an anchor.
    set_aside_as_rivals: tuple[str, ...]


@dataclass(frozen=True)
class Retrieval:
    question: str
    settings: Settings
    # The question's best raw similarity to any element. Information only.
    best_raw: float
    # Every element, best first.
    candidates: tuple[Candidate, ...]
    # Every table, best first.
    tables: tuple[TableScore, ...]
    # Best first. Empty when no table reaches the cut.
    anchors: tuple[str, ...]
    anchor_bound: AnchorBound
    terms: tuple[TermResult, ...]

    @property
    def rivals(self) -> tuple[Rival, ...]:
        return tuple(rival for term in self.terms for rival in term.rivals)

    @property
    def column_scores(self) -> dict[str, float]:
        """table.column -> combined score for the whole question: what the
        question's wording says about each column (DD-12 as amended)."""
        return {candidate.element: candidate.combined for candidate in self.candidates if candidate.column}

    def score_of(self, table: str) -> float:
        return next(entry.score for entry in self.tables if entry.table == table)


def make_terms(tokens: tuple[Token, ...]) -> tuple[Term, ...]:
    """The question's content words in order, then every pair of content
    words with nothing between them. Lower-cased; a repeat is kept once."""
    words = [Term(token.text.lower(), "word") for token in tokens if token.content]
    pairs = [
        Term(f"{first.text.lower()} {second.text.lower()}", "bigram")
        for first, second in zip(tokens, tokens[1:])
        if first.content and second.content
    ]
    return tuple(dict.fromkeys(words + pairs))


def normalise(values: list[float]) -> list[float]:
    """Min-max to 0..1. A signal that is the same for every candidate says
    nothing about any of them, and becomes 0 for all."""
    if not values:
        return []
    low, high = min(values), max(values)
    if high == low:
        return [0.0] * len(values)
    return [(value - low) / (high - low) for value in values]


def rank(scores: tuple[RawScore, ...], alpha: float) -> tuple[Candidate, ...]:
    """Every element with both normalised scores and the combination, best
    first."""
    elements = [score.element for score in scores]
    if len(set(elements)) != len(elements):
        raise ValueError("an element was scored twice")

    semantic = normalise([score.semantic for score in scores])
    keyword = normalise([score.keyword for score in scores])
    combined = [alpha * s + (1 - alpha) * k for s, k in zip(semantic, keyword)]

    order = sorted(range(len(scores)), key=lambda index: (-combined[index], elements[index]))
    return tuple(
        Candidate(
            table=scores[index].table,
            column=scores[index].column,
            semantic_raw=scores[index].semantic,
            keyword_raw=scores[index].keyword,
            semantic=semantic[index],
            keyword=keyword[index],
            combined=combined[index],
            rank=position,
        )
        for position, index in enumerate(order, start=1)
    )


def table_scores(candidates: tuple[Candidate, ...]) -> tuple[TableScore, ...]:
    """Each table once, by the better of its own row and its best column.
    `candidates` is best first, so the first element met for a table is the
    one that gives it its score."""
    best: dict[str, TableScore] = {}
    for candidate in candidates:
        best.setdefault(candidate.table, TableScore(candidate.table, candidate.combined, candidate.element))
    return tuple(sorted(best.values(), key=lambda entry: (-entry.score, entry.table)))


def retrieve(
    question: str,
    scores: tuple[RawScore, ...],
    terms: tuple[tuple[Term, tuple[RawScore, ...]], ...],
    settings: Settings,
    partners: dict[str, frozenset[str]] | None = None,
) -> Retrieval:
    """`scores` is every element scored against the whole question; `terms`
    is each term with every element scored against it alone; `partners` is
    each table with the tables a foreign key joins it to directly. Handed
    none, no two tables are joined."""
    if not scores:
        raise ValueError("nothing was scored: the schema has no elements")
    partners = partners or {}

    candidates = rank(scores, settings.alpha)
    tables = table_scores(candidates)
    best_raw = max(score.semantic for score in scores)
    term_results = tuple(_term(term, term_scores, settings, partners) for term, term_scores in terms)

    above_cut = [entry.table for entry in tables if entry.score >= settings.anchor_cut]

    # A table is set aside when some term nominated a table that is an
    # anchor, this table was within the margin of it and not joined to it,
    # and no term nominates this table in its own right.
    #
    # "Is an anchor" is read AFTER the cap, so the two are settled together.
    # Start from every table at the cut as a possible winner; set aside the
    # rivals of the winners; apply the cap; a winner the cap cut is no
    # longer one, and whatever was set aside for it alone comes back and
    # competes under the cap by its score. Repeat until nothing changes. It
    # ends: tables only ever come back, so a winner once cut stays cut and
    # the winners only shrink.
    nominated = {result.chosen_table for result in term_results if result.chosen_table}
    close_calls = [
        (rival.chosen, rival.rival)
        for result in term_results
        for rival in result.rivals
        if rival.rival not in nominated
    ]
    winners = set(above_cut)
    while True:
        beaten = {rival for chosen, rival in close_calls if chosen in winners}
        in_the_running = [table for table in above_cut if table not in beaten]
        still_anchors = winners & set(in_the_running[: settings.anchor_cap])
        if still_anchors == winners:
            break
        winners = still_anchors

    return Retrieval(
        question, settings,
        best_raw=best_raw,
        candidates=candidates, tables=tables,
        anchors=tuple(in_the_running[: settings.anchor_cap]),
        anchor_bound=AnchorBound(
            settings.anchor_cut,
            settings.anchor_cap,
            excluded_by_cap=tuple(in_the_running[settings.anchor_cap :]),
            set_aside_as_rivals=tuple(table for table in above_cut if table in beaten),
        ),
        terms=term_results,
    )  # fmt: skip


def _term(
    term: Term, scores: tuple[RawScore, ...], settings: Settings, partners: dict[str, frozenset[str]]
) -> TermResult:
    candidates = rank(scores, settings.alpha)
    tables = table_scores(candidates)
    chosen = tables[0]
    within_margin = [other for other in tables[1:] if chosen.score - other.score < settings.margin]

    def joined(other: TableScore) -> bool:
        return other.table in partners.get(chosen.table, ()) or chosen.table in partners.get(other.table, ())

    def record(other: TableScore) -> Rival:
        return Rival(term.text, chosen.table, chosen.best, chosen.score, other.table, other.best, other.score)

    return TermResult(
        term,
        best_raw=max(score.semantic for score in scores),
        chosen_table=chosen.table,
        chosen_element=chosen.best,
        considered=tuple((candidate.element, candidate.combined) for candidate in candidates[:CONSIDERED]),
        rivals=tuple(record(other) for other in within_margin if not joined(other)),
        partners=tuple(record(other) for other in within_margin if joined(other)),
    )
