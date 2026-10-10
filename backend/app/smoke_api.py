"""The smoke check's lines for the API (NFR-27; IR-02, IR-03, IR-06,
FR-27, NFR-13).

    docker compose exec backend python -m app.smoke_api

Asks the running server, over HTTP, as a browser would. Each check prints
one line that scripts/smoke.sh reads:

  SCHEMA     GET /schema returns the stored schema as nodes and edges.
  POST       one fixed development question through POST /queries is
             answered: a query id, rows, and a trace with its narrative.
             ONE PAID MODEL CALL.
  READBACK   GET /queries/<that id> returns a trace IDENTICAL to the one
             POST returned, compared as canonical JSON, and no rows: the
             trace was stored, whole, and the rows were not (DR-15).

The question is d4 of eval/dev_questions.yaml, the one `smoke_pipeline`
asks. The POST line is strict, as that one is: it needs "answered".

Exit: 0 all pass; 1 any fails; 3 the server answered 503 `not_ready`
(no key, or no stored schema): a clean skip.
"""

import json
import sys
import urllib.error
import urllib.request

from app.smoke_pipeline import NO_KEY_EXIT, QUESTION

API = "http://127.0.0.1:8000/api/v1"


def call(path: str, body: dict | None = None) -> tuple[int, dict]:
    request = urllib.request.Request(
        API + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"content-type": "application/json"},
        method="POST" if body is not None else "GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


def canonical(value) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


def main() -> int:
    failed = False

    status, graph = call("/schema")
    if status == 503:
        print(f"NOT READY  {graph.get('message')}")
        return NO_KEY_EXIT
    if status == 200 and graph["nodes"] and graph["edges"]:
        columns = sum(len(node["columns"]) for node in graph["nodes"])
        print(f"SCHEMA PASS  {len(graph['nodes'])} nodes, {len(graph['edges'])} edges, {columns} columns; snapshot {graph['hash'][:12]}")
    else:
        failed = True
        print(f"SCHEMA FAIL  http {status}: {canonical(graph)[:300]}")

    status, asked = call("/queries", {"question": QUESTION})
    if status == 503:
        print(f"NOT READY  {asked.get('message')}")
        return NO_KEY_EXIT
    if status != 200:
        print(f"POST FAIL  http {status}: {canonical(asked)[:300]}")
        print("READBACK FAIL  nothing was returned to read back")
        return 1
    trace = asked["trace"]
    narrative = trace.get("narrative") or {}
    told = all(narrative.get(part) for part in ("found", "route", "not_taken", "sql", "result"))
    if asked["status"] == "answered" and asked["row_count"] and len(asked["rows"]) == asked["row_count"] and told:
        print(f"POST PASS  answered; {asked['row_count']} rows; conformance {trace['validation']['conformance']}; "
              f"${trace['cost_usd']:.6f}; query {asked['query_id']}")  # fmt: skip
    elif asked["status"] == "not_answerable":
        failed = True
        print("POST FAIL  THE MODEL DECLINED the smoke question. This is the model's answer and not a code fault. "
              f"It was shown: {', '.join((trace.get('generation') or {}).get('tables_shown', []))}")  # fmt: skip
    else:
        failed = True
        print(f"POST FAIL  status {asked['status']} ({asked['message']}); rows {asked['row_count']}; narrative whole: {told}")

    # Whatever the outcome, what was returned must be what was stored.
    status, read = call(f"/queries/{asked['query_id']}")
    if status == 200 and canonical(read["trace"]) == canonical(trace) and read["rows"] is None:
        print(f"READBACK PASS  the stored trace is identical to the one returned ({len(canonical(trace))} characters); no rows stored")
    else:
        failed = True
        same = status == 200 and canonical(read.get("trace")) == canonical(trace)
        print(f"READBACK FAIL  http {status}; trace identical: {same}; rows returned: {read.get('rows') is not None}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
