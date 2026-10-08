"""Locate: from a scored question to one join tree, explained (FR-08 to
FR-14, FR-41, FR-42, DD-10, DD-11).

Pure (DD-01). This is the Retriever, the JoinTree and the Explainer run in
order on plain data, and nothing else: it holds no rule of its own. It is
here so that the order is written once and tested, and so that whatever
calls it -- a command today, the orchestrator at step 7 -- cannot run the
three slightly differently.

  1. retrieve     scores -> anchors, rivals, or a decline (nothing above
                  the floor)
  2. build_tree   anchors -> one tree, or a decline (anchors that cannot
                  be connected)
  3. explain_tree the tree -> sentences and warnings

A QUESTION IS DECLINED (FR-42) for exactly two reasons, ruling d: no
element scores above the floor, or the anchors cannot be connected within
the hop limit. A question for which no table reaches the anchor cut is not
declined: it has no tree, and says so.
"""

from dataclasses import dataclass

import networkx as nx

from app.core.explainer import TreeExplanation, explain_tree
from app.core.join_tree import (
    ANCHORS_NOT_CONNECTED,
    DEFAULT_SUBGRAPH_BOUND,
    JoinTree,
    SetAside,
    build_tree,
)
from app.core.path_finder import DEFAULT_MAX_JOINS
from app.core.retriever import RawScore, Retrieval, Settings, Term, retrieve


@dataclass(frozen=True)
class Located:
    retrieval: Retrieval
    # None when retrieval declined before any tree could be built.
    tree: JoinTree | None
    explanation: TreeExplanation | None
    declined: bool
    decline_reason: str | None

    @property
    def tables(self) -> tuple[str, ...]:
        return self.tree.tables if self.tree and not self.declined else ()

    @property
    def warning_codes(self) -> frozenset[str]:
        return self.explanation.codes if self.explanation and not self.declined else frozenset()


def set_aside(retrieval: Retrieval) -> tuple[SetAside, ...]:
    """The rivals the Retriever actually set aside, once each: a table that
    reached the cut and lost its place to the table a term chose."""
    aside: dict[tuple[str, str], SetAside] = {}
    for rival in retrieval.rivals:
        if rival.rival in retrieval.anchor_bound.set_aside_as_rivals and rival.chosen in retrieval.anchors:
            aside.setdefault(
                (rival.chosen, rival.rival),
                SetAside(rival.term, rival.chosen, rival.rival, rival.chosen_score, rival.rival_score),
            )
    return tuple(aside.values())


def locate(
    question: str,
    scores: tuple[RawScore, ...],
    terms: tuple[tuple[Term, tuple[RawScore, ...]], ...],
    graph: nx.DiGraph,
    settings: Settings,
    max_joins: int = DEFAULT_MAX_JOINS,
    subgraph_bound: int = DEFAULT_SUBGRAPH_BOUND,
) -> Located:
    retrieval = retrieve(question, scores, terms, settings)
    if retrieval.declined:
        return Located(retrieval, None, None, True, retrieval.decline_reason)

    tree = build_tree(
        graph,
        retrieval.anchors,
        {anchor: retrieval.score_of(anchor) for anchor in retrieval.anchors},
        evidence=retrieval.column_scores,
        margin=settings.margin,
        set_aside=set_aside(retrieval),
        max_joins=max_joins,
        subgraph_bound=subgraph_bound,
    )
    declined = tree.decline_reason == ANCHORS_NOT_CONNECTED
    return Located(retrieval, tree, explain_tree(tree, graph), declined, tree.decline_reason if declined else None)
