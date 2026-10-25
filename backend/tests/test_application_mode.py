"""The application runs chosen (step 10b, v2.1 adopted at stop E; the plan
approved at stop F1): the API, `ask` and the smoke check open the pipeline
in that mode, and the evaluation's runners name their own. No pipeline is
opened here: each command's open_pipeline is replaced by one that records
how it was asked and stops."""

from types import SimpleNamespace

import pytest

from app.shell import pipeline_session
from app.shell.model_client import ModelKeyMissing


def test_the_application_mode_is_chosen() -> None:
    assert pipeline_session.APPLICATION_MODE == "chosen"


def _recording(asked: list):
    def open_pipeline(*arguments, **keywords):
        asked.append(keywords.get("mode", "retrieval"))
        raise ModelKeyMissing("no key here")

    return open_pipeline


def test_the_api_opens_the_application_mode(monkeypatch) -> None:
    from app.shell import query_service

    asked: list = []
    monkeypatch.setattr(pipeline_session, "open_pipeline", _recording(asked))
    service = query_service.make_service("postgresql://nowhere/none", "postgresql://nowhere/none")
    with pytest.raises(query_service.NotReady):
        service.ask("A question?", 1)
    assert asked == ["chosen"]


def test_ask_opens_the_application_mode(monkeypatch) -> None:
    from app import ask

    asked: list = []
    monkeypatch.setattr(ask, "open_pipeline", _recording(asked))
    monkeypatch.setattr(ask, "get_settings", lambda: SimpleNamespace(app_database_url="postgresql://nowhere/none"))
    monkeypatch.setattr(ask, "TraceStore", lambda url: None)
    assert ask.main(["A question?"]) == ask.NO_KEY_EXIT
    assert asked == ["chosen"]


def test_the_smoke_check_opens_the_application_mode(monkeypatch) -> None:
    from app import smoke_pipeline

    asked: list = []
    monkeypatch.setattr(smoke_pipeline, "open_pipeline", _recording(asked))
    assert smoke_pipeline.main() == smoke_pipeline.NO_KEY_EXIT
    assert asked == ["chosen"]
