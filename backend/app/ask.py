"""One question through the whole pipeline: retrieval, the join tree, the
model, validation, conformance, execution. The first real answer.

    docker compose exec backend python -m app.ask "Which catalog pages brought in the most orders?"
    docker compose exec backend python -m app.ask "..." --json

Prints the result and the trace the orchestrator assembled: the tables the
model was shown, every attempt with its findings, the SQL that ran, the
joins it really made beside the joins selected, and the rows.

THIS IS A PAID CALL: the model, and the embedding of a question that is
not in the cache. Each model call is written to the spend ledger, and the
last line says what this run cost and the ledger's total. It needs
OPENAI_API_KEY; without it the command says what to set and exits 3.

Nothing is stored: persisting the trace is step 8.

No logic lives here: open the pipeline (shell), answer (orchestrator),
print.
"""

import argparse
import json
import logging
import sys

from app.answer_report import print_trace, summary
from app.shell.embedder import EmbeddingKeyMissing
from app.shell.model_client import ModelKeyMissing
from app.shell.pipeline_session import CEILING_USD, open_pipeline

NO_KEY_EXIT = 3


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="One question through the whole pipeline.")
    parser.add_argument("question")
    parser.add_argument("--json", action="store_true", help="print the trace's summary as JSON and nothing else")
    parser.add_argument("--purpose", default="ask", help="written beside the call in the spend ledger")
    arguments = parser.parse_args(argv)
    if not arguments.question.strip():
        parser.exit(2, "ask: the question is empty\n")

    logging.basicConfig(level=logging.WARNING if arguments.json else logging.INFO, format="%(message)s", stream=sys.stderr)
    logging.getLogger("httpx").setLevel(logging.WARNING)

    try:
        pipeline = open_pipeline("ask", arguments.purpose)
        trace = pipeline.orchestrator.answer(arguments.question)
    except (ModelKeyMissing, EmbeddingKeyMissing) as missing:
        print(f"ask: {missing}", file=sys.stderr)
        return NO_KEY_EXIT

    if arguments.json:
        print(json.dumps(summary(trace)))
        return 0
    print_trace(trace)
    embedded = pipeline.retrieval.paid
    print(f"    {'embedding':<11}{embedded.texts} texts embedded now, ${embedded.cost_usd:.8f}")
    print(f"    {'ledger':<11}{pipeline.ledger.calls()} model calls in all, ${pipeline.ledger.total():.6f}; "
          f"ceiling ${CEILING_USD:.2f}")  # fmt: skip
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
