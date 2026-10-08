"""PathFinder: every join path between two tables, and which one is used
(FR-11 to FR-14, DD-10, DD-12, DD-21).

Pure (DD-01): a graph and two table names in, a PathResult out.

A HOP IS ONE JOIN. In the graph a join is three edges -- table, column,
column, table -- so the search does not run on the graph itself. It runs on
a table-level view built here, in which one edge is one foreign key. The
has_column edges are structure and never count.

THE SEARCH IS UNDIRECTED, because a join works from either end. But every
join in a result keeps its real direction: which table holds the foreign key
and which the primary key, and which way this path walked it.

    many_to_one   walked from the foreign key to the primary key
                  (each sale has one store)
    one_to_many   walked from the primary key to the foreign key
                  (each store has many sales)

THE RULE (DD-12, as amended at step 6), in order:
  1. Shortest wins: fewest joins.
  2. Among tied shortest paths, a preference declared in the overlay wins --
     if it names exactly one of them. Otherwise it is not applied.
  3. Otherwise, if the caller supplies what the question's wording says
     about each column, and that scores one tied path above every other by
     more than the margin, that path wins: `question_evidence`. It is a
     reasoned choice, not an arbitrary one, and the scores are kept.
  4. Otherwise the tie is broken alphabetically, by table names and then by
     column names. That choice is ARBITRARY and the result says so.

Rule 3 uses nothing about the kind of table, its size or its place in the
schema (Charter D-06 rejects those). Its only input is what the person
asking wrote. It applies only among tied shortest paths: it can never
promote a longer route. Handed no evidence, this component behaves exactly
as it did before the rule existed.

HOW A TIED PATH IS SCORED. By the columns that tell it apart: every column
on its joins that is not on every tied path. cs_bill_addr_sk against
cs_ship_addr_sk: both reach ca_address_sk, so each path is scored by its
own one key. The score is the mean of those columns' scores.

Nothing is ever dropped to make that choice look cleaner. Every path found
within the limit is in `discovered`, and the ones tied with the selected
path are in `tied` -- because the failure this guards against is a path
that is valid, short and not the one that was meant (DD-21).

SHAPE IS REPORTED, NEVER RANKED ON. A path that arrives at a table's primary
key and leaves by the same primary key -- store_sales -> item <- catalog_sales
-- joins two "many" sides through one row, so rows multiply. Such a table is
listed in `many_to_many_at`. Ranking by it was rejected: Charter D-06 rules
out heuristics that favour one kind of table over another.

ONLY TIES BETWEEN ROUTES TO THE SAME DESTINATION are visible here. Two
different destinations one hop away (store against customer_address) is a
question of which tables were asked for, and this component cannot see it.
"""

from dataclasses import dataclass
from typing import Literal

import networkx as nx

from app.core.graph_builder import TABLE, foreign_key_edges, table_nodes
from app.core.snapshot import Preference, Source

# DD-10's ceiling is four. Three is the measured default: on TPC-DS it finds
# 11,453 paths across all pairs where four finds 99,480, and it leaves one
# pair of 276 unconnected (CHECKPOINTS.md, checkpoint-04).
DEFAULT_MAX_JOINS = 3

MANY_TO_ONE = "many_to_one"
ONE_TO_MANY = "one_to_many"

Walked = Literal["many_to_one", "one_to_many"]
Rule = Literal["only_path", "shortest", "preference", "question_evidence", "alphabetical"]


@dataclass(frozen=True)
class Join:
    """One foreign key, as one path walked it."""

    fk_table: str
    fk_columns: tuple[str, ...]
    pk_table: str
    pk_columns: tuple[str, ...]
    source: Source
    constraint: str | None
    walked: Walked
    # Why a human asserted this key, where one did (overlay edges only).
    note: str | None = None

    @property
    def from_table(self) -> str:
        return self.fk_table if self.walked == MANY_TO_ONE else self.pk_table

    @property
    def to_table(self) -> str:
        return self.pk_table if self.walked == MANY_TO_ONE else self.fk_table

    @property
    def edges(self) -> tuple[tuple[str, str], ...]:
        """The graph edges this join is made of, in their real direction."""
        return tuple(
            (f"{self.fk_table}.{fk}", f"{self.pk_table}.{pk}") for fk, pk in zip(self.fk_columns, self.pk_columns)
        )


@dataclass(frozen=True)
class Path:
    joins: tuple[Join, ...]

    @property
    def tables(self) -> tuple[str, ...]:
        return (self.joins[0].from_table, *(join.to_table for join in self.joins))

    @property
    def length(self) -> int:
        return len(self.joins)

    @property
    def edges(self) -> frozenset[tuple[str, str]]:
        return frozenset(edge for join in self.joins for edge in join.edges)

    @property
    def id(self) -> str:
        """Readable and stable: the same route has the same id whichever
        end it was asked from."""
        return " / ".join(sorted(f"{start}={end}" for start, end in self.edges))

    @property
    def many_to_many_at(self) -> tuple[str, ...]:
        """Tables this path enters by their primary key and leaves by the
        same primary key. Both neighbours hold many rows per row of such a
        table, so joining through it pairs every row of one with every row
        of the other."""
        return tuple(
            arriving.to_table
            for arriving, leaving in zip(self.joins, self.joins[1:])
            if arriving.walked == MANY_TO_ONE and leaving.walked == ONE_TO_MANY
        )


@dataclass(frozen=True)
class PathResult:
    start: str
    end: str
    max_joins: int
    # Every path found, shortest first, then in tie-break order.
    discovered: tuple[Path, ...]
    selected: Path | None
    rule: Rule | None
    # The shortest paths when there is more than one, selected included.
    # Empty when the shortest path stood alone.
    tied: tuple[Path, ...] = ()
    preference_applied: Preference | None = None
    # Declared for this pair, but it did not name exactly one of the paths
    # it could have chosen between. Reported, never silently dropped.
    preference_not_applied: Preference | None = None
    # What the question's wording said about each tied path, best first:
    # (path id, score). Empty when no evidence was supplied or nothing
    # tied. Present whether or not it decided.
    evidence: tuple[tuple[str, float], ...] = ()
    # The difference in score evidence had to exceed to decide.
    margin: float | None = None

    @property
    def arbitrary(self) -> bool:
        """True when nothing but the alphabet chose the selected path."""
        return self.rule == "alphabetical"


@dataclass(frozen=True)
class Choice:
    """One path chosen among several equally short ones, and on what basis."""

    selected: Path
    rule: Rule
    evidence: tuple[tuple[str, float], ...] = ()


def score_tied(tied: tuple[Path, ...], evidence: dict[str, float]) -> dict[str, float]:
    """Path id -> the mean score of the columns that tell that path apart
    from the others it is tied with."""
    columns = [frozenset(column for edge in path.edges for column in edge) for path in tied]
    shared = frozenset.intersection(*columns)
    scores = {}
    for path, on_path in zip(tied, columns):
        telling = sorted(on_path - shared)
        missing = [column for column in telling if column not in evidence]
        if missing:
            raise ValueError(f"no score was supplied for {missing}")
        scores[path.id] = sum(evidence[column] for column in telling) / len(telling) if telling else 0.0
    return scores


def choose(
    tied: tuple[Path, ...],
    preferred: tuple[Path, ...] = (),
    evidence: dict[str, float] | None = None,
    margin: float = 0.0,
) -> Choice:
    """Rules 2 to 4, among paths already known to be equally short. `tied`
    is in alphabetical order; `preferred` is those of them a declared
    preference names."""
    if len(preferred) == 1:
        return Choice(preferred[0], "preference")

    scored: tuple[tuple[str, float], ...] = ()
    if evidence is not None:
        scores = score_tied(tied, evidence)
        ranked = sorted(tied, key=lambda path: -scores[path.id])  # stable: ties stay alphabetical
        scored = tuple((path.id, scores[path.id]) for path in ranked)
        if scored[0][1] - scored[1][1] > margin:
            return Choice(ranked[0], "question_evidence", scored)

    return Choice(tied[0], "alphabetical", scored)


def find_paths(
    graph: nx.DiGraph,
    start: str,
    end: str,
    max_joins: int = DEFAULT_MAX_JOINS,
    evidence: dict[str, float] | None = None,
    margin: float = 0.0,
) -> PathResult:
    """`evidence` is table.column -> what the question's wording says about
    that column, and `margin` the difference it must exceed to decide a
    tie (rule 3). Without evidence, rule 3 is skipped."""
    for table in (start, end):
        if table not in graph or graph.nodes[table]["kind"] != TABLE:
            raise ValueError(f"{table} is not a table in this graph")
    if start == end:
        raise ValueError(f"a path needs two different tables, got {start} twice")
    if max_joins < 1:
        raise ValueError("max_joins must be at least 1")

    tables = _table_view(graph)
    discovered = []
    for walk in nx.all_simple_edge_paths(tables, start, end, cutoff=max_joins):
        joins = []
        for from_table, to_table, key in walk:
            facts = tables.edges[from_table, to_table, key]
            walked = MANY_TO_ONE if from_table == facts["fk_table"] else ONE_TO_MANY
            joins.append(Join(**facts, walked=walked))
        discovered.append(Path(tuple(joins)))
    discovered.sort(key=lambda path: (path.length, _alphabetical(path)))

    declared = next(
        (p for p in graph.graph.get("preferences", ()) if p.between == tuple(sorted((start, end)))),
        None,
    )
    if not discovered:
        return PathResult(start, end, max_joins, (), None, None, preference_not_applied=declared)

    shortest = tuple(path for path in discovered if path.length == discovered[0].length)
    preferred = [path for path in shortest if declared and path.edges == frozenset(declared.prefer)]

    if len(shortest) == 1:
        # No tie, so nothing for a preference to break. If one was declared
        # for some other route, it cannot promote that route past a shorter
        # one (DD-12 lists it after rule 1), and the result says it was unused.
        return PathResult(
            start, end, max_joins, tuple(discovered),
            selected=shortest[0],
            rule="only_path" if len(discovered) == 1 else "shortest",
            preference_not_applied=None if preferred or declared is None else declared,
        )  # fmt: skip

    choice = choose(shortest, tuple(preferred), evidence, margin)
    return PathResult(
        start, end, max_joins, tuple(discovered),
        selected=choice.selected, rule=choice.rule, tied=shortest,
        preference_applied=declared if choice.rule == "preference" else None,
        preference_not_applied=None if choice.rule == "preference" else declared,
        evidence=choice.evidence,
        margin=margin if choice.evidence else None,
    )  # fmt: skip


def _table_view(graph: nx.DiGraph) -> nx.MultiGraph:
    """One node per table, one edge per foreign key. A multigraph, because
    here two keys between the same two tables really are parallel edges.

    A key of several columns is several column-to-column edges in the
    graph, all carrying the same `foreign_key` number; they are gathered
    back into one join. Grouping by constraint name would not do: overlay
    edges have none, and a two-column overlay key would come out as two
    alternative one-column joins.
    """
    gathered: dict[int, dict] = {}
    for start, end, data in foreign_key_edges(graph):
        fk_table, pk_table = graph.nodes[start]["table"], graph.nodes[end]["table"]
        facts = gathered.setdefault(
            data["foreign_key"],
            {
                "fk_table": fk_table,
                "fk_columns": (),
                "pk_table": pk_table,
                "pk_columns": (),
                "source": data["source"],
                "constraint": data["constraint"],
                "note": data["note"],
            },
        )
        facts["fk_columns"] += (graph.nodes[start]["name"],)
        facts["pk_columns"] += (graph.nodes[end]["name"],)

    tables = nx.MultiGraph()
    tables.add_nodes_from(table_nodes(graph))
    for facts in gathered.values():
        # A table that references itself is a loop, and no simple path uses one.
        if facts["fk_table"] != facts["pk_table"]:
            tables.add_edge(facts["fk_table"], facts["pk_table"], **facts)
    return tables


def _alphabetical(path: Path) -> tuple:
    """The tie-break of DD-12, rule 2: table names along the path, then
    column names. DD-12 says only "table names", which cannot separate
    cs_bill_addr_sk from cs_ship_addr_sk -- the same two tables either way --
    so the columns are the necessary second key.

    Read from the alphabetically smaller end, so that asking for A to B and
    for B to A always chooses the same route.
    """
    joins = path.joins if path.tables[0] <= path.tables[-1] else tuple(reversed(path.joins))
    tables = path.tables if path.tables[0] <= path.tables[-1] else tuple(reversed(path.tables))
    return (tables, tuple((join.fk_table, join.fk_columns, join.pk_table, join.pk_columns) for join in joins))
