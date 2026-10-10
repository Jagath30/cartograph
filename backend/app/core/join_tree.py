"""JoinTree: several anchors, one tree of joins (FR-10, FR-11, DD-10, DD-11;
the owner's ruling b at step 6).

Pure (DD-01): a graph, the anchors with their scores, and what the question
said about each column in; a JoinTree out.

WHY THIS EXISTS. The PathFinder answers "how do these TWO tables join". A
question names three or four, and pairs do not combine by themselves: asked
about item and date_dim alone, the PathFinder bridges them through whichever
fact table sorts first, although store_sales is already in the question.
Connecting every pair of anchors adds joins no correct answer makes.

THE METHOD: NEAREST ATTACHMENT.
  1. The seed is the highest-scoring anchor, ties broken alphabetically.
  2. Of the anchors not yet in the tree, take the one fewest joins from ANY
     table already in it (ties: higher score, then name).
  3. Its candidate routes are every shortest route to every tree table at
     that distance. One candidate: it is used. Several: DD-12's rules 2 to
     4 choose -- the question's wording, then a declared preference, then
     the alphabet -- exactly as between two tables.
  4. Every table on the chosen route joins the tree, so a later anchor can
     attach to a bridging table as well as to an anchor.
  5. Repeat until no anchor is left. The order is recorded.

A PREFERENCE BETWEEN TWO PLACES (ruling C of the retrieval pass). Where
the candidates end at different tree tables and the question's wording has
not decided, the overlay may declare `attach_to: X, rather_than: Y`: when
both X and Y are among the places, the candidates ending at Y are
withdrawn. One left: it is chosen, by the preference, and nothing warns.
Several left: the alphabet chooses among them and the choice is arbitrary,
as before. The wording is consulted once, over every candidate, and not
again over what a preference left. What was withdrawn is recorded. It is
never applied to the table being attached, to a bridge inside a route, or
where every candidate ends at one table.

A ROUTE PREFERENCE AMONG SEVERAL PLACES (ruling D of the retrieval pass).
A preference that names one route between two tables cannot choose between
places. Where the wording has not decided and it names exactly one of the
candidates, it withdraws the OTHER candidates that end at the same table
-- the other keys to it -- and says nothing of the rest. The same two
outcomes follow: one left, chosen by preference; several, the alphabet.

No kind of table is favoured anywhere in this (Charter D-06): no fact table
as hub, no ranking by fan-out. Distance and the question decide; where they
cannot, the alphabet does and the tree says so.

WHAT IS RECORDED AS A FACT, for the Explainer to turn into warnings:
  - an attachment the alphabet chose, and whether the candidates it could
    not tell apart attached at the SAME table (the two-table tie of step 4)
    or at a DIFFERENT one (possible only with several anchors);
  - a table that two joins of the tree both reach by its primary key, so
    the rows on either side multiply through it;
  - a table the Retriever set aside as a term's rival that is not in the
    tree: choosing it would have given a different tree.

DISCONNECTION IS REPORTED, NOT REPAIRED (DD-10). If an anchor has no route
to the tree within the hop limit, nothing is forced: the tree is declined,
naming the anchors left out (FR-42).

THE SUBGRAPH BOUND (DD-11). The tables of the tree are protected: they are
never cut. If they alone exceed the bound, the lowest-scoring anchor is
dropped and the tree rebuilt -- never a connector. Tables that lie only on
tied alternatives are added while the bound allows, and are the first cut.
What was dropped and what was cut are both recorded.
"""

from dataclasses import dataclass, replace

import networkx as nx

from app.core.path_finder import (
    DEFAULT_MAX_JOINS,
    MANY_TO_ONE,
    ONE_TO_MANY,
    Choice,
    Join,
    Path,
    Rule,
    alphabetical,
    choose,
    find_paths,
)
from app.core.snapshot import AttachPreference, Preference

DEFAULT_SUBGRAPH_BOUND = 10

NO_ANCHORS = "no_anchors"
ANCHORS_NOT_CONNECTED = "anchors_not_connected"


@dataclass(frozen=True)
class SetAside:
    """A table the Retriever did not make an anchor because one term could
    as well have meant another table, which it chose."""

    term: str
    chosen: str
    rival: str
    chosen_score: float
    rival_score: float


@dataclass(frozen=True)
class Attachment:
    """How one anchor came into the tree."""

    anchor: str
    # 0 for the seed.
    order: int
    # The route from the anchor to the tree as it then stood. None for the
    # seed.
    path: Path | None
    # The tree table the route ends at.
    attached_to: str | None
    rule: Rule | None
    # Every candidate route when there was more than one, chosen included.
    tied: tuple[Path, ...] = ()
    evidence: tuple[tuple[str, float], ...] = ()
    margin: float | None = None
    # Routes of any length found between the anchor and the tree tables it
    # could have attached to at this distance.
    discovered: int = 0
    preference_applied: Preference | None = None
    preference_not_applied: Preference | None = None
    # Candidates a preference between two places withdrew, and the
    # preferences that did it. `tied` is then what was left, chosen included.
    withdrawn: tuple[Path, ...] = ()
    attach_preferences: tuple[AttachPreference, ...] = ()
    # Route preferences that withdrew the other keys to their own table
    # where the candidates ended at several places.
    route_preferences: tuple[Preference, ...] = ()
    # A route preference that named a candidate and was outranked by the
    # question's wording.
    preference_outranked: Preference | None = None

    @property
    def arbitrary(self) -> bool:
        return self.rule == "alphabetical"

    @property
    def tied_at_the_same_table(self) -> tuple[Path, ...]:
        """Candidates the chosen route was tied with that end where it does."""
        return tuple(path for path in self.tied if path is not self.path and path.tables[-1] == self.attached_to)

    @property
    def tied_at_another_table(self) -> tuple[Path, ...]:
        return tuple(path for path in self.tied if path.tables[-1] != self.attached_to)


@dataclass(frozen=True)
class SubgraphBound:
    limit: int
    # Anchors dropped, lowest score first, because the tree's own tables
    # exceeded the limit with them in.
    dropped_anchors: tuple[str, ...] = ()
    # Tables on tied alternatives that the limit left out.
    excluded: tuple[str, ...] = ()


@dataclass(frozen=True)
class JoinTree:
    anchors: tuple[str, ...]
    max_joins: int
    declined: bool
    decline_reason: str | None
    # Anchors with no route to the tree, when declined for that.
    unconnected: tuple[str, ...]
    seed: str | None
    # In the order the anchors were attached, the seed first.
    attachments: tuple[Attachment, ...]
    # The tables of the tree, in the order they joined it.
    tables: tuple[str, ...]
    # The joins of the tree, each once.
    joins: tuple[Join, ...]
    # Tables two joins of the tree both reach by the primary key, each with
    # the tables on the other end of those joins.
    pivots: tuple[tuple[str, tuple[str, ...]], ...]
    # Rivals set aside that are not in the tree.
    ambiguities: tuple[SetAside, ...]
    # The tree's tables, then tables of tied alternatives as far as allowed.
    subgraph: tuple[str, ...]
    subgraph_bound: SubgraphBound

    @property
    def edges(self) -> frozenset[frozenset[tuple[str, str]]]:
        """The tree's joins, each as the column pairs of one foreign key."""
        return frozenset(frozenset(join.edges) for join in self.joins)

    def route(self, start: str, end: str) -> Path | None:
        """The one path between two tables within the tree, walked from
        `start`, or None when the tree does not hold both."""
        if start not in self.tables or end not in self.tables or start == end:
            return None
        reached: dict[str, tuple[Join, ...]] = {start: ()}
        frontier = [start]
        while frontier:
            table = frontier.pop()
            for join in self.joins:
                if table not in (join.fk_table, join.pk_table):
                    continue
                other = join.pk_table if table == join.fk_table else join.fk_table
                if other not in reached:
                    walked = MANY_TO_ONE if table == join.fk_table else ONE_TO_MANY
                    reached[other] = (*reached[table], replace(join, walked=walked))
                    frontier.append(other)
        return Path(reached[end]) if end in reached else None

    def attachments_on(self, route: Path) -> tuple[Attachment, ...]:
        """The attachments that put any join of `route` into the tree."""
        on_route = {frozenset(join.edges) for join in route.joins}
        return tuple(
            attachment
            for attachment in self.attachments
            if attachment.path and on_route & {frozenset(join.edges) for join in attachment.path.joins}
        )


def build_tree(
    graph: nx.DiGraph,
    anchors: tuple[str, ...],
    scores: dict[str, float],
    evidence: dict[str, float] | None = None,
    margin: float = 0.0,
    set_aside: tuple[SetAside, ...] = (),
    max_joins: int = DEFAULT_MAX_JOINS,
    subgraph_bound: int = DEFAULT_SUBGRAPH_BOUND,
) -> JoinTree:
    """`scores` is each anchor's retrieval score; `evidence` and `margin` are
    as for find_paths."""
    if len(set(anchors)) != len(anchors):
        raise ValueError(f"an anchor is named twice: {anchors}")
    missing = [anchor for anchor in anchors if anchor not in scores]
    if missing:
        raise ValueError(f"no score was supplied for the anchors {missing}")
    if subgraph_bound < 1:
        raise ValueError("the subgraph bound must be at least 1")

    ranked = tuple(sorted(anchors, key=lambda anchor: (-scores[anchor], anchor)))
    if not ranked:
        return _declined(anchors, max_joins, NO_ANCHORS, (), subgraph_bound)

    dropped: list[str] = []
    while True:
        kept = tuple(anchor for anchor in ranked if anchor not in dropped)
        attachments, tables, joins, unconnected = _grow(graph, kept, scores, evidence, margin, max_joins)
        if unconnected:
            return _declined(anchors, max_joins, ANCHORS_NOT_CONNECTED, unconnected, subgraph_bound, tuple(dropped))
        if len(tables) <= subgraph_bound or len(kept) == 1:
            break
        # The protected set alone is too large: drop the lowest-scoring
        # anchor and re-expand. Never cut a connector (DD-11).
        dropped.append(kept[-1])

    alternatives: list[str] = []
    for attachment in attachments:
        for path in (*attachment.tied, *attachment.withdrawn):
            alternatives += [table for table in path.tables if table not in tables and table not in alternatives]
    room = max(subgraph_bound - len(tables), 0)

    return JoinTree(
        anchors=anchors,
        max_joins=max_joins,
        declined=False,
        decline_reason=None,
        unconnected=(),
        seed=kept[0],
        attachments=attachments,
        tables=tables,
        joins=joins,
        pivots=_pivots(tables, joins),
        ambiguities=tuple(
            entry for entry in set_aside if entry.chosen in tables and entry.rival not in tables
        ),
        subgraph=(*tables, *alternatives[:room]),
        subgraph_bound=SubgraphBound(subgraph_bound, tuple(dropped), tuple(alternatives[room:])),
    )


def _grow(graph, anchors, scores, evidence, margin, max_joins):
    """The tree over `anchors`, best first: (attachments, tables, joins,
    unconnected). `unconnected` is the anchors with no route to the tree
    within the limit; when it is not empty the rest is as far as it got."""
    seed = anchors[0]
    attachments = [Attachment(seed, 0, None, None, None)]
    tables = [seed]
    joins: list[Join] = []
    waiting = list(anchors[1:])
    found: dict[tuple[str, str], object] = {}

    def paths(anchor: str, table: str):
        if (anchor, table) not in found:
            found[anchor, table] = find_paths(graph, anchor, table, max_joins, evidence, margin)
        return found[anchor, table]

    while waiting:
        # No waiting anchor is ever already in the tree: a table on a
        # shortest route to the tree is nearer to it than the route's far
        # end, so it would have been attached first.
        distance = {}
        for anchor in waiting:
            lengths = [paths(anchor, table).selected.length for table in tables if paths(anchor, table).selected]
            if lengths:
                distance[anchor] = min(lengths)
        if not distance:
            return tuple(attachments), tuple(tables), tuple(joins), tuple(waiting)

        anchor = min(distance, key=lambda name: (distance[name], -scores[name], name))
        nearest = [
            paths(anchor, table)
            for table in tables
            if paths(anchor, table).selected and paths(anchor, table).selected.length == distance[anchor]
        ]

        if len(nearest) == 1:
            # One place to attach: this is the PathFinder's own answer for
            # that pair, preference and evidence included.
            (result,) = nearest
            attachment = Attachment(
                anchor, len(attachments), result.selected, result.end, result.rule,
                tied=result.tied, evidence=result.evidence, margin=result.margin,
                discovered=len(result.discovered),
                preference_applied=result.preference_applied,
                preference_not_applied=result.preference_not_applied,
                preference_outranked=result.preference_outranked,
            )  # fmt: skip
        else:
            # Several places, equally near. Neither kind of preference is
            # asked unless the question's wording has not decided. A
            # preference between two PLACES withdraws the candidates ending
            # at the place it ranks second. A preference that names a ROUTE
            # cannot choose between places: it withdraws the other
            # candidates ending at its own table. One declared for a pair
            # here that names none of the candidates is reported as not
            # applied, as before.
            candidates = tuple(
                sorted(
                    (path for result in nearest for path in (result.tied or (result.selected,))),
                    key=alphabetical,
                )
            )
            choice = choose(candidates, (), evidence, margin)
            routes_declared = tuple(
                preference
                for result in nearest
                for preference in (result.preference_applied, result.preference_not_applied, result.preference_outranked)
                if preference is not None
            )
            naming = tuple(p for p in routes_declared if any(path.edges == frozenset(p.prefer) for path in candidates))
            left, applied, routes = candidates, (), ()
            if choice.rule != "question_evidence":
                left, applied = _narrow(candidates, graph.graph.get("attach_preferences", ()))
                left, routes = _narrow_routes(left, naming)
            if applied or routes:
                choice = Choice(left[0], "preference" if len(left) == 1 else "alphabetical", choice.evidence)
            declared = next((p for p in routes_declared if p not in naming), None)
            outranked = next(
                (p for p in naming if choice.rule == "question_evidence" and choice.selected.edges != frozenset(p.prefer)),
                None,
            )
            attachment = Attachment(
                anchor, len(attachments), choice.selected, choice.selected.tables[-1], choice.rule,
                tied=left, evidence=choice.evidence, margin=margin if choice.evidence else None,
                discovered=sum(len(result.discovered) for result in nearest),
                preference_not_applied=declared,
                withdrawn=tuple(path for path in candidates if path not in left),
                attach_preferences=applied,
                route_preferences=routes,
                preference_outranked=outranked,
            )  # fmt: skip

        attachments.append(attachment)
        waiting.remove(anchor)
        for join in attachment.path.joins:
            if frozenset(join.edges) not in {frozenset(known.edges) for known in joins}:
                joins.append(join)
        tables += [table for table in attachment.path.tables if table not in tables]

    return tuple(attachments), tuple(tables), tuple(joins), ()


def _narrow(
    candidates: tuple[Path, ...], declared: tuple[AttachPreference, ...]
) -> tuple[tuple[Path, ...], tuple[AttachPreference, ...]]:
    """The candidates a preference between two places leaves, in their
    order, and the preferences that withdrew any. One applies only when
    both of its tables are among the places the candidates end at."""
    left, applied = candidates, []
    for preference in declared:
        places = {path.tables[-1] for path in left}
        if preference.attach_to in places and preference.rather_than in places:
            left = tuple(path for path in left if path.tables[-1] != preference.rather_than)
            applied.append(preference)
    return left, tuple(applied)


def _narrow_routes(
    candidates: tuple[Path, ...], declared: tuple[Preference, ...]
) -> tuple[tuple[Path, ...], tuple[Preference, ...]]:
    """The candidates route preferences leave, and those that withdrew any.
    One that names a candidate withdraws the others ending at that
    candidate's table, and no other."""
    left, applied = candidates, []
    for preference in declared:
        named = [path for path in left if path.edges == frozenset(preference.prefer)]
        if len(named) != 1:
            continue
        # It is only ever asked where its two tables tied, so there is
        # always another key to that table to withdraw.
        place = named[0].tables[-1]
        left = tuple(path for path in left if path is named[0] or path.tables[-1] != place)
        applied.append(preference)
    return left, tuple(applied)


def _pivots(tables: tuple[str, ...], joins: tuple[Join, ...]) -> tuple[tuple[str, tuple[str, ...]], ...]:
    pivots = []
    for table in tables:
        many_sides = tuple(sorted(join.fk_table for join in joins if join.pk_table == table))
        if len(many_sides) > 1:
            pivots.append((table, many_sides))
    return tuple(pivots)


def _declined(anchors, max_joins, reason, unconnected, bound, dropped=()) -> JoinTree:
    return JoinTree(
        anchors=anchors, max_joins=max_joins, declined=True, decline_reason=reason,
        unconnected=tuple(unconnected), seed=None, attachments=(), tables=(), joins=(),
        pivots=(), ambiguities=(), subgraph=(), subgraph_bound=SubgraphBound(bound, tuple(dropped)),
    )  # fmt: skip
