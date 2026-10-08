"""SnapshotStore: a schema snapshot, kept in the application store and read
back (FR-04, FR-05, DR-10, DD-17).

Impure, and so in shell/ (DD-01): it holds the application database's
connection string and nothing else. It never sees the warehouse (DD-02,
rule 2). What to store is decided in the core; this writes rows and reads
them.

ADDED, NEVER REPLACED (DD-17). Saving a snapshot the store already holds
changes nothing but which one is current. Saving a different one adds it
and marks it current; the old rows stay, so a trace's schema_ref never
points at something that no longer exists.

THE HASH is the sha256 of everything stored: tables, columns, keys, every
readable name and every search text. Two ingestions of an unchanged
warehouse with an unchanged overlay give the same hash, which is how the
embeddings come to be computed once per snapshot and not once per run.

PREFERENCES ARE NOT STORED (ruling e). The three tables hold what the
schema is. A declared preference is read from the overlay file whenever a
graph is built from stored rows, and is handed to `load_current`.

READING BACK gives a SchemaSnapshot equal to the one saved, element for
element, so the graph built from rows is the graph built from the
warehouse. That is tested against the live warehouse, not assumed.
"""

import hashlib
import json
from dataclasses import asdict, dataclass

import psycopg

from app.core.search_text import search_texts
from app.core.snapshot import Column, ForeignKey, Preference, PrimaryKey, SchemaSnapshot, Table


class NoCurrentSnapshot(RuntimeError):
    """Nothing has been ingested into the application store yet."""


@dataclass(frozen=True)
class StoredSnapshot:
    id: int
    hash: str
    source: str
    embedding_model: str | None
    # False when `save` found the snapshot already stored.
    created: bool = False


def snapshot_hash(snapshot: SchemaSnapshot) -> str:
    """Everything that is stored, in one fixed order. Preferences are left
    out: they are not stored."""
    texts = search_texts(snapshot)
    content = {
        "tables": [asdict(table) | {"search_text": texts[table.name, None]} for table in snapshot.tables],
        "columns": [asdict(column) | {"search_text": texts[column.table, column.name]} for column in snapshot.columns],
        "primary_keys": [asdict(key) for key in snapshot.primary_keys],
        "foreign_keys": [asdict(key) for key in snapshot.foreign_keys],
    }
    return hashlib.sha256(json.dumps(content, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


class SnapshotStore:
    def __init__(self, dsn: str) -> None:
        self._dsn = dsn

    def save(self, snapshot: SchemaSnapshot, source: str) -> StoredSnapshot:
        """Store the snapshot if it is new, and mark it current either way."""
        digest = snapshot_hash(snapshot)
        with psycopg.connect(self._dsn) as connection:
            known = connection.execute(
                "select id, source, embedding_model from schema_snapshots where hash = %s", (digest,)
            ).fetchone()
            connection.execute("update schema_snapshots set is_current = false where is_current")
            if known is not None:
                connection.execute("update schema_snapshots set is_current = true where id = %s", (known[0],))
                return StoredSnapshot(known[0], digest, known[1], known[2], created=False)

            (snapshot_id,) = connection.execute(
                "insert into schema_snapshots (hash, source, is_current) values (%s, %s, true) returning id",
                (digest, source),
            ).fetchone()
            self._insert(connection, snapshot_id, snapshot)
            return StoredSnapshot(snapshot_id, digest, source, None, created=True)

    def current(self) -> StoredSnapshot:
        with psycopg.connect(self._dsn) as connection:
            return self._current(connection)

    def load_current(self, preferences: tuple[Preference, ...] = ()) -> tuple[StoredSnapshot, SchemaSnapshot]:
        """The current snapshot, rebuilt from its rows."""
        with psycopg.connect(self._dsn) as connection:
            stored = self._current(connection)
            elements = connection.execute(
                """
                select table_name, column_name, data_type, nullable, comment,
                       primary_key_position, readable, description
                from schema_elements where snapshot_id = %s order by position
                """,
                (stored.id,),
            ).fetchall()
            edges = connection.execute(
                """
                select e.key_number, f.table_name, f.column_name, t.table_name, t.column_name,
                       e.source, e.constraint_name, e.note
                from schema_edges e
                join schema_elements f on f.id = e.from_element_id
                join schema_elements t on t.id = e.to_element_id
                where e.snapshot_id = %s order by e.key_number, e.position
                """,
                (stored.id,),
            ).fetchall()

        tables, columns = [], []
        primary: dict[str, list[tuple[int, str]]] = {}
        for table, column, data_type, nullable, comment, key_position, readable, description in elements:
            if column is None:
                tables.append(Table(table, comment, readable, description))
                continue
            columns.append(Column(table, column, data_type, nullable, comment, readable, description))
            if key_position is not None:
                primary.setdefault(table, []).append((key_position, column))

        keys: dict[int, dict] = {}
        for number, from_table, from_column, to_table, to_column, source, name, note in edges:
            key = keys.setdefault(
                number,
                {"from_table": from_table, "from": [], "to_table": to_table, "to": [], "source": source, "name": name, "note": note},
            )  # fmt: skip
            key["from"].append(from_column)
            key["to"].append(to_column)

        snapshot = SchemaSnapshot(
            tables=tuple(tables),
            columns=tuple(columns),
            primary_keys=tuple(
                PrimaryKey(table, tuple(column for _, column in sorted(members))) for table, members in primary.items()
            ),
            foreign_keys=tuple(
                ForeignKey(k["from_table"], tuple(k["from"]), k["to_table"], tuple(k["to"]), k["source"], k["name"], k["note"])
                for _, k in sorted(keys.items())
            ),
            preferences=preferences,
        )  # fmt: skip
        return stored, snapshot

    def _current(self, connection) -> StoredSnapshot:
        row = connection.execute(
            "select id, hash, source, embedding_model from schema_snapshots where is_current"
        ).fetchone()
        if row is None:
            raise NoCurrentSnapshot("the application store holds no schema snapshot: nothing has been ingested")
        return StoredSnapshot(*row)

    def _insert(self, connection, snapshot_id: int, snapshot: SchemaSnapshot) -> None:
        texts = search_texts(snapshot)
        key_position = {
            (key.table, column): position
            for key in snapshot.primary_keys
            for position, column in enumerate(key.columns)
        }

        # Each table's own row, then its columns, in the snapshot's order.
        rows = []
        for table in snapshot.tables:
            rows.append((table.name, None, None, None, table.comment, None, table.readable, table.description))
            rows += [
                (c.table, c.name, c.data_type, c.nullable, c.comment, key_position.get((c.table, c.name)), c.readable, c.description)
                for c in snapshot.columns
                if c.table == table.name
            ]  # fmt: skip
        if len(rows) != len(snapshot.tables) + len(snapshot.columns):
            raise ValueError("a column of the snapshot belongs to no table of it")

        ids: dict[tuple[str, str | None], int] = {}
        with connection.cursor() as cursor:
            for position, row in enumerate(rows):
                cursor.execute(
                    """
                    insert into schema_elements
                        (snapshot_id, table_name, column_name, data_type, nullable, comment,
                         primary_key_position, readable, description, position, search_text)
                    values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) returning id
                    """,
                    (snapshot_id, *row, position, texts[row[0], row[1]]),
                )
                ids[row[0], row[1]] = cursor.fetchone()[0]

            cursor.executemany(
                """
                insert into schema_edges
                    (snapshot_id, key_number, position, from_element_id, to_element_id, source, constraint_name, note)
                values (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                [
                    (snapshot_id, number, position, ids[key.from_table, start], ids[key.to_table, end],
                     key.source, key.name, key.note)
                    for number, key in enumerate(snapshot.foreign_keys)
                    for position, (start, end) in enumerate(zip(key.from_columns, key.to_columns))
                ],
            )  # fmt: skip
