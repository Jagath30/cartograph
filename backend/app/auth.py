"""Who is asking, and whether the request may change anything (step 11,
criterion 12, NFR-10, T-08).

THE CREDENTIAL IS AN OPAQUE TOKEN IN A COOKIE: HttpOnly, SameSite=Lax,
Path=/, no Domain, Secure from a setting, and Max-Age the session's seven
days. One origin (the frontend's rewrite of /api/v1), so the cookie is
first-party. The store keeps only the token's sha256 (app.shell.accounts).

`current_user` is the dependency a route takes to need a session. No
session, or one expired or revoked, is a 401; the route never runs.

THE REQUEST GUARD runs before every route, on every request that may
change something (any method but GET, HEAD and OPTIONS):
  - an Origin header that is present and not one of FRONTEND_ORIGINS is
    refused, 403, before the route is reached;
  - a request that carries a body must declare it application/json, or it
    is refused, 415. A form or text/plain body is what a page on another
    site can send without a preflight. Logout and the rerun carry no body
    and are not refused for it; the Origin check still applies to them.

Nothing here logs: not a header, not a token, not a password.
"""

from datetime import UTC, datetime
from functools import lru_cache
from typing import Annotated

from fastapi import Depends, Request, Response
from fastapi.responses import JSONResponse

from app.api_models import ErrorOut, Refused
from app.config import Settings, get_settings
from app.core.credentials import SESSION_LIFETIME
from app.shell.accounts import Accounts, SignedIn

COOKIE = "cartograph_session"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


@lru_cache
def get_accounts() -> Accounts:
    return Accounts(get_settings().app_database_url)


def now() -> datetime:
    """The moment a session is judged at. A dependency, so a test can
    stand anywhere in a session's seven days."""
    return datetime.now(UTC)


AccountsDep = Annotated[Accounts, Depends(get_accounts)]
NowDep = Annotated[datetime, Depends(now)]


def current_user(request: Request, accounts: AccountsDep, at: NowDep) -> SignedIn:
    token = request.cookies.get(COOKIE)
    signed = accounts.signed_in(token, at) if token else None
    if signed is None:
        raise Refused(401, "not_signed_in", "Sign in to do this.")
    return signed


UserDep = Annotated[SignedIn, Depends(current_user)]


def set_session(response: Response, token: str, settings: Settings) -> None:
    response.set_cookie(
        COOKIE, token, max_age=int(SESSION_LIFETIME.total_seconds()), path="/",
        secure=settings.session_cookie_secure, httponly=True, samesite="lax",
    )  # fmt: skip


def clear_session(response: Response, settings: Settings) -> None:
    response.delete_cookie(COOKIE, path="/", secure=settings.session_cookie_secure, httponly=True, samesite="lax")


class RequestGuard:
    """Pure ASGI, so that a refusal is made before a body is read."""

    def __init__(self, app, origins: list[str]) -> None:
        self.app = app
        self.origins = frozenset(origins)

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] == "http" and scope["method"] not in SAFE_METHODS:
            refusal = self.refusal(dict(scope["headers"]))
            if refusal is not None:
                status, code, message = refusal
                body = ErrorOut(code=code, message=message).model_dump(mode="json", exclude_none=True)
                await JSONResponse(status_code=status, content=body)(scope, receive, send)
                return
        await self.app(scope, receive, send)

    def refusal(self, headers: dict[bytes, bytes]) -> tuple[int, str, str] | None:
        origin = headers.get(b"origin")
        if origin is not None and origin.decode("latin-1") not in self.origins:
            return 403, "origin_refused", "This request came from a site this server does not serve."
        length = headers.get(b"content-length")
        carries = (length is not None and length.strip() != b"0") or b"transfer-encoding" in headers
        if carries:
            kind = headers.get(b"content-type", b"").split(b";")[0].strip().lower()
            if kind != b"application/json":
                return 415, "json_only", "This endpoint takes a JSON body."
        return None
