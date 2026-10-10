"""The overlay: facts about a warehouse that its catalog cannot supply (DD-16).

Two things happen here, both pure (DD-01):

  parse_overlay   YAML text -> an Overlay value. Reading the file is the
                  shell's job; this takes the text it read.
  apply_overlay   snapshot + Overlay -> a new snapshot, with the overlay's
                  foreign keys merged in, every readable name filled, and
                  its preferences checked against the merged keys.

The merge rule (FR-43): catalog edges win. An overlay relationship the
catalog already declares adds nothing and the edge stays `source: catalog`.
One the catalog does not declare is added as `source: overlay`. Either way
every edge says where it came from.

A preference (DD-12) names one route between two tables by its exact
edges. It is only ever a tie-breaker, and whether it applies is decided by
the PathFinder; what is decided here is whether it is well-formed and
whether every edge it names is a real foreign key of this warehouse.

A preference of the second kind names two tables, `attach_to` and
`rather_than`: where a table could be attached to either at equal length,
it is attached to the first. It is the join tree's to apply. Checked here:
both are tables of this warehouse, and a foreign key joins them directly.
An entry is one kind or the other, never a mixture.

A relationship's `from` and `to` are each one `table.column`, or a list of
them for a key of several columns -- the same number on each side, paired
by position, each side within one table. An optional `note` says why a
human asserted it, and travels with the edge into every explanation.

Both functions are strict. A section this code does not understand, a
relationship that is not `table.column`, or one that names a column the
warehouse does not have, raises. Passing over any of them would produce a
graph that is quietly missing an edge or quietly holding a wrong one, and
nothing downstream would say so (T-01).
"""

from dataclasses import dataclass, field, replace

import yaml

from app.core.naming import Naming, describe_column, describe_table, readable_column, readable_table
from app.core.snapshot import AttachPreference, ForeignKey, Preference, SchemaSnapshot

_SECTIONS = {"relationships", "naming", "preferences"}
_NAMING_SECTIONS = {"prefixes", "words"}


@dataclass(frozen=True)
class Relationship:
    from_table: str
    from_columns: tuple[str, ...]
    to_table: str
    to_columns: tuple[str, ...]
    note: str | None = None

    @property
    def identity(self) -> tuple:
        """What makes two relationships the same edge. The note does not."""
        return (self.from_table, self.from_columns, self.to_table, self.to_columns)

    @property
    def edges(self) -> tuple[tuple[str, str], ...]:
        return tuple(
            (f"{self.from_table}.{start}", f"{self.to_table}.{end}")
            for start, end in zip(self.from_columns, self.to_columns)
        )


@dataclass(frozen=True)
class Overlay:
    relationships: tuple[Relationship, ...] = ()
    naming: Naming = field(default_factory=Naming)
    preferences: tuple[Preference, ...] = ()
    attach_preferences: tuple[AttachPreference, ...] = ()


def parse_overlay(text: str) -> Overlay:
    document = yaml.safe_load(text)
    if document is None:
        return Overlay()
    if not isinstance(document, dict):
        raise ValueError("overlay: the file must be a mapping of sections")
    _only(document, _SECTIONS, "overlay")

    relationships = [_relationship(entry) for entry in document.get("relationships") or []]
    identities = [relationship.identity for relationship in relationships]
    repeated = sorted({str(identity) for identity in identities if identities.count(identity) > 1})
    if repeated:
        raise ValueError(f"overlay: relationship listed more than once: {repeated}")

    naming = document.get("naming") or {}
    if not isinstance(naming, dict):
        raise ValueError("overlay: naming must be a mapping")
    _only(naming, _NAMING_SECTIONS, "overlay naming")

    declared = document.get("preferences") or []
    if not isinstance(declared, list):
        raise ValueError("overlay: preferences must be a list")
    attaching = [_attach_preference(entry) for entry in declared if isinstance(entry, dict) and "attach_to" in entry]
    places = [frozenset((entry.attach_to, entry.rather_than)) for entry in attaching]
    twice = sorted({tuple(sorted(place)) for place in places if places.count(place) > 1})
    if twice:
        raise ValueError(f"overlay: more than one preference about attaching to the same two tables: {twice}")

    preferences = [_preference(entry) for entry in declared if not (isinstance(entry, dict) and "attach_to" in entry)]
    pairs = [preference.between for preference in preferences]
    contested = sorted({pair for pair in pairs if pairs.count(pair) > 1})
    if contested:
        raise ValueError(f"overlay: more than one preference between the same two tables: {contested}")

    return Overlay(
        relationships=tuple(relationships),
        naming=Naming(
            prefixes=_strings(naming.get("prefixes"), "naming.prefixes"),
            words=_strings(naming.get("words"), "naming.words"),
        ),
        preferences=tuple(preferences),
        attach_preferences=tuple(attaching),
    )


def _attach_preference(entry: dict) -> AttachPreference:
    if set(entry) != {"attach_to", "rather_than", "because"}:
        raise ValueError(
            f"overlay: a preference between two places needs exactly `attach_to`, `rather_than` and `because`, got {entry!r}"
        )
    attach_to, rather_than, because = entry["attach_to"], entry["rather_than"], entry["because"]
    for name in (attach_to, rather_than):
        if not isinstance(name, str) or not name or "." in name:
            raise ValueError(f"overlay: `attach_to` and `rather_than` each name one table, got {name!r}")
    if attach_to == rather_than:
        raise ValueError(f"overlay: `attach_to` and `rather_than` name the same table, {attach_to}")
    if not isinstance(because, str) or not because.strip():
        raise ValueError(f"overlay: a preference for {attach_to} over {rather_than} must say `because` of what")
    return AttachPreference(attach_to, rather_than, " ".join(because.split()))


def _only(mapping: dict, allowed: set[str], where: str) -> None:
    unknown = sorted(str(key) for key in mapping if key not in allowed)
    if unknown:
        raise ValueError(f"{where}: sections not understood: {unknown}; expected only {sorted(allowed)}")


def _relationship(entry: object, note_allowed: bool = True) -> Relationship:
    allowed = {"from", "to", "note"} if note_allowed else {"from", "to"}
    if not isinstance(entry, dict) or not {"from", "to"} <= set(entry) <= allowed:
        raise ValueError(f"overlay: a relationship needs `from` and `to` and may have a `note`, got {entry!r}")

    sides = []
    for side in ("from", "to"):
        value = entry[side]
        names = [value] if isinstance(value, str) else value
        if not isinstance(names, list) or not names:
            raise ValueError(f"overlay: `{side}` must be table.column or a list of them, got {value!r}")
        tables, columns = set(), []
        for name in names:
            parts = name.split(".") if isinstance(name, str) else []
            if len(parts) != 2 or not all(parts):
                raise ValueError(f"overlay: `{side}` must be table.column, got {name!r}")
            tables.add(parts[0])
            columns.append(parts[1])
        if len(tables) != 1 or len(set(columns)) != len(columns):
            raise ValueError(f"overlay: the columns of `{side}` must be different columns of one table, got {value!r}")
        sides.append((tables.pop(), tuple(columns)))

    (from_table, from_columns), (to_table, to_columns) = sides
    if len(from_columns) != len(to_columns):
        raise ValueError(f"overlay: `from` and `to` must list the same number of columns, got {entry!r}")

    note = entry.get("note")
    if note is not None and (not isinstance(note, str) or not note.strip()):
        raise ValueError(f"overlay: a `note` must be text, got {note!r}")
    return Relationship(from_table, from_columns, to_table, to_columns, " ".join(note.split()) if note else None)


def _preference(entry: object) -> Preference:
    if not isinstance(entry, dict) or set(entry) != {"between", "prefer", "because"}:
        raise ValueError(f"overlay: a preference needs exactly `between`, `prefer` and `because`, got {entry!r}")

    between = entry["between"]
    if (
        not isinstance(between, list)
        or len(between) != 2
        or not all(isinstance(table, str) and table for table in between)
        or between[0] == between[1]
    ):
        raise ValueError(f"overlay: `between` must name two different tables, got {between!r}")

    prefer = entry["prefer"]
    if not isinstance(prefer, list) or not prefer:
        raise ValueError(f"overlay: `prefer` must list the edges of one route, got {prefer!r}")
    edges = [edge for entry in prefer for edge in _relationship(entry, note_allowed=False).edges]

    because = entry["because"]
    if not isinstance(because, str) or not because.strip():
        raise ValueError(f"overlay: a preference between {between} must say `because` of what")

    first, second = sorted(between)
    return Preference(between=(first, second), prefer=tuple(edges), because=" ".join(because.split()))


def _strings(mapping: object, where: str) -> dict[str, str]:
    """A mapping of text to text, and nothing else.

    The check earns its place: YAML reads a bare `on`, `no` or `yes` as a
    boolean, so `on: on` would arrive here as {True: True} and the word
    would silently never match. Quote such a key in the file.
    """
    if mapping is None:
        return {}
    if not isinstance(mapping, dict):
        raise ValueError(f"overlay: {where} must be a mapping")
    for key, value in mapping.items():
        if not isinstance(key, str) or not isinstance(value, str) or not key or not value.strip():
            raise ValueError(f"overlay: {where} must map text to text, got {key!r}: {value!r}")
    return dict(mapping)


def apply_overlay(snapshot: SchemaSnapshot, overlay: Overlay) -> SchemaSnapshot:
    columns_known = {(column.table, column.name) for column in snapshot.columns}
    declared = {(key.from_table, key.from_columns, key.to_table, key.to_columns) for key in snapshot.foreign_keys}

    added = []
    for relationship in overlay.relationships:
        for table, names in (
            (relationship.from_table, relationship.from_columns),
            (relationship.to_table, relationship.to_columns),
        ):
            for column in names:
                if (table, column) not in columns_known:
                    raise ValueError(f"overlay: {table}.{column} is not a column of this warehouse")

        if relationship.identity in declared:
            continue  # the catalog wins
        added.append(ForeignKey(*relationship.identity, source="overlay", note=relationship.note))

    tables = []
    for table in snapshot.tables:
        readable = readable_table(table.name, table.comment, overlay.naming)
        tables.append(replace(table, readable=readable, description=describe_table(table.name, readable)))

    columns = []
    for column in snapshot.columns:
        readable = readable_column(column.name, column.comment, overlay.naming)
        description = describe_column(column.table, column.data_type, readable)
        columns.append(replace(column, readable=readable, description=description))

    foreign_keys = snapshot.foreign_keys + tuple(added)
    for preference in overlay.preferences:
        _check_preference(preference, foreign_keys)
    known = {table.name for table in snapshot.tables}
    joined = {frozenset((key.from_table, key.to_table)) for key in foreign_keys}
    for attaching in overlay.attach_preferences:
        for name in (attaching.attach_to, attaching.rather_than):
            if name not in known:
                raise ValueError(f"overlay: a preference names {name}, and the warehouse has no table of that name")
        if frozenset((attaching.attach_to, attaching.rather_than)) not in joined:
            raise ValueError(
                f"overlay: no foreign key joins {attaching.attach_to} and {attaching.rather_than} directly, "
                "so a preference between them as places to attach is refused"
            )

    return replace(
        snapshot,
        tables=tuple(tables),
        columns=tuple(columns),
        foreign_keys=foreign_keys,
        preferences=overlay.preferences,
        attach_preferences=overlay.attach_preferences,
    )


def _check_preference(preference: Preference, foreign_keys: tuple[ForeignKey, ...]) -> None:
    """Every edge must be a foreign key of the warehouse, written in its
    real direction, and together they must be one unbroken route from one
    of the two tables to the other. Whether that route is among the tied
    shortest ones is not known here; the PathFinder finds that out.

    A key of several columns is one join, so it must be named whole -- all
    of its column pairs or none -- and it counts as one hop."""
    owner = {}
    for number, key in enumerate(foreign_keys):
        for start, end in zip(key.from_columns, key.to_columns):
            owner[(f"{key.from_table}.{start}", f"{key.to_table}.{end}")] = number
    for edge in preference.prefer:
        if edge not in owner:
            raise ValueError(
                f"overlay: preference between {list(preference.between)} names {edge[0]} -> {edge[1]}, "
                "which is not a foreign key of this warehouse"
            )
    named = {owner[edge] for edge in preference.prefer}
    for number in named:
        key = foreign_keys[number]
        if sum(1 for edge in preference.prefer if owner[edge] == number) != len(key.from_columns):
            raise ValueError(
                f"overlay: preference between {list(preference.between)} names part of the key from "
                f"{key.from_table} to {key.to_table}; a key of several columns must be named whole"
            )

    # Walk from one end, crossing each named key once, in whichever
    # direction it lies. The walk must use them all and stop at the other end.
    hops = [(foreign_keys[number].from_table, foreign_keys[number].to_table) for number in sorted(named)]
    here, goal = preference.between
    visited = {here}
    while hops:
        onward = [hop for hop in hops if here in hop]
        if not onward:
            break
        hop = onward[0]
        hops.remove(hop)
        here = hop[1] if hop[0] == here else hop[0]
        if here in visited:
            hops.append(hop)  # a loop is not a route
            break
        visited.add(here)
    if hops or here != goal:
        raise ValueError(
            f"overlay: preference between {list(preference.between)} does not describe one route "
            "from one of those tables to the other"
        )
