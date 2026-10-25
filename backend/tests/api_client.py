"""The application under test, with the pipeline behind it scripted: the
real orchestrator, a scripted model, a stand-in executor, no network.
Shared by the API's tests."""

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from memory_stores import Memory, MemoryAccounts
from tracing import CONTEXT, located, orchestrator

from app.api import get_service
from app.auth import current_user, get_accounts, now
from app.config import Settings
from app.main import create_app
from app.orchestrator import Keep, Orchestrator
from app.shell.accounts import SignedIn
from app.shell.query_service import QueryService
from app.shell.snapshot_store import NoCurrentSnapshot, StoredSnapshot

ADDRESS = ("catalog_sales", "customer_address")
URLS = dict(
    app_database_url="postgresql://unused/unused", warehouse_database_url="postgresql://unused/unused", redis_url="redis://unused"
)
SETTINGS = Settings(**URLS)
# What a signed-in request is, where a test is not about signing in.
ADA = SignedIn(1, "ada@example.com")


class NoSchema:
    def load_current(self):
        raise NoCurrentSnapshot("the application store holds no schema snapshot: nothing has been ingested")


class Snapshots:
    """The schema as stored: the one the scripted pipeline answers over,
    unless a test names another."""

    def __init__(self, snapshot=None) -> None:
        from tracing import SNAPSHOT

        self.snapshot = snapshot or SNAPSHOT

    def load_current(self):
        return StoredSnapshot(7, "abc123", "test", None), self.snapshot


class Ticking:
    """A clock for Keep: each question a minute after the one before."""

    def __init__(self, start: datetime = datetime(2026, 10, 25, 9, 0, tzinfo=UTC)) -> None:
        self.at = start

    def __call__(self) -> datetime:
        self.at += timedelta(minutes=1)
        return self.at


def service(script, anchors=ADDRESS, executor=None, traces=None, snapshots=None, opened=None, clock=None,
            rerun_executor=None, **tree_arguments) -> QueryService:  # fmt: skip
    traces = traces if traces is not None else Memory()

    def open_orchestrator() -> Orchestrator:
        if opened is not None:
            opened.append(1)
        keep = Keep(traces, CONTEXT, user_id=1, now=clock or Ticking())
        return orchestrator(script, located(*anchors, **tree_arguments), executor, keep=keep)

    return QueryService(open_orchestrator, traces, snapshots or NoSchema(), rerun_executor)


def client(made: QueryService, user: SignedIn | None = ADA) -> TestClient:
    """Signed in as `user` without going through the door; the tests of
    the door itself use `served`."""
    app = create_app(SETTINGS)
    app.dependency_overrides[get_service] = lambda: made
    if user is not None:
        app.dependency_overrides[current_user] = lambda: user
    return TestClient(app, raise_server_exceptions=False)


def served(made: QueryService | None = None, accounts=None, secure: bool = False, at=None, **settings) -> TestClient:
    """The real door: sessions from `accounts`, the clock from `at` (a
    one-item list, so that a test can move it)."""
    app = create_app(Settings(**URLS, session_cookie_secure=secure, **settings))
    app.dependency_overrides[get_service] = lambda: made or service([])
    app.dependency_overrides[get_accounts] = lambda: accounts if accounts is not None else MemoryAccounts()
    if at is not None:
        app.dependency_overrides[now] = lambda: at[0]
    return TestClient(app, raise_server_exceptions=False)


def ask(made: QueryService, question: str = "Where are catalogue buyers billed?"):
    return client(made).post("/api/v1/queries", json={"question": question})
