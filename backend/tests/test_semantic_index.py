"""The semantic index, in a scratch database with the fake embedder (FR-06,
FR-07, DR-11, DD-09; ruling h).

No key and no network: the fake embedder turns words into vectors, which
is enough to see that the right rows are written and the right numbers
come back. Postgres's full-text search is the real one. Nothing here says
anything about how well retrieval works; only the evaluation does that.
"""

from dataclasses import replace

import psycopg
import pytest
from samples import SMALL

from app.core.retriever import Term, Token
from app.core.snapshot import Table
from app.shell.embedder import DIMENSIONS, Embedded, FakeEmbedder
from app.shell.semantic_index import EmbeddingModelMismatch, SchemaNotEmbedded, SemanticIndex
from app.shell.snapshot_store import SnapshotStore
from app.shell.vector_cache import CachedEmbedder

ELEMENTS = len(SMALL.tables) + len(SMALL.columns)


class OtherModel(FakeEmbedder):
    model = "another-model"


@pytest.fixture
def stored(scratch_database):
    return SnapshotStore(scratch_database).save(SMALL, "test")


@pytest.fixture
def index(scratch_database, stored):
    index = SemanticIndex(scratch_database, FakeEmbedder())
    index.embed_schema(stored.id)
    return index


def _by_element(scores) -> dict[str, tuple[float, float]]:
    return {score.element: (score.semantic, score.keyword) for score in scores}


# --------------------------------------------------------------------------
# Embedded once per snapshot (DR-11)
# --------------------------------------------------------------------------


def test_every_element_is_embedded_with_its_search_text(scratch_database, stored) -> None:
    embedder = FakeEmbedder()
    report = SemanticIndex(scratch_database, embedder).embed_schema(stored.id)

    assert (report.embedded, report.already, report.model) == (ELEMENTS, 0, "fake-bag-of-words")
    assert len(embedder.calls) == 1 and len(embedder.calls[0]) == ELEMENTS
    assert "return. Table return. Holds: amount." in embedder.calls[0]
    assert "shop — region. Text column in table shop." in embedder.calls[0]

    with psycopg.connect(scratch_database) as connection:
        assert connection.execute(
            "select count(*), count(embedding), min(vector_dims(embedding)) from schema_elements"
        ).fetchone() == (ELEMENTS, ELEMENTS, DIMENSIONS)
        assert connection.execute("select embedding_model from schema_snapshots").fetchone() == ("fake-bag-of-words",)


def test_a_second_run_embeds_nothing_and_calls_nobody(scratch_database, stored) -> None:
    SemanticIndex(scratch_database, FakeEmbedder()).embed_schema(stored.id)

    again = FakeEmbedder()
    report = SemanticIndex(scratch_database, again).embed_schema(stored.id)
    assert (report.embedded, report.already, report.tokens, report.cost_usd) == (0, ELEMENTS, 0, 0.0)
    assert again.calls == []


def test_only_the_elements_without_a_vector_are_embedded(scratch_database, stored) -> None:
    SemanticIndex(scratch_database, FakeEmbedder()).embed_schema(stored.id)
    with psycopg.connect(scratch_database) as connection:
        connection.execute("update schema_elements set embedding = null where table_name = 'shop'")

    embedder = FakeEmbedder()
    report = SemanticIndex(scratch_database, embedder).embed_schema(stored.id)
    assert (report.embedded, report.already) == (3, ELEMENTS - 3)
    assert len(embedder.calls[0]) == 3


def test_a_snapshot_is_never_topped_up_by_a_second_model(scratch_database, stored) -> None:
    SemanticIndex(scratch_database, FakeEmbedder()).embed_schema(stored.id)
    with psycopg.connect(scratch_database) as connection:
        connection.execute("update schema_elements set embedding = null where table_name = 'shop'")

    other = OtherModel()
    with pytest.raises(EmbeddingModelMismatch, match="was embedded with fake-bag-of-words"):
        SemanticIndex(scratch_database, other).embed_schema(stored.id)
    assert other.calls == []


# --------------------------------------------------------------------------
# Scoring: two raw numbers for every element
# --------------------------------------------------------------------------


def test_every_element_is_scored_once_in_stored_order(index) -> None:
    scores = index.score("region of the shop")
    assert len(scores) == ELEMENTS
    assert [score.element for score in scores][:3] == ["sale", "sale.item", "sale.ticket"]
    assert {score.element for score in scores} >= {"shop", "shop.region", "return.amount"}


def test_a_word_that_occurs_literally_gets_a_keyword_score_and_others_get_none(index) -> None:
    scores = _by_element(index.score("region"))
    assert scores["shop.region"][1] > 0
    assert scores["shop"][1] > 0  # "Holds: region."
    assert scores["sale.item"][1] == 0 and scores["return.amount"][1] == 0


def test_the_keyword_search_stems(index) -> None:
    """ "regions" finds "region": Postgres reduces both to one lexeme. The
    fake embedder does not, so this is the keyword half alone."""
    scores = _by_element(index.score("regions"))
    assert scores["shop.region"][1] > 0
    assert scores["shop.region"][0] == pytest.approx(0.0, abs=1e-6)


def test_the_words_of_a_question_are_joined_by_or_not_and(index) -> None:
    """No description holds both "region" and "amount". Joined by AND,
    nothing would match at all."""
    scores = _by_element(index.score("region amount"))
    assert scores["shop.region"][1] > 0 and scores["return.amount"][1] > 0
    assert scores["sale.item"][1] == 0


def test_the_semantic_score_is_highest_where_the_most_words_are_shared(index) -> None:
    scores = index.score("numeric amount column")
    best = max(scores, key=lambda score: score.semantic)
    assert best.element == "return.amount"
    assert _by_element(scores)["shop.region"][0] < best.semantic
    assert all(-1.0 <= score.semantic <= 1.0 + 1e-6 for score in scores)


def test_only_the_current_snapshot_is_scored(scratch_database, index) -> None:
    """An older snapshot stays in the store (DD-17) and must not be searched.
    (With the snapshot filter removed, every other test here passed: none
    of them had two snapshots.)"""
    newer = replace(SMALL, tables=SMALL.tables + (Table("depot", None, "depot", "depot. Table depot."),))
    stored = SnapshotStore(scratch_database).save(newer, "test")
    index.embed_schema(stored.id)

    scores = index.score("region")
    assert len(scores) == ELEMENTS + 1
    assert [score.element for score in scores].count("shop.region") == 1


def test_a_text_of_stopwords_alone_matches_nothing_and_does_not_fail(index) -> None:
    scores = index.score("what is the of")
    assert len(scores) == ELEMENTS
    assert all(score.keyword == 0 for score in scores)


def test_text_that_would_break_a_query_is_only_text(index) -> None:
    for text in ("it's & (broken | ! query':*", "'; drop table schema_elements; --", ""):
        assert len(index.score(text)) == ELEMENTS


def test_scoring_before_embedding_says_what_to_run(scratch_database, stored) -> None:
    with pytest.raises(SchemaNotEmbedded, match="python -m app.ingest_schema"):
        SemanticIndex(scratch_database, FakeEmbedder()).score("region")


def test_scoring_with_nothing_stored_says_what_to_run(scratch_database) -> None:
    with pytest.raises(SchemaNotEmbedded, match="no schema snapshot is stored"):
        SemanticIndex(scratch_database, FakeEmbedder()).score("region")


def test_a_question_is_never_scored_against_another_models_vectors(scratch_database, index) -> None:
    with pytest.raises(EmbeddingModelMismatch, match="mean nothing"):
        SemanticIndex(scratch_database, OtherModel()).score("region")


# --------------------------------------------------------------------------
# Words (ruling h): Postgres's own stopword list
# --------------------------------------------------------------------------


def test_stopwords_are_marked_by_postgres_and_punctuation_is_kept_as_a_break(index) -> None:
    assert index.tokens("Which web site sells most, and by which method?") == (
        Token("Which", False),
        Token("web", True),
        Token("site", True),
        Token("sells", True),
        Token("most", False),
        Token(",", False),
        Token("and", False),
        Token("by", False),
        Token("which", False),
        Token("method", True),
        Token("?", False),
    )


def test_a_hyphenated_word_is_its_parts_and_the_hyphen_does_not_separate_them(index) -> None:
    assert index.tokens("top-selling items in 2002") == (
        Token("top", True),
        Token("selling", True),
        Token("items", True),
        Token("in", False),
        Token("2002", True),
    )


def test_a_question_is_scored_whole_and_term_by_term_with_one_embedder_call(scratch_database, stored) -> None:
    embedder = FakeEmbedder()
    index = SemanticIndex(scratch_database, embedder)
    index.embed_schema(stored.id)
    embedder.calls.clear()

    scored = index.score_question("What is the shop region of each return?")

    assert [term for term, _ in scored.terms] == [
        Term("shop", "word"),
        Term("region", "word"),
        Term("return", "word"),
        Term("shop region", "bigram"),
    ]
    assert embedder.calls == [["What is the shop region of each return?", "shop", "region", "return", "shop region"]]
    assert len(scored.scores) == ELEMENTS and all(len(scores) == ELEMENTS for _, scores in scored.terms)

    region = _by_element(dict(scored.terms)[Term("region", "word")])
    assert region["shop.region"][1] > 0 and region["return.amount"][1] == 0


# --------------------------------------------------------------------------
# The vector cache
# --------------------------------------------------------------------------


def test_a_vector_is_embedded_once_and_then_read_from_the_cache(tmp_path) -> None:
    inner = FakeEmbedder()
    cached = CachedEmbedder(inner, tmp_path / "vectors")

    first = cached.embed(["shop region", "return amount"])
    assert inner.calls == [["shop region", "return amount"]]
    assert first.tokens == 4

    again_inner = FakeEmbedder()
    again = CachedEmbedder(again_inner, tmp_path / "vectors").embed(["return amount", "shop region"])
    assert again_inner.calls == []
    assert (again.tokens, again.cost_usd) == (0, 0.0)
    # The same bytes, in the order asked.
    assert again.vectors == (first.vectors[1], first.vectors[0])


def test_only_what_is_missing_is_embedded_and_a_repeat_is_embedded_once(tmp_path) -> None:
    CachedEmbedder(FakeEmbedder(), tmp_path).embed(["shop region"])
    inner = FakeEmbedder()
    result = CachedEmbedder(inner, tmp_path).embed(["shop region", "sale item", "sale item"])

    assert inner.calls == [["sale item"]]
    assert len(result.vectors) == 3 and result.vectors[1] == result.vectors[2]
    assert all(len(vector) == DIMENSIONS for vector in result.vectors)


def test_the_cache_is_per_model(tmp_path) -> None:
    CachedEmbedder(FakeEmbedder(), tmp_path).embed(["shop region"])
    other = OtherModel()
    CachedEmbedder(other, tmp_path).embed(["shop region"])
    assert other.calls == [["shop region"]]


def test_a_damaged_cache_file_is_embedded_again_not_trusted(tmp_path) -> None:
    CachedEmbedder(FakeEmbedder(), tmp_path).embed(["shop region"])
    (file,) = tmp_path.iterdir()
    file.write_bytes(b"\x00\x00\x00\x00")

    inner = FakeEmbedder()
    result = CachedEmbedder(inner, tmp_path).embed(["shop region"])
    assert inner.calls == [["shop region"]]
    assert len(result.vectors[0]) == DIMENSIONS


def test_what_is_returned_is_what_a_later_run_will_read(tmp_path) -> None:
    """Stored as 32-bit floats. The first call returns the stored values,
    not the embedder's 64-bit ones, so two runs never differ in the last
    digits."""

    class Precise(FakeEmbedder):
        def embed(self, texts):
            return Embedded(tuple((0.1,) * DIMENSIONS for _ in texts), 1, 0.0)

    first = CachedEmbedder(Precise(), tmp_path).embed(["x"])
    second = CachedEmbedder(Precise(), tmp_path).embed(["x"])
    assert first.vectors == second.vectors
    assert first.vectors[0][0] != 0.1
