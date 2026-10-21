"""One answer to the original set's Q3, assembled by whichever build's code
is mounted at /app, with a scripted model and no database. Prints the
document exactly as the trace store serialises it (model_dump_json)."""
import sys

sys.path[:0] = ["/app", "/app/tests"]
from tracing import document, reply  # noqa: E402

SQL = ("SELECT ca.ca_state, COUNT(*) FROM catalog_sales cs JOIN customer_address ca "
       "ON cs.cs_bill_addr_sk = ca.ca_address_sk GROUP BY ca.ca_state")
made = document([reply(SQL)], "catalog_sales", "customer_address", question="Where are our catalog customers located?")
sys.stdout.write(made.model_dump_json())
