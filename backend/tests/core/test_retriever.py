"""The Retriever, from scores written by hand (FR-08 to FR-10, FR-41, FR-42,
DD-09, DD-11).

No database, no embedding, no key: every number below was typed, so every
assertion can be checked by reading this file. Nothing here is a question
of the evaluation set.

The miniature has three tables. "sales" and "returns" each hold an amount;
"shop" holds a region.
"""

import math

import pytest

from app.core.retriever import (
    RawScore,
    Settings,
    Term,
    Token,
    make_terms,
    normalise,
    rank,
    retrieve,
    table_scores,
)

DEFAULTS = Settings(alpha=0.5, anchor_cut=0.5, anchor_cap=5, margin=0.1)


def _scores(*rows: tuple[str, float, float]) -> tuple[RawScore, ...]:
    """("sales.amount", semantic, keyword) or ("sales", semantic, keyword)."""
    scored = []
    for name, semantic, keyword in rows:
        table, _, column = name.partition(".")
        scored.append(RawScore(table, column or None, semantic, keyword))
    return tuple(scored)


QUESTION = _scores(
    ("sales", 0.50, 0.0),
    ("sales.amount", 0.60, 0.8),
    ("sales.shop_key", 0.20, 0.0),
    ("returns", 0.30, 0.0),
    ("returns.amount", 0.40, 0.8),
    ("shop", 0.35, 0.0),
    ("shop.region", 0.55, 0.0),
)


def _settings(**changes) -> Settings:
    values = {"alpha": 0.5, "anchor_cut": 0.5, "anchor_cap": 5, "margin": 0.1}
    return Settings(**{**values, **changes})


# --------------------------------------------------------------------------
# Normalisation: the silent failure DD-09 names
# --------------------------------------------------------------------------


def test_scores_are_stretched_to_run_from_0_to_1() -> None:
    assert normalise([2.0, 4.0, 3.0]) == [0.0, 1.0, 0.5]


def test_a_signal_that_is_the_same_everywhere_becomes_0_everywhere() -> None:
    """No keyword matched anything. Not 1 for all, which would let a signal
    that said nothing outvote one that did; not a division by zero."""
    for flat in ([0.0, 0.0, 0.0], [0.7, 0.7, 0.7], [0.3]):
        normalised = normalise(flat)
        assert normalised == [0.0] * len(flat)
        assert not any(math.isnan(value) for value in normalised)
    assert normalise([]) == []


def test_with_no_keyword_match_the_ranking_is_the_semantic_ranking_and_nothing_is_nan() -> None:
    scores = _scores(("sales.amount", 0.6, 0.0), ("shop.region", 0.4, 0.0), ("returns.amount", 0.5, 0.0))
    ranked = rank(scores, alpha=0.5)

    assert [candidate.element for candidate in ranked] == ["sales.amount", "returns.amount", "shop.region"]
    assert [candidate.keyword for candidate in ranked] == [0.0, 0.0, 0.0]
    # The best semantic match scores alpha: the silent keyword half adds 0.
    # Had it normalised to 1, every combined score would sit 0.5 higher and
    # everything would clear the anchor cut.
    assert [candidate.combined for candidate in ranked] == [0.5, 0.25, 0.0]


def test_with_no_keyword_match_a_poor_semantic_match_does_not_become_an_anchor() -> None:
    scores = _scores(("sales", 0.6, 0.0), ("shop", 0.4, 0.0), ("returns", 0.5, 0.0))
    assert retrieve("q", scores, (), DEFAULTS).anchors == ("sales",)


def test_alpha_changes_the_ranking() -> None:
    """If moving alpha moved nothing, one signal would be drowning the
    other and the weighting would be decoration (DD-09)."""
    scores = _scores(("sales.amount", 0.9, 0.0), ("shop.region", 0.1, 5.0), ("returns.amount", 0.5, 2.0))

    def order(alpha: float) -> list[str]:
        return [candidate.element for candidate in rank(scores, alpha)]

    assert order(1.0) == ["sales.amount", "returns.amount", "shop.region"]
    assert order(0.0) == ["shop.region", "returns.amount", "sales.amount"]
    assert order(1.0) != order(0.5) != order(0.0)


def test_a_keyword_rank_far_outside_0_to_1_does_not_dominate() -> None:
    """Text rank has no fixed range. Left raw, 40.0 against 0.9 would decide
    everything; normalised, the two halves weigh the same."""
    scores = _scores(("sales.amount", 0.9, 0.0), ("shop.region", 0.1, 40.0))
    ranked = rank(scores, alpha=0.5)
    assert [candidate.combined for candidate in ranked] == [0.5, 0.5]


def test_both_kinds_of_score_are_kept_on_every_candidate() -> None:
    ranked = rank(QUESTION, alpha=0.5)
    best = ranked[0]
    assert (best.element, best.kind, best.rank) == ("sales.amount", "column", 1)
    assert (best.semantic_raw, best.keyword_raw) == (0.60, 0.8)
    assert (best.semantic, best.keyword, best.combined) == (1.0, 1.0, 1.0)
    assert len(ranked) == len(QUESTION)
    assert [candidate.rank for candidate in ranked] == list(range(1, len(QUESTION) + 1))
    assert {candidate.kind for candidate in ranked} == {"table", "column"}


def test_equal_scores_are_ordered_by_name() -> None:
    scores = _scores(("shop.region", 0.5, 0.0), ("returns.amount", 0.5, 0.0), ("sales.amount", 0.9, 0.0))
    assert [candidate.element for candidate in rank(scores, 0.5)] == ["sales.amount", "returns.amount", "shop.region"]


def test_an_element_scored_twice_raises() -> None:
    with pytest.raises(ValueError, match="scored twice"):
        rank(_scores(("sales.amount", 0.5, 0.0), ("sales.amount", 0.6, 0.0)), 0.5)


# --------------------------------------------------------------------------
# A table's score (ruling f)
# --------------------------------------------------------------------------


def test_a_table_scores_the_better_of_its_own_row_and_its_best_column() -> None:
    tables = {entry.table: entry for entry in table_scores(rank(QUESTION, alpha=1.0))}

    # sales: its column (0.60) beats its own row (0.50).
    assert tables["sales"].best == "sales.amount"
    # A table whose own row is its best element is credited to the row.
    by_row = table_scores(rank(_scores(("shop", 0.9, 0.0), ("shop.region", 0.2, 0.0)), alpha=1.0))
    assert (by_row[0].table, by_row[0].best, by_row[0].score) == ("shop", "shop", 1.0)


def test_tables_are_ranked_best_first() -> None:
    assert [entry.table for entry in table_scores(rank(QUESTION, alpha=1.0))] == ["sales", "shop", "returns"]


# --------------------------------------------------------------------------
# Anchors and the anchor bound (DD-11)
# --------------------------------------------------------------------------


def test_a_table_is_an_anchor_when_its_score_reaches_the_cut() -> None:
    # Semantic only: sales 1.0, shop 0.875, returns 0.5.
    assert retrieve("q", QUESTION, (), _settings(alpha=1.0, anchor_cut=0.6)).anchors == ("sales", "shop")
    assert retrieve("q", QUESTION, (), _settings(alpha=1.0, anchor_cut=0.5)).anchors == ("sales", "shop", "returns")
    assert retrieve("q", QUESTION, (), _settings(alpha=1.0, anchor_cut=0.9)).anchors == ("sales",)


def test_the_cap_keeps_the_best_and_records_what_it_left_out() -> None:
    retrieval = retrieve("q", QUESTION, (), _settings(alpha=1.0, anchor_cut=0.5, anchor_cap=2))
    assert retrieval.anchors == ("sales", "shop")
    assert retrieval.anchor_bound.excluded_by_cap == ("returns",)
    assert (retrieval.anchor_bound.cut, retrieval.anchor_bound.cap) == (0.5, 2)


def test_a_bound_that_excluded_nothing_says_so() -> None:
    bound = retrieve("q", QUESTION, (), _settings(alpha=1.0)).anchor_bound
    assert bound.excluded_by_cap == () and bound.set_aside_as_rivals == ()


def test_no_table_at_the_cut_means_no_anchors_and_no_decline() -> None:
    """Recorded as built: the cut is applied as ruled, with no fallback to
    "the best table anyway". The keyword-only ranking of a question whose
    words occur nowhere scores every table 0."""
    scores = _scores(("sales.amount", 0.6, 0.0), ("shop.region", 0.4, 0.0))
    retrieval = retrieve("q", scores, (), _settings(alpha=0.0))
    assert retrieval.anchors == ()


# --------------------------------------------------------------------------
# No floor (withdrawn after the step 6 baseline falsified it)
# --------------------------------------------------------------------------


def test_nothing_is_declined_here_however_low_the_raw_similarity() -> None:
    """As first built, a question whose best raw similarity was at or below
    a floor was declined here. The floor, computed from the schema alone,
    declined all sixteen questions of the evaluation and was withdrawn by
    the owner. No number replaced it: the lowest scores still rank, and
    the best raw similarity is kept as information."""
    scores = _scores(("sales.amount", 0.05, 0.0), ("shop.region", 0.01, 0.0))
    retrieval = retrieve("q", scores, (), DEFAULTS)
    assert retrieval.anchors == ("sales",)
    assert retrieval.best_raw == 0.05
    assert not hasattr(retrieval, "declined") and not hasattr(DEFAULTS, "floor")


# --------------------------------------------------------------------------
# Terms (ruling h)
# --------------------------------------------------------------------------


def _tokens(text: str, stopwords: set[str]) -> tuple[Token, ...]:
    return tuple(Token(word, word.lower() not in stopwords and word.isalnum()) for word in text.split())


def test_terms_are_the_content_words_then_the_adjacent_pairs() -> None:
    tokens = _tokens("What is the income band of each web page", {"what", "is", "the", "of", "each"})
    assert make_terms(tokens) == (
        Term("income", "word"),
        Term("band", "word"),
        Term("web", "word"),
        Term("page", "word"),
        Term("income band", "bigram"),
        Term("web page", "bigram"),
    )


def test_a_stopword_or_punctuation_between_two_words_breaks_the_pair() -> None:
    assert Term("number dependents", "bigram") not in make_terms(_tokens("number of dependents", {"of"}))
    assert make_terms(_tokens("sells most , and shipping", {"most", "and"})) == (
        Term("sells", "word"),
        Term("shipping", "word"),
    )


def test_terms_are_lower_cased_and_a_repeat_is_kept_once() -> None:
    assert make_terms(_tokens("Revenue against revenue", {"against"})) == (Term("revenue", "word"),)


# --------------------------------------------------------------------------
# What a term matched (FR-41), and what it did not
# --------------------------------------------------------------------------

AMOUNT = (
    Term("amount", "word"),
    _scores(("sales.amount", 0.70, 1.0), ("returns.amount", 0.68, 1.0), ("shop.region", 0.10, 0.0)),
)
REGION = (
    Term("region", "word"),
    _scores(("sales.amount", 0.10, 0.0), ("returns.amount", 0.10, 0.0), ("shop.region", 0.80, 1.0)),
)
WEATHER = (
    Term("weather", "word"),
    _scores(("sales.amount", 0.12, 0.0), ("returns.amount", 0.11, 0.0), ("shop.region", 0.15, 0.0)),
)


def test_a_term_records_what_it_considered_and_what_it_chose() -> None:
    (term,) = retrieve("q", QUESTION, (REGION,), DEFAULTS).terms
    assert (term.chosen_table, term.chosen_element) == ("shop", "shop.region")
    assert term.considered[0] == ("shop.region", 1.0)
    assert [element for element, _ in term.considered] == ["shop.region", "returns.amount", "sales.amount"]
    assert term.rivals == ()


def test_every_term_nominates_the_table_of_its_best_element_however_weak_the_match() -> None:
    """No threshold: a term always has a best element, and that element's
    table is its nominee. "weather" matches nothing in this schema and
    still nominates the table it is least unlike. Its raw similarity is
    kept, so a reader can see how weak the nomination is."""
    retrieval = retrieve("q", QUESTION, (REGION, WEATHER), DEFAULTS)
    weather = retrieval.terms[1]
    assert (weather.chosen_table, weather.chosen_element) == ("shop", "shop.region")
    assert weather.best_raw == 0.15
    assert weather.considered[0][0] == "shop.region"


def test_equal_scores_nominate_the_name_that_sorts_first() -> None:
    term = (Term("amount", "word"), _scores(("sales.amount", 0.5, 0.0), ("returns.amount", 0.5, 0.0), ("shop", 0.1, 0.0)))
    (result,) = retrieve("q", QUESTION, (term,), DEFAULTS).terms
    assert result.chosen_table == "returns"
    assert [rival.rival for rival in result.rivals] == ["sales"]


def test_a_weak_terms_nomination_keeps_a_table_from_being_set_aside() -> None:
    """The effect of having no threshold, stated as a test. "returns" is a
    rival for "amount". A second term that matches nothing well still has
    a best element, and it is in "returns": so "returns" is nominated in
    its own right and stays an anchor."""
    weak = (
        Term("weather", "word"),
        _scores(("sales.amount", 0.11, 0.0), ("returns.amount", 0.15, 0.0), ("shop.region", 0.02, 0.0)),
    )
    settings = _settings(alpha=1.0, anchor_cut=0.5)
    assert retrieve("q", QUESTION, (AMOUNT,), settings).anchor_bound.set_aside_as_rivals == ("returns",)
    assert retrieve("q", QUESTION, (AMOUNT, weak), settings).anchor_bound.set_aside_as_rivals == ()


# --------------------------------------------------------------------------
# Rivals (ruling c)
# --------------------------------------------------------------------------


def test_a_table_within_the_margin_of_a_terms_choice_is_its_rival() -> None:
    (term,) = retrieve("q", QUESTION, (AMOUNT,), DEFAULTS).terms
    # Semantic 0.70 against 0.68 over a range of 0.60: 1.0 against 0.967,
    # and the keyword half is equal. Combined: 1.0 against 0.983.
    (rival,) = term.rivals
    assert (rival.term, rival.chosen, rival.rival) == ("amount", "sales", "returns")
    assert (rival.chosen_element, rival.rival_element) == ("sales.amount", "returns.amount")
    assert rival.chosen_score == 1.0
    assert rival.rival_score == pytest.approx(0.9833, abs=1e-4)


def test_a_table_outside_the_margin_is_not_a_rival() -> None:
    (term,) = retrieve("q", QUESTION, (AMOUNT,), _settings(margin=0.01)).terms
    assert term.rivals == ()


def test_a_table_exactly_the_margin_behind_is_not_a_rival() -> None:
    """A rival differs by LESS than the margin. Binary fractions, so the
    difference is exactly 0.25 and the boundary is really tested."""
    term = (Term("amount", "word"), _scores(("sales.amount", 1.0, 0.0), ("returns.amount", 0.75, 0.0), ("shop", 0.0, 0.0)))
    at = retrieve("q", QUESTION, (term,), _settings(alpha=1.0, margin=0.25)).terms[0]
    assert at.rivals == ()
    inside = retrieve("q", QUESTION, (term,), _settings(alpha=1.0, margin=0.26)).terms[0]
    assert [rival.rival for rival in inside.rivals] == ["returns"]


def test_a_rival_is_not_made_an_anchor_beside_the_table_that_beat_it() -> None:
    """Both tables reach the cut on the whole question. One term, "amount",
    is why, and it could mean either. One is chosen; the other is set aside
    and the bound says so. Never both."""
    settings = _settings(alpha=1.0, anchor_cut=0.5)
    assert retrieve("q", QUESTION, (), settings).anchors == ("sales", "shop", "returns")

    retrieval = retrieve("q", QUESTION, (AMOUNT, REGION), settings)
    assert retrieval.anchors == ("sales", "shop")
    assert retrieval.anchor_bound.set_aside_as_rivals == ("returns",)
    assert [(rival.chosen, rival.rival) for rival in retrieval.rivals] == [("sales", "returns")]


def test_a_rival_that_another_term_nominates_stays_an_anchor() -> None:
    """ "returns" is a rival for "amount" and the choice of a second term in
    its own right. It is wanted for itself, so it is kept; the rivalry is
    still handed on."""
    refunds = (
        Term("refunds", "word"),
        _scores(("sales.amount", 0.20, 0.0), ("returns.amount", 0.75, 0.0), ("shop.region", 0.10, 0.0)),
    )
    retrieval = retrieve("q", QUESTION, (AMOUNT, refunds), _settings(alpha=1.0, anchor_cut=0.5))
    assert "returns" in retrieval.anchors
    assert retrieval.anchor_bound.set_aside_as_rivals == ()
    assert [(rival.chosen, rival.rival) for rival in retrieval.rivals] == [("sales", "returns")]


def test_a_rival_is_not_set_aside_when_the_table_that_beat_it_is_out_of_the_running() -> None:
    """Setting a table aside for one that is not an anchor either would
    lose both."""
    question = _scores(("sales.amount", 0.20, 0.0), ("returns.amount", 0.60, 0.0), ("shop.region", 0.10, 0.0))
    retrieval = retrieve("q", question, (AMOUNT,), _settings(alpha=1.0, anchor_cut=0.5))
    assert retrieval.anchors == ("returns",)
    assert retrieval.anchor_bound.set_aside_as_rivals == ()


def test_the_cap_applies_after_rivals_are_set_aside() -> None:
    retrieval = retrieve("q", QUESTION, (AMOUNT,), _settings(alpha=1.0, anchor_cut=0.5, anchor_cap=2))
    assert retrieval.anchors == ("sales", "shop")
    assert retrieval.anchor_bound.set_aside_as_rivals == ("returns",)
    assert retrieval.anchor_bound.excluded_by_cap == ()


# --------------------------------------------------------------------------
# Partners (the correction to ruling c at the third review stop)
# --------------------------------------------------------------------------

# Which tables a foreign key joins directly, either way round. In the
# miniature a return refers to its sale, and a sale to its shop.
JOINED = {"sales": frozenset({"returns", "shop"}), "returns": frozenset({"sales"}), "shop": frozenset({"sales"})}
# The same schema without the key from a return to its sale.
APART = {"sales": frozenset({"shop"}), "returns": frozenset(), "shop": frozenset({"sales"})}


def test_two_tables_joined_by_a_foreign_key_are_partners_and_never_rivals() -> None:
    """ "amount" is best matched in sales and almost as well in returns.
    One refers to the other: an answer about one routinely needs both, so
    they are not alternatives for one role. Neither is set aside."""
    settings = _settings(alpha=1.0, anchor_cut=0.5)
    retrieval = retrieve("q", QUESTION, (AMOUNT, REGION), settings, partners=JOINED)

    assert retrieval.rivals == ()
    assert retrieval.anchors == ("sales", "shop", "returns")
    assert retrieval.anchor_bound.set_aside_as_rivals == ()


def test_a_partner_within_the_margin_is_recorded_as_one() -> None:
    """Nothing is dropped silently: the term still says returns was within
    the margin, and that it was left alone because the two are joined."""
    (term,) = retrieve("q", QUESTION, (AMOUNT,), DEFAULTS, partners=JOINED).terms
    assert term.rivals == ()
    (partner,) = term.partners
    assert (partner.term, partner.chosen, partner.rival) == ("amount", "sales", "returns")
    assert partner.rival_score == pytest.approx(0.9833, abs=1e-4)


def test_two_tables_not_joined_and_close_in_score_are_still_rivals() -> None:
    """The same scores, the same margin, and no key between the two: one is
    chosen and the other set aside, exactly as before the correction."""
    settings = _settings(alpha=1.0, anchor_cut=0.5)
    retrieval = retrieve("q", QUESTION, (AMOUNT, REGION), settings, partners=APART)

    assert [(rival.chosen, rival.rival) for rival in retrieval.rivals] == [("sales", "returns")]
    assert retrieval.terms[0].partners == ()
    assert retrieval.anchors == ("sales", "shop")
    assert retrieval.anchor_bound.set_aside_as_rivals == ("returns",)


def test_the_key_may_point_either_way() -> None:
    """A partner is a partner whichever of the two holds the foreign key,
    and whichever of the two the term chose."""
    reversed_scores = (
        Term("amount", "word"),
        _scores(("sales.amount", 0.68, 1.0), ("returns.amount", 0.70, 1.0), ("shop.region", 0.10, 0.0)),
    )
    for term in (AMOUNT, reversed_scores):
        for joined in ({"sales": frozenset({"returns"})}, {"returns": frozenset({"sales"})}):
            (result,) = retrieve("q", QUESTION, (term,), DEFAULTS, partners=joined).terms
            assert result.rivals == ()
            assert len(result.partners) == 1


def test_one_term_can_have_a_partner_and_a_rival() -> None:
    """Three tables within the margin of one another for one term. The one
    joined to the choice is kept; the one that is not is still set aside."""
    term = (
        Term("amount", "word"),
        _scores(("sales.amount", 1.0, 0.0), ("returns.amount", 0.95, 0.0), ("shop.region", 0.9, 0.0), ("shop", 0.0, 0.0)),
    )
    joined = {"sales": frozenset({"shop"}), "shop": frozenset({"sales"})}
    retrieval = retrieve("q", QUESTION, (term,), _settings(alpha=1.0, anchor_cut=0.5), partners=joined)

    (result,) = retrieval.terms
    assert [rival.rival for rival in result.rivals] == ["returns"]
    assert [partner.rival for partner in result.partners] == ["shop"]
    assert retrieval.anchors == ("sales", "shop")
    assert retrieval.anchor_bound.set_aside_as_rivals == ("returns",)


def test_a_partner_of_one_terms_choice_is_still_set_aside_as_the_rival_of_anothers() -> None:
    """Being joined to one table protects a table from that table only.
    returns is joined to sales, which "amount" chose, and is not joined to
    shop, which "place" chose with returns within the margin. Nothing
    nominates returns, so it is set aside, for shop."""
    place = (
        Term("place", "word"),
        _scores(("sales.amount", 0.0, 0.0), ("returns.amount", 0.95, 0.0), ("shop.region", 1.0, 0.0)),
    )
    retrieval = retrieve("q", QUESTION, (AMOUNT, place), _settings(alpha=1.0, anchor_cut=0.5), partners=JOINED)

    assert [(rival.chosen, rival.rival) for rival in retrieval.rivals] == [("shop", "returns")]
    assert retrieval.anchor_bound.set_aside_as_rivals == ("returns",)


def test_handed_no_partners_no_two_tables_are_joined() -> None:
    assert retrieve("q", QUESTION, (AMOUNT,), DEFAULTS).terms == retrieve(
        "q", QUESTION, (AMOUNT,), DEFAULTS, partners={}
    ).terms


# --------------------------------------------------------------------------
# A table is set aside only for a winner that is still an anchor after the
# cap (ruling 1 of the fourth review stop)
# --------------------------------------------------------------------------

# Five tables, scored so that alpha 1.0 leaves the typed numbers as they
# are: "floor" is there only to be the 0.
FIVE = _scores(
    ("returns.amount", 1.0, 0.0),
    ("item.name", 0.95, 0.0),
    ("shop.region", 0.9, 0.0),
    ("sales.amount", 0.8, 0.0),
    ("depot.name", 0.5, 0.0),
    ("floor", 0.0, 0.0),
)


def _choice(text: str, chosen: str, rival: str) -> tuple:
    """A term best matched in `chosen`, with `rival` just behind it."""
    return (Term(text, "word"), _scores((chosen, 1.0, 0.0), (rival, 0.95, 0.0), ("floor", 0.0, 0.0)))


def test_a_table_set_aside_for_a_winner_the_cap_then_cuts_is_not_lost() -> None:
    """ "store" chose depot, with returns within the margin. depot reaches
    the cut and falls to the cap. Setting returns aside for it would keep
    neither, and nothing would say so. returns is given back, and competes
    under the cap by its score like any other table: here it is the best."""
    settings = _settings(alpha=1.0, anchor_cut=0.5, anchor_cap=3)
    retrieval = retrieve("q", FIVE, (_choice("store", "depot.name", "returns.amount"),), settings)

    assert retrieval.anchors == ("returns", "item", "shop")
    assert retrieval.anchor_bound.set_aside_as_rivals == ()
    assert retrieval.anchor_bound.excluded_by_cap == ("sales", "depot")
    # The close call itself is still on the term.
    assert [(rival.chosen, rival.rival) for rival in retrieval.rivals] == [("depot", "returns")]


def test_a_table_is_still_set_aside_for_a_winner_that_is_an_anchor() -> None:
    """The same question with room for depot under the cap: depot is an
    anchor, so returns is set aside for it, as before."""
    settings = _settings(alpha=1.0, anchor_cut=0.5, anchor_cap=4)
    retrieval = retrieve("q", FIVE, (_choice("store", "depot.name", "returns.amount"),), settings)

    assert retrieval.anchors == ("item", "shop", "sales", "depot")
    assert retrieval.anchor_bound.set_aside_as_rivals == ("returns",)


def test_one_winner_that_is_an_anchor_is_enough_to_set_a_table_aside() -> None:
    """returns is within the margin of depot, which the cap cuts, and of
    item, which is an anchor. It stays set aside, for item."""
    terms = (_choice("store", "depot.name", "returns.amount"), _choice("thing", "item.name", "returns.amount"))
    retrieval = retrieve("q", FIVE, terms, _settings(alpha=1.0, anchor_cut=0.5, anchor_cap=3))

    assert retrieval.anchors == ("item", "shop", "sales")
    assert retrieval.anchor_bound.set_aside_as_rivals == ("returns",)
    assert retrieval.anchor_bound.excluded_by_cap == ("depot",)


def test_a_table_given_back_can_push_another_winner_over_the_cap_and_free_its_rival_too() -> None:
    """The rule is applied until nothing changes. depot falls to the cap,
    so returns comes back; returns is the best table and pushes shop over
    the cap; shop was why sales was set aside, so sales comes back as well.
    No table is left set aside for a table that is not an anchor."""
    terms = (_choice("store", "depot.name", "returns.amount"), _choice("place", "shop.region", "sales.amount"))
    retrieval = retrieve("q", FIVE, terms, _settings(alpha=1.0, anchor_cut=0.5, anchor_cap=2))

    assert retrieval.anchors == ("returns", "item")
    assert retrieval.anchor_bound.set_aside_as_rivals == ()
    assert retrieval.anchor_bound.excluded_by_cap == ("shop", "sales", "depot")


def test_whatever_is_set_aside_was_set_aside_for_an_anchor() -> None:
    """The property itself, over every cap, for the two questions above."""
    terms = (_choice("store", "depot.name", "returns.amount"), _choice("place", "shop.region", "sales.amount"))
    for cap in range(1, 6):
        retrieval = retrieve("q", FIVE, terms, _settings(alpha=1.0, anchor_cut=0.5, anchor_cap=cap))
        for table in retrieval.anchor_bound.set_aside_as_rivals:
            winners = {rival.chosen for rival in retrieval.rivals if rival.rival == table}
            assert winners & set(retrieval.anchors), (cap, table)
        # And nothing that reached the cut has gone missing.
        bound = retrieval.anchor_bound
        assert sorted((*retrieval.anchors, *bound.excluded_by_cap, *bound.set_aside_as_rivals)) == [
            "depot", "item", "returns", "sales", "shop",
        ]  # fmt: skip


# --------------------------------------------------------------------------
# What the PathFinder will be handed
# --------------------------------------------------------------------------


def test_column_scores_are_the_whole_questions_combined_scores_for_columns_only() -> None:
    retrieval = retrieve("q", QUESTION, (), DEFAULTS)
    assert set(retrieval.column_scores) == {"sales.amount", "sales.shop_key", "returns.amount", "shop.region"}
    assert retrieval.column_scores["sales.amount"] == 1.0
    assert retrieval.score_of("sales") == 1.0


# --------------------------------------------------------------------------
# Settings
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad", [{"alpha": 1.5}, {"alpha": -0.1}, {"anchor_cut": 2}, {"anchor_cap": 0}, {"margin": -0.01}]
)
def test_settings_outside_their_range_raise(bad) -> None:
    with pytest.raises(ValueError):
        _settings(**bad)


def test_nothing_scored_raises() -> None:
    with pytest.raises(ValueError, match="nothing was scored"):
        retrieve("q", (), (), DEFAULTS)
