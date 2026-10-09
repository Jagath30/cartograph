"""One call to the SQL model with a fixed, trivial prompt that is not a
question: does this account reach the model, and does it accept the
parameters the method depends on (reasoning effort, temperature, an output
cap, a strict JSON shape)?

    docker compose exec backend python -m app.probe_model

It costs a few hundredths of a cent, is written to the spend ledger like
any call, and prints what came back: never the key, and nothing of a
provider's error but its status and a code from the fixed table.
"""

import json
import sys
from pathlib import Path

from app.config import MODEL_PRICES, get_settings
from app.shell.model_client import LedgeredModel, Message, ModelKeyMissing, ReplyShape, SpendLedger, make_model

LEDGER = Path(__file__).resolve().parents[1] / "eval" / ".spend" / "model_calls.jsonl"
# Step 7's development work stops itself here, well inside its $1.
CEILING_USD = 0.50

SHAPE = ReplyShape(
    "probe",
    {
        "type": "object",
        "properties": {"ok": {"type": "boolean"}},
        "required": ["ok"],
        "additionalProperties": False,
    },
)
MESSAGES = (
    Message("system", "Reply with the JSON object asked for and nothing else."),
    Message("user", 'Reply with {"ok": true}.'),
)


def main() -> None:
    settings = get_settings()
    try:
        model = make_model(settings)
    except ModelKeyMissing as missing:
        sys.exit(f"probe_model: {missing}")
    ledger = SpendLedger(LEDGER)
    reply = LedgeredModel(model, ledger, "probe", CEILING_USD).complete(MESSAGES, SHAPE)

    price = MODEL_PRICES[settings.sql_model]
    print(f"asked for   {reply.requested_model}; reasoning effort {settings.sql_model_reasoning_effort}; "
          f"temperature {settings.sql_model_temperature}; output cap {settings.sql_model_max_output_tokens}")  # fmt: skip
    print(f"answered by {reply.returned_model}; fingerprint {reply.fingerprint}")
    print(f"outcome     {reply.failure or 'ok'}" + (f": {reply.message}" if reply.message else ""))
    print(f"reply       {reply.text!r}")
    print(f"tokens      {reply.tokens_in} in ({reply.tokens_cached} cached), {reply.tokens_out} out; {reply.duration_ms} ms")
    print(f"cost        ${reply.cost_usd:.8f}  (prices read {price.as_of}: ${price.input} in, "
          f"${price.cached_input} cached, ${price.output} out, per million)")  # fmt: skip
    print(f"ledger      {ledger.calls()} calls, ${ledger.total():.8f} in all; ceiling ${CEILING_USD:.2f}")
    if not reply.ok:
        sys.exit(1)
    if json.loads(reply.text) != {"ok": True}:
        sys.exit("probe_model: the reply was not the shape asked for")


if __name__ == "__main__":
    main()
