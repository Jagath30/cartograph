"""Step 10's full-schema mode: the prompt with every table and every key,
and no selection (FR-38, DD-20 as clarified by the owner's ruling 11).

Pure (DD-01). The frozen PromptBuilder is imported, never edited; this
module composes a second user message from its parts. That is allowed by
the owner's ruling on ruling 11 (16 October 2026), which is the record of
it.

THE PRINCIPLE. The two modes' prompts differ only in what Cartograph
contributes. Both have the same system message (`GENERAL_RULES`), the
same question text, every table rendered by the frozen `render_tables`.
Retrieval mode adds the selection: which tables, which joins, and the
instruction to use them. Full-schema mode gets every table and every key
the graph holds, catalog and overlay alike, and no selection: no tree, no
tie-break, no declared preference.

THE KEYS SECTION stands where retrieval mode has its join section. Its
first lines are the owner's wording, word for word (`KEYS_RULES`). Its key
lines are the frozen builder's own: `render_joins` is called on every key
and its `JOIN_RULES` heading is taken off, so each line is exactly the
line retrieval mode would render for that key, sorted as it sorts them.

The stand-in for retrieval (`FullSchemaLocated`) gives the orchestrator
what it reads from a located question -- every table, no joins, not
declined -- so that both modes go through one orchestrator, the same
model, the same retries, validator, checker and executor.
"""

from dataclasses import dataclass

import networkx as nx

from app.core.graph_builder import foreign_key_edges
from app.core.path_finder import MANY_TO_ONE, Join
from app.core.prompt_builder import GENERAL_RULES, JOIN_RULES, Prompt, build_prompt, render_joins, render_tables
from app.core.snapshot import SchemaSnapshot

KEYS_RULES = """\
Keys between tables. These are the keys by which the tables above can be joined. A line with two conditions joined by AND is one key and needs both. Two tables may be joined by more than one key. Use only the tables the question needs: a table listed above need not appear in the query."""


def graph_keys(graph: nx.DiGraph) -> tuple[Join, ...]:
    """Every foreign key of the graph, once, as a Join. A key of several
    columns is several column edges with one `foreign_key` number, gathered
    back into one, in the order the graph holds them."""
    gathered: dict[int, dict] = {}
    for start, end, data in foreign_key_edges(graph):
        facts = gathered.setdefault(
            data["foreign_key"],
            {"fk_table": graph.nodes[start]["table"], "fk_columns": (), "pk_table": graph.nodes[end]["table"],
             "pk_columns": (), "source": data["source"], "constraint": data["constraint"], "note": data["note"]},
        )  # fmt: skip
        facts["fk_columns"] += (graph.nodes[start]["name"],)
        facts["pk_columns"] += (graph.nodes[end]["name"],)
    return tuple(Join(**facts, walked=MANY_TO_ONE) for _, facts in sorted(gathered.items()))


def render_keys(keys: tuple[Join, ...]) -> str:
    """The keys section: the owner's wording, then the frozen builder's
    lines for every key."""
    rendered = render_joins(keys)
    heading = JOIN_RULES + "\n"
    if not rendered.startswith(heading):
        raise ValueError("the frozen join section no longer begins with JOIN_RULES; the keys cannot be rendered")
    return KEYS_RULES + "\n" + rendered[len(heading) :]


def all_tables(snapshot: SchemaSnapshot) -> tuple[str, ...]:
    """Every table, in alphabetical order."""
    return tuple(sorted(table.name for table in snapshot.tables))


def build_full_prompt(question: str, snapshot: SchemaSnapshot, graph: nx.DiGraph) -> Prompt:
    """The full-schema prompt. Built from the frozen builder's own prompt
    for every table and no joins, with the keys section put where the join
    section would stand, between the tables and the question."""
    tables = all_tables(snapshot)
    plain = build_prompt(question, snapshot, tables, None)
    tables_part = "Tables:\n\n" + render_tables(snapshot, tables) + "\n\n"
    if not plain.user.startswith(tables_part):
        raise ValueError("the frozen prompt no longer begins with its tables; the keys cannot be placed")
    user = tables_part + render_keys(graph_keys(graph)) + "\n\n" + plain.user[len(tables_part) :]
    # No selected path: has_join_section is False, as it is for any prompt
    # without one. The keys are not a selection.
    return Prompt(GENERAL_RULES, user, tables, False)


@dataclass(frozen=True)
class FullSchemaTree:
    """What the orchestrator reads of a tree, for the whole schema."""

    tables: tuple[str, ...]
    subgraph: tuple[str, ...]
    joins: tuple[Join, ...] = ()
    decline_reason: str | None = None


@dataclass(frozen=True)
class FullSchemaLocated:
    """Stands where retrieval's located question would: every table, no
    joins, never declined. Nothing was retrieved."""

    tree: FullSchemaTree
    declined: bool = False
    decline_reason: str | None = None


def full_schema_located(snapshot: SchemaSnapshot) -> FullSchemaLocated:
    tables = all_tables(snapshot)
    return FullSchemaLocated(FullSchemaTree(tables, tables))
