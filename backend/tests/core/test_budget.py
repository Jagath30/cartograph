"""The deployed demonstration's spend ceiling and daily limit (NFR-25,
step 12): the rule that decides, pure. Runs in CI."""

from datetime import UTC, datetime, timedelta, timezone

import pytest

from app.config import MODEL_PRICES
from app.core.budget import (
    QUESTION_COST_BOUND_USD,
    RETRY_MESSAGE_CHARS,
    Allowance,
    day_start,
    month_start,
    question_cost_bound,
    refusal,
)

DEMO = Allowance(monthly_usd=2.00, daily_questions=30, question_bound_usd=0.03)


def test_a_question_runs_while_the_month_has_room_for_it() -> None:
    assert refusal(0.0, running=0, asked_today=0, allowance=DEMO) is None
    assert refusal(1.97, running=0, asked_today=0, allowance=DEMO) is None


def test_a_question_that_could_pass_the_budget_is_refused() -> None:
    # 1.98 + 0.03 = 2.01: over, though nothing has been spent past 2.
    assert refusal(1.98, running=0, asked_today=0, allowance=DEMO) == "budget_spent"


def test_the_questions_running_hold_their_bound() -> None:
    # 1.90 + three running and this one at 0.03 each = 2.02.
    assert refusal(1.90, running=2, asked_today=0, allowance=DEMO) is None
    assert refusal(1.90, running=3, asked_today=0, allowance=DEMO) == "budget_spent"


def test_the_daily_limit_is_the_most_a_user_may_ask() -> None:
    assert refusal(0.0, running=0, asked_today=29, allowance=DEMO) is None
    assert refusal(0.0, running=0, asked_today=30, allowance=DEMO) == "daily_limit"


def test_a_spent_month_is_said_before_a_daily_limit() -> None:
    assert refusal(2.0, running=0, asked_today=30, allowance=DEMO) == "budget_spent"


@pytest.mark.parametrize("field", ["monthly_usd", "daily_questions", "question_bound_usd"])
def test_an_allowance_of_nothing_is_refused(field) -> None:
    values = dict(monthly_usd=2.0, daily_questions=30, question_bound_usd=0.03) | {field: 0}
    with pytest.raises(ValueError):
        Allowance(**values)


def test_the_month_and_the_day_begin_in_utc() -> None:
    # 00:30 on 1 November in Singapore is still 31 October in UTC.
    singapore = timezone(timedelta(hours=8))
    at = datetime(2026, 11, 1, 0, 30, tzinfo=singapore)
    assert month_start(at) == datetime(2026, 10, 1, tzinfo=UTC)
    assert day_start(at) == datetime(2026, 10, 31, tzinfo=UTC)
    assert month_start(datetime(2026, 10, 31, 23, 59, tzinfo=UTC)) == datetime(2026, 10, 1, tzinfo=UTC)


def test_the_bound_counts_every_call_at_its_most() -> None:
    prices = dict(input_price=MODEL_PRICES["gpt-6-luna"].input, output_price=MODEL_PRICES["gpt-6-luna"].output)
    # One call, no retry: the prompt in, the cap out.
    one = question_cost_bound(10_000, 800, calls=1, retries=0, **prices)
    assert one == pytest.approx((10_000 * 0.10 + 800 * 0.50) / 1_000_000)
    # Each retry adds the reply before it and its message to every call.
    two = question_cost_bound(10_000, 800, calls=5, retries=2, **prices)
    assert two == pytest.approx(5 * ((10_000 + 2 * (800 + RETRY_MESSAGE_CHARS)) * 0.10 + 800 * 0.50) / 1_000_000)


def test_the_constant_is_the_one_the_demo_runs() -> None:
    assert Allowance(2.0, 30).question_bound_usd == QUESTION_COST_BOUND_USD
