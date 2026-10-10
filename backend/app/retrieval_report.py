"""How one located question is printed (FR-09, FR-41, DD-11).

Shared by the two commands that show retrieval: `python -m app.show_eval
--step6`, for every question of the evaluation set, and `python -m
app.show_retrieval "..."`, for one question of one's own. It lives here so
that the two cannot print the same thing differently.

Pure printing: a Located in, lines out. No database, no key, no decision.
"""

import textwrap

from app.core.join_tree import NO_ANCHORS
from app.core.locate import Located


def line(label: str, text: str) -> None:
    lines = textwrap.wrap(text, width=96, subsequent_indent="  ") or [""]
    print(f"    {label:<11}{lines[0]}")
    for extra in lines[1:]:
        print(f"    {'':<11}{extra}")


def joins_of(edges) -> list[str]:
    return sorted(" and ".join(f"{start} = {end}" for start, end in sorted(join)) for join in edges)


def print_retrieval(located: Located) -> None:
    """What was retrieved, which tables became anchors and which did not,
    and the tree: how each anchor was attached, and by which rule."""
    retrieval = located.retrieval
    best = max(retrieval.candidates, key=lambda candidate: candidate.semantic_raw)
    line("best raw", f"{retrieval.best_raw:.4f} ({best.element})  (information only)")
    line("terms", "; ".join(f"{term.term.text} -> {term.chosen_table}" for term in retrieval.terms) or "none")
    line("tables", ", ".join(
        f"{entry.table} {entry.score:.3f} (by {entry.best.split('.')[-1]})" for entry in retrieval.tables[:6]
    ))  # fmt: skip
    line("columns", ", ".join(
        f"{c.element} {c.combined:.3f} [sem {c.semantic_raw:.3f}, key {c.keyword_raw:.3f}]"
        for c in [candidate for candidate in retrieval.candidates if candidate.column][:4]
    ))  # fmt: skip

    bound = retrieval.anchor_bound
    line("anchors", f"{len(retrieval.anchors)}: "
                    + (", ".join(f"{a} {retrieval.score_of(a):.3f}" for a in retrieval.anchors) or "none"))  # fmt: skip

    # Every pair that set a table aside. The winner is always an anchor.
    aside = "; ".join(
        f"{rival.rival} (\"{rival.term}\" chose {rival.chosen}: {rival.chosen_score:.3f} against "
        f"{rival.rival_score:.3f})"
        for rival in retrieval.rivals
        if rival.rival in bound.set_aside_as_rivals and rival.chosen in retrieval.anchors
    )
    line("set aside", aside or "none")

    # The tables that reached the cut, that no term nominates, and that
    # were kept only because they are joined to the table a term chose.
    above_cut = {*retrieval.anchors, *bound.excluded_by_cap, *bound.set_aside_as_rivals}
    nominated = {term.chosen_table for term in retrieval.terms}
    kept = "; ".join(
        f"{partner.rival} (joined to {partner.chosen}, which \"{partner.term}\" chose: "
        f"{partner.chosen_score:.3f} against {partner.rival_score:.3f})"
        for term in retrieval.terms
        for partner in term.partners
        if partner.chosen in above_cut
        and partner.rival in above_cut
        and partner.rival not in nominated
        and partner.rival not in bound.set_aside_as_rivals
    )
    line("partners", kept or "none")
    line("cap cut", ", ".join(bound.excluded_by_cap) or "none")

    tree = located.tree
    if located.declined:
        line("DECLINED", located.explanation.reason)
    elif tree.decline_reason == NO_ANCHORS:
        line("tree", "none: no table reached the anchor cut")
    else:
        for attachment, explained in zip(tree.attachments, located.explanation.attachments):
            if attachment.path is None:
                line("attached", f"0  {attachment.anchor}: the seed")
                continue
            tied = f", {len(attachment.tied)} tied" if attachment.tied else ""
            if attachment.withdrawn:
                declared = "; ".join(f"{p.attach_to} rather than {p.rather_than}" for p in attachment.attach_preferences)
                tied = f", {len(attachment.tied) + len(attachment.withdrawn)} tied, " \
                       f"{len(attachment.withdrawn)} withdrawn by a declared preference ({declared})"  # fmt: skip
            evidence = ""
            if attachment.evidence:
                (_, top), (_, second) = attachment.evidence[:2]
                evidence = f"; evidence {top:.3f} against {second:.3f}, margin {attachment.margin:.3f}"
            line("attached", f"{attachment.order}  {attachment.anchor} to {attachment.attached_to} "
                             f"by {explained.path.id}  [{attachment.rule}{tied}{evidence}]")  # fmt: skip
        line("tree", ", ".join(tree.tables))
        if tree.subgraph_bound.dropped_anchors or tree.subgraph_bound.excluded:
            line("bound", f"dropped anchors: {', '.join(tree.subgraph_bound.dropped_anchors) or 'none'}; "
                          f"alternatives left out: {', '.join(tree.subgraph_bound.excluded) or 'none'}")  # fmt: skip


def print_warnings(located: Located, beside: str = "") -> None:
    """The warning codes raised and each warning's sentence; then the close
    calls, which are information and not warnings (ruling c as revised).
    `beside` is whatever the caller wants said after the codes."""
    line("warnings", f"{', '.join(sorted(located.warning_codes)) or 'none'}{beside}")
    if located.declined:
        return
    for warning in located.explanation.warnings:
        line("", f"{warning.code}: {warning.text}")
    for position, call in enumerate(located.explanation.close_calls):
        line("close call" if position == 0 else "", call.text)
