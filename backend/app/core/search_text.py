"""The text each schema element is embedded and searched by (FR-06, DD-08;
ruling f of step 6).

Pure (DD-01): a snapshot in, one string per element out. Derived from the
schema and the naming overlay alone. Nothing here is written by hand, and
nothing may be: a description written now would be written by someone who
has read the evaluation questions.

  a column   its description, as DD-08 gives it:
             "store sales — extended sales price. Numeric column in table
             store_sales."

  a table    its description, then what it holds: the readable names of its
             columns that are not keys, without the table's own name in
             front of each.
             "store. Table store. Holds: store identifier, store name,
             number employees, ..."

Key columns are left out of a table's text. A table's surrogate keys say
what it joins to, not what it is about, and a sales table would otherwise
read as a list of every dimension in the warehouse. A key column is one in
a primary key, or on either side of a foreign key. They keep their own
rows and are searched there.
"""

from app.core.snapshot import SchemaSnapshot


def key_columns(snapshot: SchemaSnapshot) -> frozenset[tuple[str, str]]:
    """(table, column) for every column of a primary key or of either side
    of a foreign key."""
    keys = {(key.table, column) for key in snapshot.primary_keys for column in key.columns}
    for key in snapshot.foreign_keys:
        keys.update((key.from_table, column) for column in key.from_columns)
        keys.update((key.to_table, column) for column in key.to_columns)
    return frozenset(keys)


def search_texts(snapshot: SchemaSnapshot) -> dict[tuple[str, str | None], str]:
    """(table, column) -> text, with column None for the table's own row."""
    keys = key_columns(snapshot)
    texts: dict[tuple[str, str | None], str] = {}
    for table in snapshot.tables:
        holds = [
            (column.readable or column.name).split(" — ", 1)[-1]
            for column in snapshot.columns
            if column.table == table.name and (column.table, column.name) not in keys
        ]
        description = table.description or f"{table.name}."
        texts[table.name, None] = f"{description} Holds: {', '.join(holds)}." if holds else description
    for column in snapshot.columns:
        texts[column.table, column.name] = column.description or f"{column.name}. Column in table {column.table}."
    return texts
