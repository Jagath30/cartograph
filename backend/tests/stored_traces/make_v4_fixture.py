"""Writes the frontend's version 4 fixture (step 14): an answer served
from the answer cache, made by app.core.answer_cache from the version 3
catalog fixture, offline.

    docker run --rm --network none -v "$PWD/backend:/app" -v "$PWD/frontend/fixtures:/fixtures:ro" -w /app \
      cartograph-backend python tests/stored_traces/make_v4_fixture.py > frontend/fixtures/trace-v4-served-from-cache.json
"""

import json, sys, uuid
from datetime import UTC, datetime
sys.path[:0] = ["/app"]
from app.core.answer_cache import entry, read_entry, served
from app.core.trace_document import TraceDocument
first = TraceDocument.model_validate(json.load(open("/fixtures/trace-v3-catalog-customers.json")))
cached = read_entry(entry(first, [["CA", 41]]))
hit = served(cached, uuid.UUID("00000000-0000-4000-8000-000000000002"), 2, first.question,
             datetime(2026, 11, 2, 9, 0, tzinfo=UTC), 7)
print(json.dumps(hit.model_dump(mode="json"), indent=2, ensure_ascii=False))
