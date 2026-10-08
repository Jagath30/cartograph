"""The margin and the floor, from numbers written by hand (rulings a, c, d).

Nothing here is an embedding and nothing reads the evaluation set. The
miniature: "order" has two keys to "place" (billing and shipping), "place"
is linked to "order", and "weather" and "moon" are linked to nothing.
"""

import pytest

from app.core.calibration import (
    Pseudo,
    floor,
    floor_bests,
    linked_tables,
    margin,
    margin_differences,
    percentile,
    sibling_pairs,
)
from app.core.retriever import RawScore
from app.core.snapshot import Column, ForeignKey, SchemaSnapshot, Table

SNAPSHOT = SchemaSnapshot(
    tables=(Table("order"), Table("place"), Table("weather"), Table("moon")),
    columns=(
        Column("order", "bill", "bigint"),
        Column("order", "ship", "bigint"),
        Column("order", "day", "bigint"),
        Column("place", "id", "bigint"),
        Column("weather", "id", "bigint"),
        Column("weather", "rain", "numeric"),
        Column("moon", "phase", "text"),
    ),
    primary_keys=(),
    foreign_keys=(
        ForeignKey("order", ("bill",), "place", ("id",), "catalog"),
        ForeignKey("order", ("ship",), "place", ("id",), "catalog"),
        ForeignKey("order", ("day",), "weather", ("id",), "overlay"),
    ),
)
PAIRS = sibling_pairs(SNAPSHOT)


def _scores(**semantic: float) -> tuple[RawScore, ...]:
    """order_bill=0.5 -> RawScore("order", "bill", 0.5, 0). Every element
    not named scores 0."""
    named = {tuple(name.split("_", 1)): value for name, value in semantic.items()}
    elements = [(column.table, column.name) for column in SNAPSHOT.columns]
    return tuple(RawScore(table, column, named.get((table, column), 0.0), 0.0) for table, column in elements)


# --------------------------------------------------------------------------
# What the methods are computed over
# --------------------------------------------------------------------------


def test_sibling_keys_run_from_the_same_table_to_the_same_table() -> None:
    assert PAIRS == ((("order", ("bill",)), ("order", ("ship",))),)


def test_three_siblings_are_three_pairs() -> None:
    more = SchemaSnapshot(
        SNAPSHOT.tables,
        SNAPSHOT.columns + (Column("order", "mail", "bigint"),),
        (),
        SNAPSHOT.foreign_keys + (ForeignKey("order", ("mail",), "place", ("id",), "catalog"),),
    )
    assert len(sibling_pairs(more)) == 3


def test_tables_are_linked_by_a_key_in_either_direction() -> None:
    linked = linked_tables(SNAPSHOT)
    assert linked["order"] == {"place", "weather"}
    assert linked["place"] == {"order"} and linked["weather"] == {"order"}
    assert linked["moon"] == frozenset()


def test_a_percentile_is_the_nearest_rank_and_always_a_value_that_was_seen() -> None:
    values = [float(n) for n in range(1, 21)]
    assert percentile(values, 95) == 19.0
    assert percentile(values, 50) == 10.0
    assert percentile(values, 100) == 20.0
    assert percentile([3.0, 1.0, 2.0], 50) == 2.0
    assert percentile([7.0], 95) == 7.0
    with pytest.raises(ValueError):
        percentile([], 50)
    with pytest.raises(ValueError):
        percentile([1.0], 0)


# --------------------------------------------------------------------------
# The margin
# --------------------------------------------------------------------------


def test_the_margin_reads_the_difference_between_siblings_under_unrelated_text() -> None:
    """Semantic only (alpha 1). Under "rain", bill scores 0.25 and ship 0.5
    while weather.rain, the pseudo-question's own table, is left out; so
    the best remaining raw score is 0.5 and the worst 0. Normalised: 0.5
    and 1.0. Difference 0.5."""
    rain = Pseudo("weather", "rain", _scores(order_bill=0.25, order_ship=0.5, weather_rain=0.99))
    assert margin_differences((rain,), SNAPSHOT, PAIRS, alpha=1.0) == [0.5]


def test_a_pseudo_question_is_never_compared_with_its_own_table() -> None:
    """With weather.rain left in, the range would run to 0.99 and the same
    two keys would differ by about 0.25, not 0.5."""
    rain = Pseudo("weather", "rain", _scores(order_bill=0.25, order_ship=0.5, weather_rain=0.99))
    (difference,) = margin_differences((rain,), SNAPSHOT, PAIRS, alpha=1.0)
    assert difference == 0.5 != pytest.approx(0.25 / 0.99)


def test_a_pseudo_question_from_either_end_of_the_pair_is_not_used() -> None:
    """Text from "order" or from "place" is about the pair, not unrelated
    to it: what it says of bill against ship is signal, not noise."""
    scores = _scores(order_bill=0.25, order_ship=0.5)
    pseudos = (Pseudo("order", "order — ship", scores), Pseudo("place", "place", scores))
    assert margin_differences(pseudos, SNAPSHOT, PAIRS, alpha=1.0) == []


def test_the_margin_is_the_95th_percentile_of_the_differences() -> None:
    """Twenty pseudo-questions from "moon": ship scores 1, a third column
    0, and bill n/20 of the way up, so the differences are 20/20 ... 1/20."""
    pseudos = tuple(
        Pseudo("moon", f"phase {n}", _scores(order_ship=1.0, order_bill=n / 20)) for n in range(20)
    )
    measured = margin(pseudos, SNAPSHOT, PAIRS, alpha=1.0)
    assert measured.observations == 20
    assert measured.value == pytest.approx(19 / 20)


def test_the_margin_depends_on_alpha() -> None:
    """The siblings differ in meaning and not in wording. Counting only the
    keyword half, which is flat, they do not differ at all."""
    rain = Pseudo("weather", "rain", _scores(order_bill=0.25, order_ship=0.5))
    assert margin((rain,), SNAPSHOT, PAIRS, alpha=1.0).value == 0.5
    assert margin((rain,), SNAPSHOT, PAIRS, alpha=0.5).value == 0.25
    assert margin((rain,), SNAPSHOT, PAIRS, alpha=0.0).value == 0.0


# --------------------------------------------------------------------------
# The floor
# --------------------------------------------------------------------------


def test_a_floor_best_is_taken_over_tables_with_no_key_to_the_pseudo_questions_own() -> None:
    """ "rain" is from weather. order is linked to weather and is left out
    although it scores highest; place and moon are not linked."""
    rain = Pseudo("weather", "rain", _scores(order_day=0.9, place_id=0.3, moon_phase=0.4, weather_rain=1.0))
    assert floor_bests((rain,), linked_tables(SNAPSHOT)) == [0.4]


def test_the_floor_reads_raw_similarity() -> None:
    """0.4 as scored, not 1.0 as it would be once normalised."""
    rain = Pseudo("weather", "rain", _scores(place_id=0.3, moon_phase=0.4))
    assert floor((rain,), linked_tables(SNAPSHOT)).value == 0.4


def test_the_floor_is_the_median_of_the_bests_not_of_every_pair() -> None:
    """Three pseudo-questions from moon, which is linked to nothing. Their
    bests are 0.2, 0.6 and 0.8; the median best is 0.6. The median over
    every single pair would be far lower: most pairs score 0."""
    linked = linked_tables(SNAPSHOT)
    pseudos = (
        Pseudo("moon", "a", _scores(order_bill=0.2)),
        Pseudo("moon", "b", _scores(place_id=0.6, order_day=0.1)),
        Pseudo("moon", "c", _scores(weather_rain=0.8)),
    )
    measured = floor(pseudos, linked)
    assert (measured.value, measured.observations) == (0.6, 3)


def test_a_pseudo_question_linked_to_everything_gives_no_best() -> None:
    star = SchemaSnapshot(
        (Table("order"), Table("place")),
        (Column("order", "bill", "bigint"), Column("place", "id", "bigint")),
        (),
        (ForeignKey("order", ("bill",), "place", ("id",), "catalog"),),
    )
    scores = (RawScore("order", "bill", 0.5, 0.0), RawScore("place", "id", 0.7, 0.0))
    assert floor_bests((Pseudo("order", "order", scores),), linked_tables(star)) == []
