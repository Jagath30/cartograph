"""ConformanceCheck: which joins a SQL statement really performs, and
whether they are the joins of a given path (FR-39, DD-13, DD-20,
criterion 13).

Pure (DD-01). Two functions, kept apart on purpose:

    extract_joins(sql, schema)     the joins the SQL performs. It is given
                                   no path and no graph: at step 10 the
                                   full-schema mode has no selected path,
                                   and this must work there alone.
    compare(extraction, edges)     those joins against a path's.

It is a validation stage and the evaluation's instrument, the same code.
It was written, tested on hand-written SQL and committed before any model
had produced SQL for it to read, so that nothing a model writes could
shape what it accepts.

WHAT IS EXTRACTED. An equality between two columns of two different
sources, written in an ON clause or as a top-level AND-ed condition of a
WHERE (the house style of TPC-DS, correction 9), with every alias resolved
and every column followed through a CTE or a derived table back to the
table it was read from. A column a derived source computes is not
followed: a join on it is `not_checked`.

COMPARISON IS BY MEANING, NOT BY SPELLING (the owner's ruling at step 7;
a clarification of DD-13). `a = b AND b = c` joins a to c. Postgres treats
the three as one equivalence class, and so does this:

  - the SQL's inner-join equalities are gathered into classes of columns
    that are equal to one another; the path's edges likewise;
  - an edge of the path is PRESENT when each of its column pairs lies
    within one of the SQL's classes;
  - an equality of the SQL is FOREIGN when the path's classes do not
    imply it;
  - an OUTER join's condition takes no part in any of that: it does not
    make its two sides equal for other rows, so it must match a column
    pair of the path directly;
  - an edge of two columns of which only one pair is present is a wrong
    join, not half a right one;
  - a source in a FROM that nothing joins is a cross join.

UNION AND UNION ALL (the owner's ruling at stop 2). Each branch is read as
its own scope, exactly as a CTE's body is. A join on a column that comes
out of a UNION is read only when every branch puts a plain column of a
table there; it is then one equality for each branch, and those take no
part in transitivity, as an outer join's condition does not: each is true
of its own branch's rows and of no others. INTERSECT and EXCEPT stay
`not_checked`: they compare rows across their branches, which is a join
in disguise.

So joining two fact tables each to `item`, and joining them to each other
on the item key with `item` joined too, both conform to a path through
`item`; joining them directly without `item` is incomplete; and the wrong
address key is diverged however it is spelled.

THE OUTCOMES, AND WHICH WINS (DD-13 as clarified):

    diverged      a confidently extracted join is foreign, or a cross
                  join, or a two-column key is half there. FIRST, even
                  when another part of the SQL could not be read: at step
                  10 `not_checked` leaves the denominator, and a known
                  wrong join must be counted, not dropped.
    not_checked   something could not be read with confidence. Never
                  "conforms" (T-02).
    incomplete    edges of the path are unused and nothing is foreign.
    conforms      every edge present, nothing foreign.

Every finding is listed whatever the outcome.
"""

from dataclasses import dataclass
from typing import Literal

from sqlglot import exp

from app.core.sql_reading import (
    Body,
    Reading,
    Schema,
    Select,
    Source,
    SqlNotParsed,
    outputs,
    own_nodes,
    parse_statements,
    read,
    resolve,
    trace,
)

# (table, column)
Column = tuple[str, str]
# One join of a path: its column pairs. A single-column key has one.
Edge = tuple[tuple[Column, Column], ...]

Outcome = Literal["conforms", "diverged", "incomplete", "not_checked"]

# Why something could not be read with confidence. The words are fixed:
# step 10 counts by them.
REASONS = (
    "not_parsed",
    "not_one_statement",
    "not_a_select",
    "self_join",
    "or",
    "negated",
    "non_equality",
    "computed_join_key",
    "natural_join",
    "using",
    "set_operation",
    "recursive_cte",
    "correlated_subquery",
    "subquery_predicate",
    "unread_source",
    "unresolved_column",
)


@dataclass(frozen=True)
class Equality:
    """One join condition the SQL performs, in terms of tables: the two
    columns in alphabetical order."""

    left: Column
    right: Column
    # Written in the ON of a LEFT, RIGHT or FULL join.
    outer: bool = False
    # One side came out of a UNION: this is what the condition means for
    # the rows of one branch.
    union: bool = False

    @property
    def pair(self) -> frozenset[Column]:
        return frozenset((self.left, self.right))

    @property
    def direct(self) -> bool:
        """True when it must match a pair of the path as it stands, and
        makes nothing else equal: an outer join's, or a UNION branch's."""
        return self.outer or self.union


@dataclass(frozen=True)
class Unchecked:
    reason: str
    # The piece of SQL it is about.
    detail: str


@dataclass(frozen=True)
class Extraction:
    # Every join condition read with confidence, once each.
    equalities: tuple[Equality, ...]
    # The columns the inner joins make equal to one another. Two uses of
    # one table are two sets of columns here, never merged.
    classes: tuple[frozenset[Column], ...]
    # Every table the statement reads.
    tables: tuple[str, ...]
    # Groups of sources in one FROM with nothing joining group to group.
    cross_joins: tuple[tuple[str, ...], ...]
    unchecked: tuple[Unchecked, ...]


@dataclass(frozen=True)
class Conformance:
    outcome: Outcome
    present: tuple[Edge, ...]
    missing: tuple[Edge, ...]
    partial: tuple[Edge, ...]
    foreign: tuple[Equality, ...]
    cross_joins: tuple[tuple[str, ...], ...]
    unchecked: tuple[Unchecked, ...]
    # One sentence per thing found, whatever the outcome.
    findings: tuple[str, ...]


def edges_of(joins) -> tuple[Edge, ...]:
    """A tree's joins (path_finder.Join) as edges to compare with."""
    return tuple(
        tuple(((join.fk_table, fk), (join.pk_table, pk)) for fk, pk in zip(join.fk_columns, join.pk_columns))
        for join in joins
    )


# --------------------------------------------------------------------------
# Extraction
# --------------------------------------------------------------------------


class _Classes:
    """Union-find: things declared equal, gathered into classes."""

    def __init__(self) -> None:
        self._parent: dict = {}

    def find(self, item):
        self._parent.setdefault(item, item)
        while self._parent[item] != item:
            self._parent[item] = self._parent[self._parent[item]]
            item = self._parent[item]
        return item

    def join(self, a, b) -> None:
        self._parent[self.find(a)] = self.find(b)

    def classes(self) -> list[list]:
        groups: dict = {}
        for item in self._parent:
            groups.setdefault(self.find(item), []).append(item)
        return list(groups.values())


class _Extractor:
    def __init__(self, schema: Schema) -> None:
        self.schema = schema
        # Keyed by (source, column): one use of a table, one of its columns.
        self.inner = _Classes()
        self.sources: dict[int, Source] = {}
        self.equalities: dict[tuple[frozenset[Column], bool, bool], Equality] = {}
        self.tables: set[str] = set()
        self.cross_joins: list[tuple[str, ...]] = []
        self.unchecked: dict[tuple[str, str], Unchecked] = {}
        self._seen: set[int] = set()

    def skip(self, reason: str, node) -> None:
        detail = node if isinstance(node, str) else node.sql(dialect="postgres")
        self.unchecked.setdefault((reason, detail), Unchecked(reason, detail))

    # ---- walking the statement ------------------------------------------

    def body(self, body: Body) -> None:
        if id(body) in self._seen:
            return
        self._seen.add(id(body))
        if body.recursive:
            self.skip("recursive_cte", body.node)
        if body.is_set_operation:
            # UNION: each branch is a scope of its own, like a CTE's body.
            # INTERSECT and EXCEPT compare rows across branches. Either
            # way the joins inside each branch are read.
            if not isinstance(body.node, exp.Union):
                self.skip("set_operation", body.node)
            for branch in body.branches:
                self.body(branch)
        elif body.select is not None:
            self.select(body.select)

    def select(self, select: Select) -> None:
        linked = _Classes()
        for source in select.sources:
            linked.find(id(source))
            if source.kind == "table":
                self.tables.add(source.table if source.qualifier in (None, "public") else f"{source.qualifier}.{source.table}")
            elif source.kind == "derived":
                self.body(source.body)
            else:
                self.skip("unread_source", f"{source.unread} in FROM")
        self._self_joins(select)

        for position, join in enumerate(select.joins, start=1):
            if join.natural or join.using:
                self.skip("natural_join" if join.natural else "using", f"JOIN {join.source.alias}")
                # Which earlier source it joins to is exactly what is not
                # known, so it is not also called a cross join.
                for earlier in select.sources[:position]:
                    linked.join(id(earlier), id(join.source))
            if join.on is not None:
                self._conditions(join.on, select, join.outer, linked)
        where = select.node.args.get("where")
        if where is not None:
            self._conditions(where.this, select, False, linked)

        for subquery in select.subqueries:
            self._subquery(subquery, select)
            self.body(subquery)
        self._cross_joins(select, linked)

    def _self_joins(self, select: Select) -> None:
        seen: dict = {}
        for source in select.sources:
            key = ("table", source.table) if source.kind == "table" else ("body", id(source.body))
            if source.kind != "unread" and key in seen:
                self.skip("self_join", f"{seen[key].alias} and {source.alias}")
            seen.setdefault(key, source)

    # ---- one ON or WHERE -------------------------------------------------

    def _conditions(self, expression, select: Select, outer: bool, linked: _Classes) -> None:
        for condition in _conjuncts(expression):
            sides = _column_equality(condition)
            if sides is not None:
                self._equality(condition, sides, select, outer, linked)
                continue
            reason = self._spanning(condition, select, linked)
            if reason is not None:
                self.skip(reason, condition)

    def _equality(self, condition, sides, select: Select, outer: bool, linked: _Classes) -> None:
        resolved = [resolve(side, select, self.schema) for side in sides]
        if any(found.kind in ("unknown", "ambiguous") for found in resolved):
            # It is a condition between the two sources it names, whatever
            # it means: they are not also reported as a cross join.
            named = [s for side in sides for s in select.sources if side.table and s.alias == side.table.lower()]
            for other in named[1:]:
                linked.join(id(named[0]), id(other))
            return self.skip("unresolved_column", condition)
        if any(found.correlated for found in resolved):
            # A reference outwards: the subquery it sits in is reported whole.
            return None
        left, right = resolved
        if left.source is right.source:
            return None
        linked.join(id(left.source), id(right.source))

        traced = [trace(found, self.schema) for found in resolved]
        for kind, reason in (
            ("opaque", "unread_source"),
            ("unresolved", "unresolved_column"),
            ("set_operation", "set_operation"),
            ("computed", "computed_join_key"),
        ):
            if any(end.kind == kind for end in traced):
                return self.skip(reason, condition)
        # A column out of a UNION is one column for each branch.
        lefts, rights = (end.branches if end.kind == "branches" else (end,) for end in traced)
        union = any(end.kind == "branches" for end in traced)
        if any(first.source is second.source for first in lefts for second in rights):
            # Two names for one use of a table: a CTE joined to itself.
            return self.skip("self_join", condition)

        for first in lefts:
            for second in rights:
                a, b = sorted(((first.table, first.column), (second.table, second.column)))
                equality = Equality(a, b, outer, union)
                self.equalities.setdefault((equality.pair, outer, union), equality)
                self.sources[id(first.source)] = first.source
                self.sources[id(second.source)] = second.source
                if not equality.direct:
                    self.inner.join((id(first.source), first.column), (id(second.source), second.column))
        return None

    def _spanning(self, condition, select: Select, linked: _Classes) -> str | None:
        """A condition that is not a plain column equality. If a comparison
        inside it sets columns of two sources against each other it is a
        join this cannot read: say why. Otherwise it is a filter: None."""
        found: str | None = None
        for node in own_nodes(condition):
            if not isinstance(node, _COMPARISONS) or isinstance(node, exp.Connector):
                continue
            sources = set()
            for column in (inner for inner in own_nodes(node) if isinstance(inner, exp.Column)):
                resolved = resolve(column, select, self.schema)
                if resolved.kind == "found" and not resolved.correlated:
                    sources.add(id(resolved.source))
                    self.sources[id(resolved.source)] = resolved.source
            if len(sources) < 2:
                continue
            ordered = sorted(sources)
            for other in ordered[1:]:
                linked.join(ordered[0], other)
            if isinstance(condition, exp.Or):
                found = "or"
            elif isinstance(condition, exp.Not):
                found = "negated"
            elif isinstance(node, exp.EQ):
                found = found or "computed_join_key"
            else:
                found = found or "non_equality"
        return found

    # ---- subqueries in expressions --------------------------------------

    def _subquery(self, body: Body, select: Select) -> None:
        if self._correlated(body):
            return self.skip("correlated_subquery", body.node)
        if isinstance(body.node.parent, exp.Exists):
            return None
        if body.select is None:
            # `x IN (SELECT .. UNION SELECT ..)`: a join spelled as a filter,
            # over several tables at once.
            return self.skip("subquery_predicate", body.node)
        # `x IN (SELECT key FROM t)` is a join spelled as a filter. A
        # subquery that gives out a value it computed is only a value.
        first = outputs(body, self.schema)[:1]
        for out in first:
            expression = out.expression
            while isinstance(expression, exp.Paren):
                expression = expression.this
            if out.source is not None or isinstance(expression, exp.Column):
                self.skip("subquery_predicate", body.node)
        return None

    def _correlated(self, body: Body) -> bool:
        selects = [body.select] if body.select is not None else []
        selects += [branch.select for branch in body.branches if branch.select is not None]
        for select in selects:
            for node in own_nodes(select.node):
                if isinstance(node, exp.Column) and resolve(node, select, self.schema).correlated:
                    return True
            if any(self._correlated(inner) for inner in select.subqueries):
                return True
            for source in select.sources:
                if source.kind == "derived" and source.cte is None and self._correlated(source.body):
                    return True
        return False

    # ---- cross joins -----------------------------------------------------

    def _cross_joins(self, select: Select, linked: _Classes) -> None:
        counted = [source for source in select.sources if source.kind != "unread" and not _one_row(source)]
        groups: dict = {}
        for source in counted:
            groups.setdefault(linked.find(id(source)), []).append(source.table or source.alias)
        if len(groups) > 1:
            self.cross_joins.append(tuple(" + ".join(sorted(names)) for names in groups.values()))

    # ---- the result ------------------------------------------------------

    def result(self) -> Extraction:
        classes = []
        for members in self.inner.classes():
            classes.append(frozenset((self.sources[source].table, column) for source, column in members))
        return Extraction(
            equalities=tuple(sorted(self.equalities.values(), key=lambda e: (e.left, e.right, e.outer, e.union))),
            classes=tuple(sorted(classes, key=sorted)),
            tables=tuple(sorted(self.tables)),
            cross_joins=tuple(self.cross_joins),
            unchecked=tuple(self.unchecked.values()),
        )


_COMPARISONS = (exp.Binary, exp.Between, exp.In)


def _conjuncts(expression):
    while isinstance(expression, exp.Paren):
        expression = expression.this
    if isinstance(expression, exp.And):
        yield from _conjuncts(expression.left)
        yield from _conjuncts(expression.right)
    else:
        yield expression


def _column_equality(condition) -> tuple[exp.Column, exp.Column] | None:
    if not isinstance(condition, exp.EQ):
        return None
    sides = []
    for side in (condition.left, condition.right):
        while isinstance(side, exp.Paren):
            side = side.this
        if not isinstance(side, exp.Column):
            return None
        sides.append(side)
    return sides[0], sides[1]


def _one_row(source: Source) -> bool:
    """A derived source that is one row by construction: aggregates, no
    GROUP BY, and nothing in its select list that returns a set of rows.
    Setting it beside a table joins nothing wrongly."""
    if source.kind != "derived" or source.body.select is None:
        return False
    node = source.body.select.node
    if node.args.get("group") is not None or not node.expressions:
        return False
    if any(_returns_rows(inner) for projection in node.expressions for inner in projection.walk()):
        return False
    return all(
        projection.find(exp.AggFunc) is not None and projection.find(exp.Window) is None
        for projection in node.expressions
    )


# Functions that return a set of rows and not a value. One in a select
# list multiplies the rows, whatever aggregate stands beside it.
_ROW_RETURNING = (exp.Unnest, exp.Explode, exp.GenerateSeries, exp.ExplodingGenerateSeries)
_ROW_RETURNING_NAMES = (
    "generate_", "unnest", "regexp_split_to_table", "regexp_matches", "string_to_table",
    "json_array_elements", "jsonb_array_elements", "json_each", "jsonb_each", "json_object_keys",
    "jsonb_object_keys", "jsonb_path_query",
)  # fmt: skip


def _returns_rows(node) -> bool:
    if isinstance(node, _ROW_RETURNING):
        return True
    return isinstance(node, exp.Anonymous) and node.name.lower().startswith(_ROW_RETURNING_NAMES)


def extract_joins(sql: str, schema: Schema) -> Extraction:
    """The joins one SQL statement performs. Needs no path."""
    try:
        statements = parse_statements(sql)
    except SqlNotParsed:
        return Extraction((), (), (), (), (Unchecked("not_parsed", ""),))
    if len(statements) != 1:
        return Extraction((), (), (), (), (Unchecked("not_one_statement", f"{len(statements)} statements"),))
    reading: Reading = read(statements[0], schema)
    if reading.root.select is None and not reading.root.branches:
        # Nothing was read out of it, which must never look like "no joins".
        return Extraction((), (), (), (), (Unchecked("not_a_select", type(reading.root.node).__name__.upper()),))
    extractor = _Extractor(schema)
    extractor.body(reading.root)
    return extractor.result()


# --------------------------------------------------------------------------
# Comparison
# --------------------------------------------------------------------------


def compare(extraction: Extraction, edges: tuple[Edge, ...]) -> Conformance:
    """The SQL's joins against a path's, by meaning."""
    path = _Classes()
    path_pairs: set[frozenset[Column]] = set()
    for edge in edges:
        for a, b in edge:
            path.join(a, b)
            path_pairs.add(frozenset((a, b)))
    direct_pairs = {equality.pair for equality in extraction.equalities if equality.direct}

    def joined(a: Column, b: Column) -> bool:
        return any(a in members and b in members for members in extraction.classes) or frozenset((a, b)) in direct_pairs

    present, missing, partial = [], [], []
    for edge in edges:
        held = [joined(a, b) for a, b in edge]
        (present if all(held) else partial if any(held) else missing).append(edge)

    foreign = []
    for equality in extraction.equalities:
        if equality.direct:
            implied = equality.pair in path_pairs
        else:
            # A column the path does not mention is in a class of its own.
            implied = path.find(equality.left) == path.find(equality.right)
        if not implied:
            foreign.append(equality)

    # A key that looks half there may have its other half inside something
    # that could not be read. Then it is not known to be wrong.
    half_joined = bool(partial) and not extraction.unchecked
    outcome: Outcome
    if foreign or extraction.cross_joins or half_joined:
        outcome = "diverged"
    elif extraction.unchecked:
        outcome = "not_checked"
    elif missing:
        outcome = "incomplete"
    else:
        outcome = "conforms"

    findings = (
        [f"joins outside the path: {_show(equality)}" for equality in foreign]
        + [f"only part of a two-column key is joined: {_edge(edge)}" for edge in partial]
        + [f"joined to nothing (a cross join): {' | '.join(groups)}" for groups in extraction.cross_joins]
        + [f"not checked ({item.reason}): {item.detail}" for item in extraction.unchecked]
        + [f"an edge of the path is not used: {_edge(edge)}" for edge in missing]
    )
    return Conformance(
        outcome, tuple(present), tuple(missing), tuple(partial), tuple(foreign),
        extraction.cross_joins, extraction.unchecked, tuple(findings),
    )  # fmt: skip


def _name(column: Column) -> str:
    return f"{column[0]}.{column[1]}"


def _show(equality: Equality) -> str:
    how = (" (outer join)" if equality.outer else "") + (" (through a UNION)" if equality.union else "")
    return f"{_name(equality.left)} = {_name(equality.right)}{how}"


def _edge(edge: Edge) -> str:
    return " and ".join(f"{_name(a)} = {_name(b)}" for a, b in edge)
