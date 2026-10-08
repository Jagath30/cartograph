"""The schema snapshot: what the warehouse declares, as flat facts.

This is the value that crosses from the shell to the core. SchemaIngestor
produces one; GraphBuilder consumes one. It is deliberately flat -- four
lists, no nesting, no graph -- so that it can be written out by hand in a
test fixture and read at a glance (DD-01).

Everything here is frozen: a snapshot is a record of what was read, and
nothing downstream may quietly change it. The overlay is applied by
building a new snapshot, never by editing this one.
"""

from dataclasses import dataclass
from typing import Literal

# Where a foreign key came from (FR-43). `catalog`: the database declares it
# as a constraint. `overlay`: a human asserted it in the overlay file.
Source = Literal["catalog", "overlay"]


@dataclass(frozen=True)
class Table:
    name: str
    comment: str | None = None
    # Filled in when the overlay is applied (DD-08). `readable` is the short
    # name a sentence can use; `description` is the longer text that will be
    # embedded at step 6.
    readable: str = ""
    description: str = ""


@dataclass(frozen=True)
class Column:
    table: str
    name: str
    # As Postgres itself formats it: "bigint", "numeric(7,2)".
    data_type: str
    nullable: bool = True
    comment: str | None = None
    readable: str = ""
    description: str = ""


@dataclass(frozen=True)
class PrimaryKey:
    table: str
    columns: tuple[str, ...]


@dataclass(frozen=True)
class ForeignKey:
    """`from` is the referencing side, `to` the referenced side. The two
    column tuples pair up by position, which is what lets a composite key
    be recorded even though TPC-DS has none."""

    from_table: str
    from_columns: tuple[str, ...]
    to_table: str
    to_columns: tuple[str, ...]
    source: Source
    # The constraint's name in the catalog. An overlay edge has none.
    name: str | None = None


@dataclass(frozen=True)
class SchemaSnapshot:
    tables: tuple[Table, ...]
    columns: tuple[Column, ...]
    primary_keys: tuple[PrimaryKey, ...]
    foreign_keys: tuple[ForeignKey, ...]
