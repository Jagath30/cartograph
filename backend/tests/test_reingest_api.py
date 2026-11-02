"""POST /api/v1/admin/reingest (IR-07, FR-05; step 14, the owner's ruling
2): there only for ADMIN_TOKEN as a bearer token; once in 10 minutes, and
never without Redis; the live schema's hash compared with the
calibration's; nothing stored, and no model or embedder called, on either
path. The token here is an obviously fake one."""

import json

import pytest
from api_client import URLS, served, service
from fake_redis import DownRedis, FakeRedis
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app import auth
from app.api import get_counters, get_schema_reader, get_service
from app.config import Settings
from app.main import create_app
from app.shell import reingest as reingest_module
from app.shell.counters import Counters
from app.shell.query_service import QueryService
from app.shell.reingest import SchemaRead

FAKE_TOKEN = "x" * 32
CALIBRATED = "c" * 64
PATH = "/api/v1/admin/reingest"


class Untouchable:
    """Stands for the model and the embedder: any call fails the test."""

    def __getattr__(self, name):
        raise AssertionError(f"{name} was called: re-ingest calls no model and no embedder")


class Service:
    """What the endpoint uses of the query service, counting reopens."""

    def __init__(self, ready: bool = True) -> None:
        self.ready = ready
        self.reopened = 0
        self.model = Untouchable()
        self.embedder = Untouchable()

    def reopen_if_not_ready(self) -> bool:
        if self.ready:
            return False
        self.reopened += 1
        self.ready = True
        return True


class Reader:
    def __init__(self, live: str = CALIBRATED) -> None:
        self.live = live
        self.reads = 0

    def __call__(self) -> SchemaRead:
        self.reads += 1
        return SchemaRead(self.live, CALIBRATED)


def web(token: str | None = FAKE_TOKEN, redis=None, reader=None, made=None) -> tuple[TestClient, Reader, Service]:
    settings = Settings(**URLS, admin_token=token)
    app = create_app(settings)
    reader = reader or Reader()
    made = made or Service()
    counters = Counters(redis if redis is not None else FakeRedis())
    app.dependency_overrides[get_counters] = lambda: counters
    app.dependency_overrides[get_schema_reader] = lambda: reader
    app.dependency_overrides[get_service] = lambda: made
    from app.config import get_settings

    app.dependency_overrides[get_settings] = lambda: settings
    return TestClient(app, raise_server_exceptions=False), reader, made


def bearer(token: str = FAKE_TOKEN) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def never_routed(client: TestClient):
    return client.post("/api/v1/admin/no-such-route")


# --------------------------------------------------------------------------
# 404 unless the token is set, long enough, and sent
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "token, headers",
    [
        (None, bearer()),  # none set
        (None, {}),
        ("x" * 31, bearer("x" * 31)),  # set, and too short
        (FAKE_TOKEN, {}),  # no token sent
        (FAKE_TOKEN, bearer("y" * 32)),  # a wrong one
        (FAKE_TOKEN, bearer(FAKE_TOKEN[:-1])),
        (FAKE_TOKEN, {"Authorization": FAKE_TOKEN}),  # not as a bearer token
        (FAKE_TOKEN, {"Authorization": f"Basic {FAKE_TOKEN}"}),
    ],
)
def test_without_the_right_token_it_is_404_as_a_path_never_routed(token, headers) -> None:
    client, reader, made = web(token)
    response = client.post(PATH, headers=headers)
    unknown = never_routed(client)
    assert response.status_code == unknown.status_code == 404
    assert response.json() == unknown.json()
    assert reader.reads == 0 and made.reopened == 0


def test_it_is_not_in_the_published_schema() -> None:
    assert PATH not in create_app(Settings(**URLS, admin_token=FAKE_TOKEN)).openapi()["paths"]


def test_the_token_is_compared_in_constant_time(monkeypatch) -> None:
    compared: list = []
    real = auth.hmac.compare_digest
    monkeypatch.setattr(auth.hmac, "compare_digest", lambda a, b: compared.append(1) or real(a, b))
    client, _, _ = web()
    assert client.post(PATH, headers=bearer()).status_code == 200
    assert compared == [1]


def test_the_token_is_never_logged(caplog) -> None:
    client, _, _ = web()
    with caplog.at_level("DEBUG"):
        client.post(PATH, headers=bearer())
        client.post(PATH, headers=bearer("y" * 32))
    assert FAKE_TOKEN not in caplog.text and "y" * 32 not in caplog.text


# --------------------------------------------------------------------------
# The same hash, and a different one
# --------------------------------------------------------------------------


def test_the_same_hash_is_unchanged_and_an_open_pipeline_is_left_as_it_is() -> None:
    client, reader, made = web()
    response = client.post(PATH, headers=bearer())
    assert response.status_code == 200
    assert response.json() == {"status": "unchanged", "hash": CALIBRATED, "pipeline_reopened": False}
    assert reader.reads == 1 and made.reopened == 0


def test_the_same_hash_reopens_a_pipeline_that_is_not_open() -> None:
    client, _, made = web(made=Service(ready=False))
    response = client.post(PATH, headers=bearer())
    assert response.status_code == 200 and response.json()["pipeline_reopened"] is True
    assert made.reopened == 1


def test_a_different_hash_is_409_and_nothing_is_reopened() -> None:
    client, reader, made = web(reader=Reader(live="d" * 64), made=Service(ready=False))
    response = client.post(PATH, headers=bearer())
    assert response.status_code == 409
    assert response.json()["code"] == "schema_changed"
    assert "needs a new calibration first, an offline step" in response.json()["message"]
    assert "Nothing was stored" in response.json()["message"]
    assert reader.reads == 1 and made.reopened == 0


@pytest.mark.parametrize("live", [CALIBRATED, "d" * 64])
def test_on_either_path_nothing_is_stored_and_no_model_or_embedder_is_called(monkeypatch, live) -> None:
    """The real read, over a scripted catalog: the snapshot store, the model
    and the embedder each fail the test if they are reached."""
    from app.shell import embedder, model_client, snapshot_store
    from tracing import SNAPSHOT

    def refuse(*args, **kwargs):
        raise AssertionError("re-ingest reached something it must never reach")

    monkeypatch.setattr(snapshot_store.SnapshotStore, "save", refuse)
    monkeypatch.setattr(model_client, "make_model", refuse)
    monkeypatch.setattr(embedder, "make_embedder", refuse)
    monkeypatch.setattr(reingest_module.SchemaIngestor, "ingest", lambda self: SNAPSHOT)
    calibration = {"snapshot_sha256": reingest_module.snapshot_hash(SNAPSHOT) if live == CALIBRATED else live}

    def read():
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "calibration.json"
            path.write_text(json.dumps(calibration))
            return reingest_module.read_schema(Settings(**URLS), path)

    made = QueryService(refuse, None, None)
    made._orchestrator = Untouchable()  # open: the reopen is not tried
    client, _, _ = web(reader=read, made=made)
    response = client.post(PATH, headers=bearer())
    if live == CALIBRATED:
        assert response.status_code == 200 and response.json()["status"] == "unchanged"
    else:
        assert response.status_code == 409


# --------------------------------------------------------------------------
# Once in 10 minutes; never without Redis
# --------------------------------------------------------------------------


def test_a_second_call_within_10_minutes_is_refused_with_retry_after() -> None:
    redis = FakeRedis()
    client, reader, _ = web(redis=redis)
    assert client.post(PATH, headers=bearer()).status_code == 200
    redis.advance(599)
    refused = client.post(PATH, headers=bearer())
    assert refused.status_code == 429 and refused.json()["code"] == "reingest_limit"
    assert refused.headers["Retry-After"] == "1"
    assert reader.reads == 1
    redis.advance(1)
    assert client.post(PATH, headers=bearer()).status_code == 200


def test_a_refused_schema_still_takes_the_10_minutes() -> None:
    redis = FakeRedis()
    client, _, _ = web(redis=redis, reader=Reader(live="d" * 64))
    assert client.post(PATH, headers=bearer()).status_code == 409
    assert client.post(PATH, headers=bearer()).status_code == 429


def test_without_redis_it_refuses_and_reads_nothing() -> None:
    client, reader, made = web(redis=DownRedis(), made=Service(ready=False))
    response = client.post(PATH, headers=bearer())
    assert response.status_code == 503 and response.json()["code"] == "not_ready"
    assert reader.reads == 0 and made.reopened == 0


def test_without_the_token_redis_is_never_touched() -> None:
    redis = FakeRedis()
    client, _, _ = web(token=None, redis=redis)
    client.post(PATH, headers=bearer())
    assert redis.keys_held() == []


# --------------------------------------------------------------------------
# The real service's reopen
# --------------------------------------------------------------------------


def test_the_service_reopens_only_when_not_open() -> None:
    opened: list = []
    made = service([], opened=opened)
    assert made.reopen_if_not_ready() is True and opened == [1]
    assert made.reopen_if_not_ready() is False and opened == [1]


def test_the_setting_is_a_secret() -> None:
    settings = Settings(**URLS, admin_token=FAKE_TOKEN)
    assert isinstance(settings.admin_token, SecretStr)
    assert FAKE_TOKEN not in repr(settings) and FAKE_TOKEN not in str(settings.model_dump())


def test_served_has_no_admin_route_by_default() -> None:
    assert served().post(PATH, headers=bearer()).status_code == 404
