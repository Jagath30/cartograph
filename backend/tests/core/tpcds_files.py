"""The committed TPC-DS files, for tests that need the real schema and no
database: the DDL the warehouse is created from, and the generated overlay.
"""

import re
from pathlib import Path

from app.core.snapshot import Column, SchemaSnapshot, Table

BACKEND = Path(__file__).resolve().parents[2]
OVERLAY = BACKEND / "overlays" / "tpcds.yaml"
NAMING_SOURCE = BACKEND / "overlays" / "tpcds.naming.yaml"
RELATIONSHIPS_SOURCE = BACKEND / "overlays" / "tpcds.relationships.yaml"
SCHEMA = BACKEND / "warehouse" / "tpcds_schema.sql"


def ddl_snapshot() -> SchemaSnapshot:
    """The 24 tables and their columns, read from the DDL -- with no keys of
    any kind, which is the state DuckDB's output is in (R-09)."""
    tables: list[Table] = []
    columns: list[Column] = []
    for line in SCHEMA.read_text().splitlines():
        if opening := re.fullmatch(r"create table (\w+) \(", line):
            tables.append(Table(opening.group(1)))
        elif column := re.fullmatch(r"    (\w+)\s+(\S.*?),?", line):
            columns.append(Column(tables[-1].name, column.group(1), column.group(2)))
    return SchemaSnapshot(tuple(tables), tuple(columns), (), ())
