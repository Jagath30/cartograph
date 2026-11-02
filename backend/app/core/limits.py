"""The rate limits (FR-36, item 112, step 14). Pure: what each limit is,
the key it is counted under, and what the page is told when it is reached.

Five limits, each counted in Redis in a fixed window that starts at the
first request it counts and ends when its key expires:

  login_limit     5 failed logins for one email in 15 minutes. Counted on
                  failure only, cleared by a success. Reached, every login
                  for that email is 429 until the window ends, the right
                  password included, and argon2 is not run. The message
                  is the same whether or not an account holds the email.
  login_ceiling   300 login attempts an hour, from everyone: it bounds the
                  hashing a 0.1-CPU host is asked to do.
  register_limit  30 registrations an hour, from everyone.
  minute_limit    10 questions a minute for one user, every ask counted,
                  a cached answer included. Beside the 30 a day
                  (daily_limit, app.core.budget), which it does not
                  replace.
  rerun_limit     30 reruns an hour for one user.

A KEY NEVER HOLDS AN EMAIL: it holds the email's sha256, the email first
put as a login reads it (trimmed, lower case). A user is their id.

Every 429 carries Retry-After, the seconds left in the window, and says
in words which limit it is and when it lifts.

Deferred, and recorded (CHECKPOINTS.md, step 14): limits per client
address, since behind Vercel and Render the address is not yet one to
trust; and the two global ceilings can be used by anyone to lock everyone
out of logging in, or registering, for up to an hour.
"""

import hashlib
from dataclasses import dataclass
from typing import Literal

LimitCode = Literal["login_limit", "login_ceiling", "register_limit", "minute_limit", "rerun_limit"]


@dataclass(frozen=True)
class Limit:
    code: LimitCode
    count: int
    window_seconds: int
    # What was reached, in words, before "You can ... again in ...".
    reached: str
    again: str

    def key(self, subject: str) -> str:
        return f"cartograph:limit:{self.code}:{subject}"

    def message(self, retry_after: int) -> str:
        return f"{self.reached} You can {self.again} again in {lifts_in(retry_after)}."


LOGIN_FAILURES = Limit(
    "login_limit", 5, 15 * 60,
    "Too many failed sign-ins for this email: 5 in 15 minutes is the most allowed.", "try to sign in",
)  # fmt: skip
LOGIN_CEILING = Limit(
    "login_ceiling", 300, 60 * 60,
    "This demo has had too many sign-in attempts this hour: 300 an hour is the most it takes from everyone.",
    "try to sign in",
)  # fmt: skip
REGISTRATIONS = Limit(
    "register_limit", 30, 60 * 60,
    "This demo has had too many new accounts this hour: 30 an hour is the most it takes from everyone.",
    "register",
)  # fmt: skip
QUESTIONS = Limit(
    "minute_limit", 10, 60,
    "You have asked 10 questions in the last minute, the most one account may ask in a minute.", "ask",
)  # fmt: skip
RERUNS = Limit(
    "rerun_limit", 30, 60 * 60,
    "You have run stored SQL again 30 times this hour, the most one account may in an hour.", "run SQL",
)  # fmt: skip

LIMITS: tuple[Limit, ...] = (LOGIN_FAILURES, LOGIN_CEILING, REGISTRATIONS, QUESTIONS, RERUNS)

# Every global ceiling is counted under this one subject.
EVERYONE = "all"


def email_subject(email: str) -> str:
    """The email's sha256, as a login reads the email. Never the email."""
    return hashlib.sha256(email.strip().lower().encode()).hexdigest()


def user_subject(user_id: int) -> str:
    return f"user:{user_id}"


def retry_after(ttl_seconds: int | None, limit: Limit) -> int:
    """Seconds until the window ends, at least 1. A key with no expiry
    (which the counter never leaves) is given the whole window."""
    if ttl_seconds is None or ttl_seconds < 0:
        return limit.window_seconds
    return max(1, ttl_seconds)


def lifts_in(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds} second{'s' if seconds != 1 else ''}"
    minutes = -(-seconds // 60)
    return f"{minutes} minute{'s' if minutes != 1 else ''}"
