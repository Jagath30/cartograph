"""SchemaIngestor: the warehouse's catalog, read into a flat snapshot (FR-01, FR-02).

Impure, and so in shell/ (DD-01): it opens a database connection and reads
a file. It does not build the graph -- that is app.core.graph_builder, which
takes what this returns. Everything here that is not a read is delegated to
the pure core: the catalog-first merge and the readable names are
app.core.overlay.apply_overlay.

READS pg_catalog, NEVER information_schema. Through
information_schema.table_constraints a role sees only the constraints on
tables it owns or may write to. The warehouse role may only SELECT (NFR-07),
so it sees none: the query succeeds, returns no rows, and the result is a
graph with every table and no edges. Nothing errors. pg_constraint has no
such filter. Step 2 measured it: 102 foreign keys one way, 0 the other.
"""

from pathlib import Path

import psycopg

from app.core.overlay import Overlay, apply_overlay, parse_overlay
from app.core.snapshot import Column, ForeignKey, PrimaryKey, SchemaSnapshot, Table

# Ordinary and partitioned tables, with their columns in declared order.
# Views, sequences and indexes are also rows of pg_class; relkind keeps them
# out. attnum > 0 drops the system columns, attisdropped the deleted ones.
_TABLES_AND_COLUMNS = """
    select rel.relname::text,
           obj_description(rel.oid, 'pg_class'),
           att.attname::text,
           format_type(att.atttypid, att.atttypmod),
           not att.attnotnull,
           col_description(rel.oid, att.attnum)
    from pg_class rel
    join pg_namespace ns on ns.oid = rel.relnamespace
    left join pg_attribute att
           on att.attrelid = rel.oid and att.attnum > 0 and not att.attisdropped
    where ns.nspname = %s and rel.relkind in ('r', 'p')
    order by rel.relname, att.attnum
"""

# Primary ('p') and foreign ('f') key constraints. conkey and confkey hold
# column *numbers*; each is unnested in order and joined to pg_attribute to
# become names, so a composite key keeps its column order.
_KEYS = """
    select con.conname::text,
           con.contype::text,
           rel.relname::text,
           array(select att.attname::text
                 from unnest(con.conkey) with ordinality as k(attnum, position)
                 join pg_attribute att on att.attrelid = con.conrelid and att.attnum = k.attnum
                 order by k.position),
           ref.relname::text,
           array(select att.attname::text
                 from unnest(con.confkey) with ordinality as k(attnum, position)
                 join pg_attribute att on att.attrelid = con.confrelid and att.attnum = k.attnum
                 order by k.position)
    from pg_constraint con
    join pg_class rel on rel.oid = con.conrelid
    join pg_namespace ns on ns.oid = rel.relnamespace
    left join pg_class ref on ref.oid = con.confrelid
    where ns.nspname = %s and con.contype in ('p', 'f')
    order by rel.relname, con.conname
"""


class SuperuserRefused(RuntimeError):
    """The ingestor was given credentials that can do more than read."""


class SchemaIngestor:
    def __init__(self, dsn: str, overlay_path: str | Path | None = None, schema: str = "public") -> None:
        self._dsn = dsn
        self._overlay_path = Path(overlay_path) if overlay_path else None
        self._schema = schema

    def ingest(self) -> SchemaSnapshot:
        """What the catalog declares, with the overlay applied."""
        return apply_overlay(self._read_catalog(), self._read_overlay())

    def _read_overlay(self) -> Overlay:
        if self._overlay_path is None:
            return Overlay()
        return parse_overlay(self._overlay_path.read_text())

    def _read_catalog(self) -> SchemaSnapshot:
        with psycopg.connect(self._dsn, connect_timeout=5) as connection:
            # DD-02, rule 2, made impossible to break by pointing this at
            # the wrong URL: the warehouse is only ever read as a role that
            # cannot write to it.
            if connection.execute("select rolsuper from pg_roles where rolname = current_user").fetchone()[0]:
                raise SuperuserRefused("the warehouse must be read as its SELECT-only role, not as a superuser")

            table_rows = connection.execute(_TABLES_AND_COLUMNS, (self._schema,)).fetchall()
            key_rows = connection.execute(_KEYS, (self._schema,)).fetchall()

        tables: dict[str, Table] = {}
        columns: list[Column] = []
        for table, table_comment, column, data_type, nullable, column_comment in table_rows:
            tables.setdefault(table, Table(name=table, comment=table_comment))
            if column is not None:  # a table with no columns still exists
                columns.append(
                    Column(table=table, name=column, data_type=data_type, nullable=nullable, comment=column_comment)
                )

        primary_keys: list[PrimaryKey] = []
        foreign_keys: list[ForeignKey] = []
        for name, kind, table, key_columns, referenced_table, referenced_columns in key_rows:
            if kind == "p":
                primary_keys.append(PrimaryKey(table=table, columns=tuple(key_columns)))
            else:
                foreign_keys.append(
                    ForeignKey(
                        from_table=table,
                        from_columns=tuple(key_columns),
                        to_table=referenced_table,
                        to_columns=tuple(referenced_columns),
                        source="catalog",
                        name=name,
                    )
                )

        return SchemaSnapshot(
            tables=tuple(tables.values()),
            columns=tuple(columns),
            primary_keys=tuple(primary_keys),
            foreign_keys=tuple(foreign_keys),
        )
