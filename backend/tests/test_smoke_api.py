"""The smoke check's account (step 11; the owner's ruling 1 at the plan
stop): one account a run, smoke-<UTC timestamp>-<random>@cartograph.invalid,
with a password made at random each run and never printed."""

import re

from app import smoke_api


def test_the_smoke_account_is_new_each_run_and_named_for_when() -> None:
    first, second = smoke_api.smoke_email(), smoke_api.smoke_email()
    for email in (first, second):
        assert re.fullmatch(r"smoke-\d{8}T\d{6}Z-[0-9a-f]{8}@cartograph\.invalid", email), email
    assert first != second


def test_the_smoke_password_is_random_and_of_an_accounts_length() -> None:
    passwords = {smoke_api.smoke_password() for _ in range(20)}
    assert len(passwords) == 20
    assert all(8 <= len(password) <= 128 for password in passwords)


def test_the_cookie_is_read_for_its_flags_without_its_value() -> None:
    header = "cartograph_session=SECRET-TOKEN; HttpOnly; Max-Age=604800; Path=/; SameSite=lax"
    token, flags = smoke_api.session_cookie(header)
    assert token == "SECRET-TOKEN"
    assert flags == "HttpOnly, Max-Age=604800, Path=/, SameSite=lax"
    assert "SECRET" not in flags


def test_a_cookie_without_its_flags_is_said_so() -> None:
    _, flags = smoke_api.session_cookie("cartograph_session=t; Path=/")
    assert flags == "Path=/"
    assert not smoke_api.flags_hold(flags)
    assert smoke_api.flags_hold("HttpOnly, Max-Age=604800, Path=/, SameSite=lax")
    assert not smoke_api.flags_hold("HttpOnly, Max-Age=604800, Path=/, SameSite=lax, Domain=example.com")


def test_nothing_the_smoke_check_prints_can_carry_the_password_or_the_token() -> None:
    source = (smoke_api.__file__ and open(smoke_api.__file__).read()) or ""
    for line in source.splitlines():
        if "print(" in line:
            assert "password" not in line and "token" not in line, line
