"""The deployed smoke check's pure parts (step 12): the cookie it accepts
from the public site, the six headers it requires on a page, and that
nothing it prints can carry the password or the session."""

from app import smoke_deployed


def test_the_deployed_cookie_must_be_secure() -> None:
    assert smoke_deployed.flags_hold("HttpOnly, Max-Age=604800, Path=/, SameSite=lax, Secure")
    assert not smoke_deployed.flags_hold("HttpOnly, Max-Age=604800, Path=/, SameSite=lax")
    assert not smoke_deployed.flags_hold("Max-Age=604800, Path=/, SameSite=lax, Secure")
    assert not smoke_deployed.flags_hold("HttpOnly, Max-Age=604800, Path=/, SameSite=lax, Secure, Domain=vercel.app")


def test_the_headers_are_the_six_of_next_config() -> None:
    assert len(smoke_deployed.SECURITY_HEADERS) == 6
    whole = {name.lower(): value for name, value in smoke_deployed.SECURITY_HEADERS.items()}
    assert smoke_deployed.headers_missing(whole) == []


def test_a_header_missing_or_different_is_named() -> None:
    whole = {name.lower(): value for name, value in smoke_deployed.SECURITY_HEADERS.items()}
    del whole["x-frame-options"]
    whole["referrer-policy"] = "no-referrer"
    assert smoke_deployed.headers_missing(whole) == ["Referrer-Policy", "X-Frame-Options"]


def test_header_names_are_read_in_any_case() -> None:
    upper = {name.upper(): value for name, value in smoke_deployed.SECURITY_HEADERS.items()}
    assert smoke_deployed.headers_missing(upper) == []


def test_nothing_the_deployed_smoke_prints_can_carry_the_password_or_the_token() -> None:
    source = open(smoke_deployed.__file__).read()
    for line in source.splitlines():
        if "print(" in line:
            assert "password" not in line and "token" not in line and ".cookie" not in line and "set_cookie" not in line, line
