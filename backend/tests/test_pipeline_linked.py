"""The "linked" pipeline mode (step 10b), opened for real against both
databases, with scripted models and an embedder that fails if called:
nothing is paid. d4's vectors come from the cache."""

import os

import pytest

from app.core.linking import LINK_MAX_OUTPUT_TOKENS, LINK_REPLY_NAME, LINK_RULES
from app.shell.model_client import FakeModel

D4 = "How do purchases on the web split by the buyer's level of education?"
SQL = ("SELECT cd.cd_education_status, COUNT(*) FROM web_sales ws JOIN customer_demographics cd "
       "ON ws.ws_bill_cdemo_sk = cd.cd_demo_sk GROUP BY cd.cd_education_status")  # fmt: skip


class _NeverCalled:
    model = "text-embedding-3-small"

    def embed(self, texts):
        raise AssertionError(f"not in the vector cache: {texts}")


@pytest.fixture
def opened(monkeypatch):
    if not (os.environ.get("APP_DATABASE_URL") and os.environ.get("WAREHOUSE_DATABASE_URL")):
        pytest.skip("needs the application database and the warehouse")
    import json

    from app.shell import pipeline_session, retrieval_session

    made: list[tuple[int | None, FakeModel]] = []
    scripts = [[json.dumps({"status": "sql", "sql": SQL})], [json.dumps({"tables": ["web_sales", "customer_demographics"]})]]

    def make_model(settings, max_output_tokens=None):
        model = FakeModel(scripts[len(made)])
        made.append((max_output_tokens, model))
        return model

    monkeypatch.setattr(pipeline_session, "make_model", make_model)
    monkeypatch.setattr(retrieval_session, "make_embedder", lambda settings: _NeverCalled())
    wrapped: list[object] = []
    pipeline = pipeline_session.open_pipeline(
        "test", "test linked", mode="linked", wrap_model=lambda model: wrapped.append(model) or model
    )
    return pipeline, made, wrapped


def test_the_linked_mode_links_then_answers_through_the_frozen_tree(opened) -> None:
    pipeline, made, wrapped = opened
    assert pipeline.mode == "linked"
    trace = pipeline.orchestrator.answer(D4)
    assert trace.outcome == "answered", trace.message
    assert trace.linking.tables == ("web_sales", "customer_demographics")
    assert set(trace.located.tree.tables) >= {"web_sales", "customer_demographics"}
    assert trace.located.linked == ("web_sales", "customer_demographics")
    assert trace.located.retrieval is not None
    # From the located tree on, v1: the frozen builder's prompt of the
    # tree's subgraph and joins, not the whole schema.
    from app.core.prompt_builder import build_prompt

    tree = trace.located.tree
    assert trace.prompt == build_prompt(D4, pipeline.snapshot, tree.subgraph, tree.joins)
    assert trace.prompt.has_join_section and len(trace.prompt.tables) < 24


def test_the_linking_call_has_its_own_cap_prompt_and_shape(opened) -> None:
    pipeline, made, wrapped = opened
    pipeline.orchestrator.answer(D4)
    caps = [cap for cap, _ in made]
    assert caps == [None, LINK_MAX_OUTPUT_TOKENS]
    linking_model = made[1][1]
    [(messages, shape)] = linking_model.calls
    assert messages[0].content == LINK_RULES and messages[1].content.startswith("Tables:\n\n")
    assert shape.name == LINK_REPLY_NAME
    assert len(shape.schema["properties"]["tables"]["items"]["enum"]) == 24
    # Both models are wrapped, each on the ledger, generation's first, so a
    # harness sees both.
    assert [model._inner for model in wrapped] == [made[0][1], made[1][1]]
