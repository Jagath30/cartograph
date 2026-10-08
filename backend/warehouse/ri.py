"""tpcds_ri.sql -> foreign keys -> the overlay's relationships section.

The foreign keys of the TPC-DS warehouse are never written by hand (Charter
D-10). They are read from the toolkit's published constraint file, and
everything else -- the overlay, the primary keys, the expected edge count --
is derived from that one reading.

The overlay also holds what a person wrote, which is the opposite kind of
thing: the `naming` section, from overlays/tpcds.naming.yaml, and the few
relationships the TPC's file omits, from overlays/tpcds.relationships.yaml.
The overlay file is generated from all three sources and is never edited
itself, so a rebuild cannot overwrite anything a person wrote.

The file itself is the TPC's and is not committed. scripts/warehouse.sh
fetches it beside this module from the URL below and refuses any content
whose sha256 is not the one pinned here. The overlay generated from it is
committed, so the edge list does not depend on the mirror staying up.

Pure and standard-library only (DD-01): text in, values out. The warehouse
build runs it on the host before any container is involved, and the tests
run it with no database.

The parser is strict on purpose. A line it cannot classify raises; it is
never skipped. A dropped statement would be a missing edge, and a missing
edge is silent everywhere downstream (T-01).
"""

import hashlib
import re
import sys
from dataclasses import dataclass
from pathlib import Path

RI_FILE = Path(__file__).with_name("tpcds_ri.sql")

# What is generated, and the hand-written half it is generated from.
OVERLAY_FILE = Path(__file__).parent.parent / "overlays" / "tpcds.yaml"
NAMING_FILE = OVERLAY_FILE.with_name("tpcds.naming.yaml")
RELATIONSHIPS_FILE = OVERLAY_FILE.with_name("tpcds.relationships.yaml")

# A public mirror of the TPC-DS toolkit, pinned to a commit rather than a
# branch, and to a hash rather than to trust. If either changes, that is a
# decision, and it is made here and in the test's expected edge count -- not
# by accident.
RI_URL = (
    "https://raw.githubusercontent.com/gregrahn/tpcds-kit/"
    "5a3a81796992b725c2a8b216767e142609966752/tools/tpcds_ri.sql"
)
RI_SHA256 = "c5fc873d832c6bf5362cb8bd89d6131c65cdb50712301e9f08ccf92213f92d5b"

# One statement, whole line, single-column key on both sides. Anchored at
# both ends: a composite key or a trailing clause fails to match and raises
# rather than being half-read.
_STATEMENT = re.compile(
    r"""alter \s+ table \s+ (?P<from_table>\w+) \s+
        add \s+ constraint \s+ (?P<name>\w+) \s+
        foreign \s+ key \s* \( \s* (?P<from_column>\w+) \s* \) \s*
        references \s+ (?P<to_table>\w+) \s* \( \s* (?P<to_column>\w+) \s* \) \s* ;""",
    re.IGNORECASE | re.VERBOSE,
)


@dataclass(frozen=True)
class ForeignKey:
    name: str
    from_table: str
    from_column: str
    to_table: str
    to_column: str


def parse(text: str) -> list[ForeignKey]:
    """Every active statement in the file, in file order.

    Blank lines and `--` comments are the only things passed over. That
    includes the statements the TPC authors themselves commented out: they
    name columns that do not exist in the schema, and are not relationships.
    """
    keys: list[ForeignKey] = []
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("--"):
            continue
        match = _STATEMENT.fullmatch(line)
        if match is None:
            raise ValueError(f"tpcds_ri.sql line {number}: not a foreign key statement: {line!r}")
        keys.append(ForeignKey(**{k: v.lower() for k, v in match.groupdict().items()}))

    _reject_duplicates(keys)
    return keys


def _reject_duplicates(keys: list[ForeignKey]) -> None:
    names = [key.name for key in keys]
    columns = [(key.from_table, key.from_column) for key in keys]
    for label, values in (("constraint name", names), ("referencing column", columns)):
        repeated = sorted({v for v in values if values.count(v) > 1})
        if repeated:
            raise ValueError(f"tpcds_ri.sql: repeated {label}: {repeated}")


def primary_keys(keys: list[ForeignKey]) -> dict[str, str]:
    """Table -> the column that must be unique for the foreign keys to apply.

    Derived, not listed: a foreign key needs a unique target, so the set of
    referenced columns *is* the set of primary keys the load has to add.
    """
    targets: dict[str, str] = {}
    for key in keys:
        known = targets.setdefault(key.to_table, key.to_column)
        if known != key.to_column:
            raise ValueError(f"{key.to_table} is referenced by two different columns: {known}, {key.to_column}")
    return dict(sorted(targets.items()))


def _section(source: str, name: str, file: Path) -> list[str]:
    """One section of a hand-written source, from its `name:` line to the
    end, exactly as written. The comment header above it stays behind: it
    says "edit here", which would be false in the generated file.

    Strict in the same way as the parser. The source may hold comments, then
    `name:`, then only lines indented beneath it. A second top-level section
    would be copied into the overlay unseen, so it raises.
    """
    lines = source.splitlines()
    if f"{name}:" not in lines:
        raise ValueError(f"{file.name}: no `{name}:` line at the left margin")
    start = lines.index(f"{name}:")
    for number, line in enumerate(lines, start=1):
        comment_or_blank = not line.strip() or line.lstrip().startswith("#")
        before = number - 1 < start
        indented = line.startswith(" ")
        if number - 1 != start and not comment_or_blank and (before or not indented):
            raise ValueError(f"{file.name} line {number}: only the {name} section belongs here: {line!r}")
    return lines[start:]


def naming_section(source: str) -> str:
    """The `naming:` block of its hand-written source, header line included."""
    return "\n".join(_section(source, "naming", NAMING_FILE)).rstrip() + "\n"


def hand_relationships(source: str) -> str:
    """The entries under `relationships:` in the hand-written source,
    without that line itself: they are appended to the generated list, which
    already has one."""
    entries = "\n".join(_section(source, "relationships", RELATIONSHIPS_FILE)[1:]).strip("\n")
    if "  - from:" not in entries:
        raise ValueError(f"{RELATIONSHIPS_FILE.name}: no relationships found under `relationships:`")
    return entries + "\n"


def render_overlay(keys: list[ForeignKey], relationships_source: str, naming_source: str) -> str:
    """The overlay file (DD-16): relationships generated from the keys, then
    the hand-declared relationships and the naming section, each copied from
    its own source.

    Written by hand rather than through a YAML library so the host needs
    nothing installed; every generated value is a bare identifier, so there
    is nothing to quote. The tests read it back with a real YAML parser.
    """
    by_hand = hand_relationships(relationships_source)
    lines = [
        "# Cartograph overlay for the TPC-DS warehouse (DD-16).",
        "#",
        "# GENERATED. Do not edit this file: anything typed here is overwritten.",
        "#",
        "#   relationships  from backend/warehouse/tpcds_ri.sql, whose sha256 is",
        f"#                  {RI_SHA256}",
        f"#                  and then from backend/overlays/{RELATIONSHIPS_FILE.name}",
        f"#                  HAND-DECLARED RELATIONSHIPS BELONG IN {RELATIONSHIPS_FILE.name}, not here.",
        f"#   naming         from backend/overlays/{NAMING_FILE.name}",
        f"#                  NAMING EDITS BELONG IN {NAMING_FILE.name}, not here.",
        "#",
        "# To regenerate after editing either source:",
        "#     python3 backend/warehouse/ri.py write-overlay",
        "# (./scripts/warehouse.sh also regenerates it, and rebuilds the warehouse.)",
        "#",
        f"# {len(keys)} relationships from tpcds_ri.sql, which the generated warehouse does not",
        f"# declare by itself (FR-43), then {by_hand.count('  - from:')} declared by hand that tpcds_ri.sql omits.",
        "# The preferences section is absent on purpose (DD-12).",
        "",
        "relationships:",
    ]
    for key in keys:
        lines.append(f"  - from: {key.from_table}.{key.from_column}")
        lines.append(f"    to:   {key.to_table}.{key.to_column}")
    lines.append("")
    lines.append(f"  # Declared by hand in {RELATIONSHIPS_FILE.name}. Not in tpcds_ri.sql, not in the catalog.")
    return "\n".join(lines) + "\n" + by_hand + "\n" + naming_section(naming_source)


def render_primary_keys(keys: list[ForeignKey]) -> str:
    return "".join(
        f"alter table {table} add primary key ({column});\n" for table, column in primary_keys(keys).items()
    )


def load() -> list[ForeignKey]:
    raw = RI_FILE.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != RI_SHA256:
        raise ValueError(f"{RI_FILE.name} has changed: sha256 {digest}, expected {RI_SHA256}")
    return parse(raw.decode("ascii"))


def render_current_overlay() -> str:
    return render_overlay(
        load(),
        RELATIONSHIPS_FILE.read_text(encoding="utf-8"),
        NAMING_FILE.read_text(encoding="utf-8"),
    )


if __name__ == "__main__":
    # `source` prints where the file comes from and what it must hash to, and
    # is the one command that works before the file exists.
    #
    # `write-overlay` renders everything first and writes last. Prefer it to
    # `overlay > tpcds.yaml`: a shell empties the target before the command
    # runs, so a failure there would leave an empty overlay behind.
    renderers = {"overlay": render_current_overlay, "primary-keys": lambda: render_primary_keys(load())}
    command = sys.argv[1] if len(sys.argv) == 2 else None
    if command == "source":
        print(RI_URL, RI_SHA256)
    elif command == "write-overlay":
        rendered = render_current_overlay()
        OVERLAY_FILE.write_text(rendered, encoding="utf-8")
        print(f"wrote {OVERLAY_FILE}")
    elif command in renderers:
        sys.stdout.write(renderers[command]())
    else:
        sys.exit(f"usage: ri.py {{source|write-overlay|{'|'.join(renderers)}}}")
