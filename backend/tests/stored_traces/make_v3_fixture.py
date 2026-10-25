"""Writes the frontend's version 3 fixtures, offline, with scripted
replies: the original set's Q3 through linking and choosing (the choice
left open, as every run of step 10b left it, CHECKPOINTS item 102), and
its Q15 declined at linking.

    docker compose exec -T backend python tests/stored_traces/make_v3_fixture.py > frontend/fixtures/trace-v3-catalog-customers.json
    docker compose exec -T backend python tests/stored_traces/make_v3_fixture.py declined > frontend/fixtures/trace-v3-declined-at-linking.json
"""

import json
import sys
from pathlib import Path

sys.path[:0] = [str(Path(__file__).resolve().parents[1]), str(Path(__file__).resolve().parents[2])]

from tracing import linked_document, reply  # noqa: E402

QUESTION = "Where are our catalog customers located?"
SQL = ("SELECT ca.ca_state, COUNT(*) AS customers FROM catalog_sales cs JOIN customer_address ca "
       "ON cs.cs_bill_addr_sk = ca.ca_address_sk GROUP BY ca.ca_state")  # fmt: skip

if sys.argv[1:] == ["declined"]:
    document = linked_document([json.dumps({"tables": []})], [], question="Which customers abandoned their carts?",
                               choose_script=[])  # fmt: skip
else:
    document = linked_document(
        [json.dumps({"tables": ["catalog_sales", "customer_address"]})],
        [reply(SQL)],
        question=QUESTION,
        choose_script=[json.dumps({"choices": {"1": None}})],
    )
print(json.dumps(document.model_dump(mode="json"), indent=2, ensure_ascii=False))
