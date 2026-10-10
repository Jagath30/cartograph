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
    # Why a human asserted it. Only an overlay edge can have one: the
    # catalog's edges are declared by the database and need no defence.
    note: str | None = None


@dataclass(frozen=True)
class Preference:
    """A declared answer to a known tie (DD-12, rule 3): between these two
    tables, when several shortest routes exist, this is the one meant.

    The route is named by its exact edges, each a (referencing column,
    referenced column) pair in the graph's own notation --
    ("catalog_sales.cs_bill_addr_sk", "customer_address.ca_address_sk").
    `because` is required: a preference nobody can explain is an assumption
    compiled into a tool that claims to make none.
    """

    between: tuple[str, str]  # the two tables, in alphabetical order
    prefer: tuple[tuple[str, str], ...]
    because: str


@dataclass(frozen=True)
class AttachPreference:
    """A declared answer to a tie between two PLACES (DD-12; ruling C of the
    retrieval pass, 10 October 2026): where a table could be attached to
    either of these two at equal length, attach it to `attach_to`.

    A `Preference` names one route between two tables and cannot say this:
    here the routes end at different tables. It is an operator's default
    and nothing more. It is consulted only after the question's own wording
    has failed to decide, it never chooses between two keys of one table,
    and `because` is required, as for any preference.
    """

    attach_to: str
    rather_than: str
    because: str


@dataclass(frozen=True)
class SchemaSnapshot:
    tables: tuple[Table, ...]
    columns: tuple[Column, ...]
    primary_keys: tuple[PrimaryKey, ...]
    foreign_keys: tuple[ForeignKey, ...]
    # Not a fact about the schema, but validated against it and carried with
    # it, so that whoever holds the graph holds the preferences too.
    preferences: tuple[Preference, ...] = ()
    attach_preferences: tuple[AttachPreference, ...] = ()
