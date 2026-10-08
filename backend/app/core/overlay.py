"""The overlay: facts about a warehouse that its catalog cannot supply (DD-16).

Two things happen here, both pure (DD-01):

  parse_overlay   YAML text -> an Overlay value. Reading the file is the
                  shell's job; this takes the text it read.
  apply_overlay   snapshot + Overlay -> a new snapshot, with the overlay's
                  foreign keys merged in and every readable name filled.

The merge rule (FR-43): catalog edges win. An overlay relationship the
catalog already declares adds nothing and the edge stays `source: catalog`.
One the catalog does not declare is added as `source: overlay`. Either way
every edge says where it came from.

Both functions are strict. A section this code does not understand, a
relationship that is not `table.column`, or one that names a column the
warehouse does not have, raises. Passing over any of them would produce a
graph that is quietly missing an edge or quietly holding a wrong one, and
nothing downstream would say so (T-01).
"""

from dataclasses import dataclass, field, replace

import yaml

from app.core.naming import Naming, describe_column, describe_table, readable_column, readable_table
from app.core.snapshot import ForeignKey, SchemaSnapshot

# The `preferences` section of DD-16 arrives with path selection at step 4.
# Until then it is not understood here, and so it is refused, not ignored.
_SECTIONS = {"relationships", "naming"}
_NAMING_SECTIONS = {"prefixes", "words"}


@dataclass(frozen=True)
class Relationship:
    from_table: str
    from_column: str
    to_table: str
    to_column: str


@dataclass(frozen=True)
class Overlay:
    relationships: tuple[Relationship, ...] = ()
    naming: Naming = field(default_factory=Naming)


def parse_overlay(text: str) -> Overlay:
    document = yaml.safe_load(text)
    if document is None:
        return Overlay()
    if not isinstance(document, dict):
        raise ValueError("overlay: the file must be a mapping of sections")
    _only(document, _SECTIONS, "overlay")

    relationships = [_relationship(entry) for entry in document.get("relationships") or []]
    repeated = sorted({f"{r.from_table}.{r.from_column}" for r in relationships if relationships.count(r) > 1})
    if repeated:
        raise ValueError(f"overlay: relationship listed more than once: {repeated}")

    naming = document.get("naming") or {}
    if not isinstance(naming, dict):
        raise ValueError("overlay: naming must be a mapping")
    _only(naming, _NAMING_SECTIONS, "overlay naming")

    return Overlay(
        relationships=tuple(relationships),
        naming=Naming(
            prefixes=_strings(naming.get("prefixes"), "naming.prefixes"),
            words=_strings(naming.get("words"), "naming.words"),
        ),
    )


def _only(mapping: dict, allowed: set[str], where: str) -> None:
    unknown = sorted(str(key) for key in mapping if key not in allowed)
    if unknown:
        raise ValueError(f"{where}: sections not understood: {unknown}; expected only {sorted(allowed)}")


def _relationship(entry: object) -> Relationship:
    if not isinstance(entry, dict) or set(entry) != {"from", "to"}:
        raise ValueError(f"overlay: a relationship needs exactly `from` and `to`, got {entry!r}")
    ends = []
    for side in ("from", "to"):
        value = entry[side]
        parts = value.split(".") if isinstance(value, str) else []
        if len(parts) != 2 or not all(parts):
            raise ValueError(f"overlay: `{side}` must be table.column, got {value!r}")
        ends.extend(parts)
    return Relationship(*ends)


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
        for table, column in (
            (relationship.from_table, relationship.from_column),
            (relationship.to_table, relationship.to_column),
        ):
            if (table, column) not in columns_known:
                raise ValueError(f"overlay: {table}.{column} is not a column of this warehouse")

        identity = (
            relationship.from_table,
            (relationship.from_column,),
            relationship.to_table,
            (relationship.to_column,),
        )
        if identity in declared:
            continue  # the catalog wins
        added.append(ForeignKey(*identity, source="overlay"))

    tables = []
    for table in snapshot.tables:
        readable = readable_table(table.name, table.comment, overlay.naming)
        tables.append(replace(table, readable=readable, description=describe_table(table.name, readable)))

    columns = []
    for column in snapshot.columns:
        readable = readable_column(column.name, column.comment, overlay.naming)
        description = describe_column(column.table, column.data_type, readable)
        columns.append(replace(column, readable=readable, description=description))

    return replace(
        snapshot,
        tables=tuple(tables),
        columns=tuple(columns),
        foreign_keys=snapshot.foreign_keys + tuple(added),
    )
