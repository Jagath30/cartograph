"""Readable names for compressed identifiers (DD-08, threat T-03).

`ss_ext_sales_price` means nothing to a reader and little to an embedding
model. This module turns it into

    readable     "store sales — extended sales price"
    description  "store sales — extended sales price. Numeric column in table store_sales."

from the overlay's `naming` section: `prefixes` says what a column prefix
stands for, `words` says what an abbreviated fragment stands for.

Three sources, in priority order:
  1. the database comment, where one exists -- a written description always
     beats a derived one;
  2. otherwise the prefix and word expansions;
  3. the table name and the data type, appended to the description as context.

A fragment with no entry is kept as it is. An incomplete mapping therefore
degrades towards the raw name instead of breaking, and an empty one yields
the identifier with its underscores turned to spaces.

Pure (DD-01): strings and dictionaries in, strings out.
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Naming:
    # "ss_" -> "store sales". Matched against the start of a column name.
    prefixes: dict[str, str] = field(default_factory=dict)
    # "ext" -> "extended". Matched against one underscore-separated fragment.
    words: dict[str, str] = field(default_factory=dict)


# What a Postgres type is, in one word a non-programmer would use. Keyed by
# the type name with any "(7,2)" removed.
_FAMILIES = {
    "smallint": "Integer",
    "integer": "Integer",
    "bigint": "Integer",
    "numeric": "Numeric",
    "decimal": "Numeric",
    "real": "Numeric",
    "double precision": "Numeric",
    "character varying": "Text",
    "varchar": "Text",
    "character": "Text",
    "text": "Text",
    "date": "Date",
    "boolean": "Boolean",
}


def _expand(fragments: str, naming: Naming) -> str:
    return " ".join(naming.words.get(word, word) for word in fragments.split("_") if word)


def _sentence(text: str) -> str:
    return text.strip().rstrip(".").strip()


def readable_table(name: str, comment: str | None, naming: Naming) -> str:
    """A table has no prefix of its own, so only its words are expanded:
    date_dim -> "date dimension"."""
    if comment and comment.strip():
        return _sentence(comment)
    return _expand(name, naming)


def readable_column(name: str, comment: str | None, naming: Naming) -> str:
    if comment and comment.strip():
        return _sentence(comment)

    # Longest first, so that "web_" is tried before "w_" could ever matter.
    for prefix in sorted(naming.prefixes, key=len, reverse=True):
        if name.startswith(prefix) and len(name) > len(prefix):
            return f"{naming.prefixes[prefix]} — {_expand(name[len(prefix):], naming)}"
    return _expand(name, naming)


def type_family(data_type: str) -> str | None:
    """"numeric(7,2)" -> "Numeric". None for a type not listed above."""
    base = data_type.split("(")[0].strip().lower()
    if base.startswith("timestamp"):
        return "Timestamp"
    if base.startswith("time"):
        return "Time"
    return _FAMILIES.get(base)


def describe_table(name: str, readable: str) -> str:
    return f"{readable}. Table {name}."


def describe_column(table: str, data_type: str, readable: str) -> str:
    family = type_family(data_type)
    kind = f"{family} column" if family else f"Column of type {data_type}"
    return f"{readable}. {kind} in table {table}."
