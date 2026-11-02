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


# --------------------------------------------------------------------------
# Step 14: the second asking, and the lockout
# --------------------------------------------------------------------------


class Scripted:
    """A browser whose answers are given in order; it keeps what it sent."""

    def __init__(self, answers) -> None:
        self.answers = list(answers)
        self.sent: list = []
        self.headers: dict[str, str] = {}

    def call(self, path, body=None, method=None, origin=None):
        self.sent.append((path, body))
        status, answer, headers = self.answers.pop(0)
        self.headers = headers
        return status, answer


def asked(query_id="q1", cached=None, cost=0.0003, rows=(["CA", 1],)):
    trace = {"cost_usd": cost, "tokens_in": 0 if cached else 900, "tokens_out": 0 if cached else 60, "duration_ms": 9}
    if cached:
        trace["served_from_cache"] = True
    return {"query_id": query_id, "status": "answered", "rows": [list(r) for r in rows], "row_count": len(rows), "trace": trace}


def test_the_second_asking_must_be_a_hit_at_no_cost(capsys) -> None:
    from app.smoke_api import cached_again

    first = asked()
    assert cached_again(Scripted([(200, asked("q2", cached=True, cost=0.0), {})]), first) == ("q2", True)
    assert "CACHED PASS" in capsys.readouterr().out
    for second in (asked("q2", cached=None), asked("q2", cached=True, cost=0.0001), asked("q2", cached=True, cost=0.0, rows=())):
        assert cached_again(Scripted([(200, second, {})]), first)[1] is False
    assert cached_again(Scripted([(200, asked("q1", cached=True, cost=0.0), {})]), first)[1] is False
    assert "CACHED FAIL" in capsys.readouterr().out


def test_the_lockout_needs_five_401s_then_a_429_with_retry_after(capsys) -> None:
    five = [(401, {"code": "invalid_credentials"}, {})] * 5
    sixth = (429, {"code": "login_limit"}, {"retry-after": "900"})
    browser = Scripted(five + [sixth])
    assert smoke_deployed.locked_out(browser, "smoke-20261102t090000z-0a1b2c3d@cartograph.invalid") is True
    assert len(browser.sent) == 6 and all(path == "/auth/login" for path, _ in browser.sent)
    assert "LOCKOUT PASS" in capsys.readouterr().out
    assert smoke_deployed.locked_out(Scripted(five + [(429, {"code": "login_limit"}, {})]), "e") is False
    assert smoke_deployed.locked_out(Scripted(five + [(401, {}, {})]), "e") is False
    assert smoke_deployed.locked_out(Scripted(five[:4] + [sixth, sixth]), "e") is False


def test_the_lockout_uses_a_wrong_password_of_its_own(capsys) -> None:
    browser = Scripted([(401, {}, {})] * 5 + [(429, {"code": "login_limit"}, {"retry-after": "900"})])
    smoke_deployed.locked_out(browser, "e")
    passwords = {body["password"] for _, body in browser.sent}
    assert len(passwords) == 1
    assert next(iter(passwords)) not in capsys.readouterr().out
