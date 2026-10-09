"""The development questions of step 7 are their own file, and are not the
evaluation set under another name."""

from pathlib import Path

import yaml

EVAL = Path(__file__).resolve().parents[2] / "eval"


def _words(text: str) -> set[str]:
    return {word.strip("?,.").lower() for word in text.split()} - {""}


def test_there_are_eight_with_unique_ids_and_nothing_but_a_question_and_a_purpose() -> None:
    questions = yaml.safe_load((EVAL / "dev_questions.yaml").read_text())["questions"]
    assert [entry["id"] for entry in questions] == [f"d{number}" for number in range(1, 9)]
    for entry in questions:
        # No expected tables, joins or answers: they are not a ground truth.
        assert set(entry) == {"id", "question", "exercises"}
        assert entry["question"].strip().endswith("?") and entry["exercises"].strip()


def test_no_development_question_is_an_evaluation_question() -> None:
    """Exact repeats, and near ones: no development question shares more
    than half its words with an evaluation question. A paraphrase in other
    words is the owner's to catch by reading; this catches the lazy kind."""
    development = yaml.safe_load((EVAL / "dev_questions.yaml").read_text())["questions"]
    evaluation = yaml.safe_load((EVAL / "questions.yaml").read_text())["questions"]
    assert len(evaluation) == 16
    for ours in development:
        for theirs in evaluation:
            a, b = _words(ours["question"]), _words(theirs["question"])
            assert len(a & b) <= len(a) / 2, (ours["question"], theirs["question"])
