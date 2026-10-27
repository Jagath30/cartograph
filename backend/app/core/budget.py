"""The deployed demonstration's spend ceiling and daily limit (NFR-25,
step 12). Pure: given what has been spent and asked, it says whether one
more question may run.

THE MONTH'S SPEND is what the application store records: every stored
query's cost_estimate since the first of this calendar month, UTC. Never
the spend ledger, which is a file, and the host's disk does not last.

BEFORE A QUESTION RUNS, it is refused if this month's spend, plus the most
each question already running may cost, plus the most this one may cost,
would pass the month's budget. The questions running are counted because
two can be asked at once (NFR-06): each holds its bound until it ends.

THE MOST ONE QUESTION CAN COST is QUESTION_COST_BOUND_USD, a constant
worked out from the code, never from what questions have cost (the
derivation is `question_cost_bound`; a test checks the constant covers it
for the real prompts). A question makes at most 3 + MAX_RETRIES model
calls: linking, choosing and generation, and two retries shared among
them (NFR-15). No call is sent more than the largest first prompt plus,
for each retry, the reply before it and the retry message (an allowance:
RETRY_MESSAGE_CHARS); a character is at least a token. Every reply is at
most the largest output cap.

ONE USER asks at most `daily_questions` a UTC day; a rerun calls no model
and is not counted. Rate limiting proper is step 14 (FR-36).

Commands and the evaluation never come here: they keep CEILING_USD and
the ledger, as before.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

# Above question_cost_bound for the real prompts, with room to spare: at
# step 12 that is $0.023246 (tests/test_question_cost_bound.py prints it).
QUESTION_COST_BOUND_USD = 0.03

# What a retry message is allowed besides the reply it answers: the
# validator's feedback or a fault, with the retry rules around it. An
# allowance, not a proven maximum: the feedback is one line a finding and
# is not capped. A message past it costs at most $0.0000001 a character
# more, on each of the calls that carry it.
RETRY_MESSAGE_CHARS = 4000

Refusal = Literal["budget_spent", "daily_limit"]

BUDGET_SPENT = (
    "The demo's budget for this month is spent, so no more questions can be asked until the first of "
    "next month (UTC). Your stored answers can still be opened and run again."
)


def daily_limit_message(limit: int) -> str:
    return (
        f"You have asked {limit} questions today, the most one account may ask in a day. "
        "You can ask again after midnight UTC."
    )


@dataclass(frozen=True)
class Allowance:
    monthly_usd: float
    daily_questions: int
    question_bound_usd: float = QUESTION_COST_BOUND_USD

    def __post_init__(self) -> None:
        if self.monthly_usd <= 0 or self.daily_questions <= 0 or self.question_bound_usd <= 0:
            raise ValueError("a budget, a daily limit and a bound are each greater than zero")


def month_start(at: datetime) -> datetime:
    """The first instant of `at`'s calendar month, UTC."""
    utc = at.astimezone(UTC)
    return datetime(utc.year, utc.month, 1, tzinfo=UTC)


def day_start(at: datetime) -> datetime:
    """Midnight UTC of `at`'s day."""
    utc = at.astimezone(UTC)
    return datetime(utc.year, utc.month, utc.day, tzinfo=UTC)


def refusal(spent_this_month: float, running: int, asked_today: int, allowance: Allowance) -> Refusal | None:
    """None when the question may run. `running` is how many questions are
    running now; `asked_today` counts this user's, those running included."""
    if spent_this_month + (running + 1) * allowance.question_bound_usd > allowance.monthly_usd:
        return "budget_spent"
    if asked_today >= allowance.daily_questions:
        return "daily_limit"
    return None


def question_cost_bound(
    largest_prompt_chars: int, max_output_tokens: int, calls: int, retries: int, input_price: float, output_price: float
) -> float:
    """The most one question can cost, in US dollars, at these prices per
    million tokens, never counting a cached token as cheaper."""
    most_in = largest_prompt_chars + retries * (max_output_tokens + RETRY_MESSAGE_CHARS)
    return calls * (most_in * input_price + max_output_tokens * output_price) / 1_000_000
