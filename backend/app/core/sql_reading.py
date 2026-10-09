"""Reading a SQL statement: which tables it names, under which aliases,
and which table and column each column reference really means (FR-18).

Pure (DD-01): text in, a structure out. Nothing here judges anything. The
SqlValidator and the ConformanceCheck both stand on it, so that "this
column belongs to that table" is worked out once and in one way.

sqlglot parses; this module adds the one thing sqlglot cannot know without
a schema, which is where an unqualified column comes from, and follows a
column through a CTE or a derived table back to the table it was read
from.

THE WORDS USED HERE

    a select    one SELECT, with the sources in its FROM and JOINs
    a source    one entry of a FROM: a table of the schema, a derived
                source (a CTE or a subquery in FROM), or something this
                module does not read (VALUES, a function, LATERAL)
    a body      what a derived source or a subquery is made of: a select,
                or a set operation, which is held with its branches and
                not read further
    to resolve  to find the source a column reference belongs to, looking
                in its own select first and then outwards (a reference
                found outwards is correlated)
    to trace    to follow a resolved column through derived sources down
                to a column of a table. A derived column that is anything
                but another column passed straight through is `computed`
                and the trace stops there

A NAME IS COMPARED IN LOWER CASE, as Postgres folds an unquoted one.
"""

from dataclasses import dataclass, field
from typing import Literal

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

DIALECT = "postgres"

# table name -> its column names, in order. All the reader needs of a schema.
Schema = dict[str, tuple[str, ...]]


class SqlNotParsed(ValueError):
    """The text is not SQL this dialect's grammar accepts."""


def schema_of(snapshot) -> Schema:
    """The tables and columns of a SchemaSnapshot, as the reader wants them."""
    columns: dict[str, list[str]] = {table.name: [] for table in snapshot.tables}
    for column in snapshot.columns:
        columns[column.table].append(column.name)
    return {table: tuple(names) for table, names in columns.items()}


def parse_statements(sql: str) -> tuple[exp.Expression, ...]:
    """Every statement in the text. An empty one, as after a trailing
    semicolon, is not counted."""
    try:
        parsed = sqlglot.parse(sql, read=DIALECT)
    except SqlglotError as error:
        raise SqlNotParsed(_first_line(str(error))) from None
    return tuple(statement for statement in parsed if statement is not None)


def _first_line(text: str) -> str:
    return text.strip().split("\n")[0][:300]


@dataclass(eq=False)
class Source:
    alias: str
    kind: Literal["table", "derived", "unread"]
    # For a table: its name and the schema it was written with, if any.
    table: str | None = None
    qualifier: str | None = None
    # For a derived source: what it is made of, and its name if it is a CTE.
    body: "Body | None" = None
    cte: str | None = None
    # `AS d(x, y)`: the names the source gives its columns, by position.
    renamed: tuple[str, ...] = ()
    # Why an unread source is unread, in a word.
    unread: str | None = None


@dataclass(eq=False)
class Join:
    """One JOIN, or one more comma in a FROM."""

    source: Source
    # LEFT, RIGHT or FULL make it an outer join.
    outer: bool
    natural: bool
    using: bool
    on: exp.Expression | None


@dataclass(eq=False)
class Select:
    node: exp.Select
    parent: "Select | None"
    role: Literal["root", "cte", "derived", "subquery", "branch"]
    sources: list[Source] = field(default_factory=list)
    joins: list[Join] = field(default_factory=list)
    # Subqueries met in its expressions: in WHERE, in the select list.
    subqueries: list["Body"] = field(default_factory=list)


@dataclass(eq=False)
class Body:
    node: exp.Expression
    # One select; or, for a set operation, none here and its branches below.
    select: Select | None = None
    branches: tuple["Body", ...] = ()
    recursive: bool = False

    @property
    def is_set_operation(self) -> bool:
        return isinstance(self.node, exp.SetOperation)


@dataclass(eq=False)
class Reading:
    root: Body
    # Every select in the statement, CTE bodies nothing refers to included.
    selects: list[Select]
    # Every body read: the root, each CTE, each derived table, each subquery.
    bodies: list[Body]


@dataclass(frozen=True, eq=False)
class Out:
    """One output column of a body."""

    name: str | None
    # The expression that produces it, with the select it is written in ...
    expression: exp.Expression | None = None
    select: Select | None = None
    # ... or, where a star produced it, the source and column it came from.
    source: Source | None = None
    column: str | None = None


@dataclass(frozen=True, eq=False)
class Resolved:
    kind: Literal["found", "unknown", "ambiguous", "opaque"]
    source: Source | None = None
    column: str | None = None
    # Found in an enclosing select, not its own.
    correlated: bool = False
    candidates: tuple[str, ...] = ()


@dataclass(frozen=True, eq=False)
class Traced:
    kind: Literal["column", "computed", "set_operation", "unresolved", "opaque"]
    # For a column: the source it was finally read from, and what it is.
    source: Source | None = None
    table: str | None = None
    column: str | None = None


def _is_query(node: exp.Expression) -> bool:
    return isinstance(node, exp.Select | exp.SetOperation)


def own_nodes(root: exp.Expression):
    """Every node of a query that is not inside a query nested in it. The
    nested queries themselves are yielded, and not entered."""
    for node in root.walk(prune=lambda node: node is not root and _is_query(node)):
        yield node


def read(statement: exp.Expression, schema: Schema) -> Reading:
    reading = Reading(root=None, selects=[], bodies=[])  # type: ignore[arg-type]
    reading.root = _read_body(_unwrap(statement), None, {}, "root", reading)
    return reading


def _unwrap(node: exp.Expression) -> exp.Expression:
    while isinstance(node, exp.Subquery | exp.Paren) and node.this is not None:
        node = node.this
    return node


def _read_body(node, parent, ctes, role, reading) -> Body:
    body = Body(node=node)
    reading.bodies.append(body)
    if isinstance(node, exp.SetOperation):
        ctes = _with(node, parent, ctes, reading)
        body.branches = tuple(
            _read_body(_unwrap(side), parent, ctes, "branch", reading) for side in (node.left, node.right)
        )
    elif isinstance(node, exp.Select):
        ctes = _with(node, parent, ctes, reading)
        body.select = _read_select(node, parent, ctes, role, reading)
    return body


def _with(node, parent, ctes, reading) -> dict[str, Body]:
    """The CTEs this query defines, added to those it could already see.
    Each is read where it is defined, once, whoever refers to it."""
    clause = node.args.get("with_")
    if clause is None:
        return ctes
    ctes = dict(ctes)
    for cte in clause.expressions:
        name = cte.alias.lower()
        if clause.args.get("recursive"):
            # Its own name is in scope inside it. It is held unread: a
            # recursive query is not something a join can be read out of.
            placeholder = Body(node=_unwrap(cte.this), recursive=True)
            ctes[name] = placeholder
            inner = _read_body(_unwrap(cte.this), parent, ctes, "cte", reading)
            placeholder.select, placeholder.branches = inner.select, inner.branches
            reading.bodies.remove(inner)
            reading.bodies.append(placeholder)
        else:
            ctes[name] = _read_body(_unwrap(cte.this), parent, ctes, "cte", reading)
    return ctes


def _read_select(node, parent, ctes, role, reading) -> Select:
    select = Select(node=node, parent=parent, role=role)
    reading.selects.append(select)

    first = node.args.get("from_")
    if first is not None:
        select.sources.append(_source(first.this, select, ctes, reading))
    for join in node.args.get("joins") or []:
        source = _source(join.this, select, ctes, reading)
        select.sources.append(source)
        select.joins.append(
            Join(
                source=source,
                outer=(join.side or "").upper() in ("LEFT", "RIGHT", "FULL"),
                natural=(join.method or "").upper() == "NATURAL",
                using=bool(join.args.get("using")),
                on=join.args.get("on"),
            )
        )

    for nested in own_nodes(node):
        if nested is node or not _is_query(nested):
            continue
        holder = nested.parent
        if isinstance(holder, exp.CTE):
            continue
        if isinstance(holder, exp.Subquery) and isinstance(holder.parent, exp.From | exp.Join | exp.Lateral):
            continue
        select.subqueries.append(_read_body(nested, select, ctes, "subquery", reading))
    return select


def _source(node, select, ctes, reading) -> Source:
    alias = (node.alias_or_name or "").lower()
    renamed = ()
    table_alias = node.args.get("alias")
    if isinstance(table_alias, exp.TableAlias):
        renamed = tuple(column.name.lower() for column in table_alias.columns)

    if isinstance(node, exp.Table) and isinstance(node.this, exp.Identifier):
        name = node.name.lower()
        qualifier = ".".join(part.lower() for part in (node.catalog, node.db) if part) or None
        if qualifier is None and name in ctes:
            return Source(alias, "derived", body=ctes[name], cte=name, renamed=renamed)
        return Source(alias, "table", table=name, qualifier=qualifier, renamed=renamed)
    if isinstance(node, exp.Subquery) and _is_query(_unwrap(node)):
        # A derived table sees no column of the select it sits in.
        body = _read_body(_unwrap(node), select.parent, ctes, "derived", reading)
        return Source(alias, "derived", body=body, renamed=renamed)
    return Source(alias, "unread", unread=type(node).__name__.lower(), renamed=renamed)


# --------------------------------------------------------------------------
# What a body gives out, and what a column means
# --------------------------------------------------------------------------


def outputs(body: Body, schema: Schema) -> tuple[Out, ...]:
    """The output columns of a body, in order. A set operation's are named
    by its first branch and are nobody's column in particular."""
    if body.select is None:
        if body.branches:
            return tuple(Out(out.name) for out in outputs(body.branches[0], schema))
        return ()
    select = body.select
    outs: list[Out] = []
    for projection in select.node.expressions:
        inner = projection.unalias()
        if isinstance(inner, exp.Star):
            for source in select.sources:
                outs += [Out(name, source=source, column=name) for name in source_columns(source, schema)]
        elif isinstance(inner, exp.Column) and isinstance(inner.this, exp.Star):
            for source in select.sources:
                if source.alias == inner.table.lower():
                    outs += [Out(name, source=source, column=name) for name in source_columns(source, schema)]
        else:
            name = projection.alias_or_name
            outs.append(Out(name.lower() if name else None, expression=inner, select=select))
    return tuple(outs)


def source_columns(source: Source, schema: Schema) -> tuple[str, ...]:
    if source.kind == "table":
        names = schema.get(source.table or "", ()) if source.qualifier in (None, "public") else ()
    elif source.kind == "derived":
        names = tuple(out.name or "" for out in outputs(source.body, schema))
    else:
        names = ()
    return source.renamed + names[len(source.renamed) :]


def resolve(column: exp.Column, select: Select, schema: Schema) -> Resolved:
    """The source a column reference belongs to: in its own select if it
    can be, otherwise in the nearest enclosing one."""
    name = column.name.lower()
    qualifier = column.table.lower() if column.table else None
    star = isinstance(column.this, exp.Star)
    scope: Select | None = select
    while scope is not None:
        correlated = scope is not select
        if qualifier is not None:
            for source in scope.sources:
                if source.alias != qualifier:
                    continue
                if source.kind == "unread":
                    return Resolved("opaque", source, name, correlated)
                if star or name in source_columns(source, schema):
                    return Resolved("found", source, name, correlated)
                return Resolved("unknown")
        else:
            holders = [source for source in scope.sources if name in source_columns(source, schema)]
            if len(holders) > 1:
                return Resolved("ambiguous", candidates=tuple(source.alias for source in holders))
            if holders:
                return Resolved("found", holders[0], name, correlated)
            unread = [source for source in scope.sources if source.kind == "unread"]
            if unread:
                return Resolved("opaque", unread[0], name, correlated)
        scope = scope.parent
    return Resolved("unknown")


def trace(resolved: Resolved, schema: Schema) -> Traced:
    """Follow a resolved column down to a column of a table, through any
    derived source that passes it straight through."""
    if resolved.kind == "opaque":
        return Traced("opaque")
    if resolved.kind != "found":
        return Traced("unresolved")
    source, name = resolved.source, resolved.column
    while True:
        if source.kind == "table":
            return Traced("column", source, source.table, name)
        if source.kind == "unread":
            return Traced("opaque")
        body = source.body
        if body.recursive or body.select is None:
            return Traced("set_operation")
        names = source_columns(source, schema)
        if name not in names:
            return Traced("unresolved")
        out = outputs(body, schema)[names.index(name)]
        if out.source is not None:
            source, name = out.source, out.column
            continue
        expression = out.expression
        while isinstance(expression, exp.Paren):
            expression = expression.this
        if not isinstance(expression, exp.Column):
            return Traced("computed")
        inner = resolve(expression, out.select, schema)
        if inner.kind == "opaque":
            return Traced("opaque")
        if inner.kind != "found" or inner.correlated:
            return Traced("unresolved")
        source, name = inner.source, inner.column
