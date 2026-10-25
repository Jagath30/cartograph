/** A trace of version 3 (step 10b, v2.1 adopted): the tables the model
 *  named, retrieval's scores labelled for what they now do, who decided
 *  each join, and the choices put to the model. In the words approved at
 *  stop F1. Fixtures made offline (backend/tests/stored_traces/make_v3_fixture.py),
 *  from the original set's Q3 and Q15. */
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { expect, test } from "vitest";

import { AmberBlock, DECIDED_BY, Disclosures, Narrative, SCORES_LINE } from "../components/ask/TracePanel";
import storedV2 from "../fixtures/trace-v2-gender-diverged.json";
import storedV3 from "../fixtures/trace-v3-catalog-customers.json";
import declinedV3 from "../fixtures/trace-v3-declined-at-linking.json";
import { alarms } from "../lib/ask/warnings";
import { fromTrace } from "../lib/graph/fromTrace";
import { READABLE_VERSIONS, type TraceDocument } from "../lib/trace/types";

const chosen = storedV3 as unknown as TraceDocument;
const declined = declinedV3 as unknown as TraceDocument;
const draw = (part: (props: { trace: TraceDocument }) => unknown, trace: TraceDocument) =>
  renderToStaticMarkup(createElement(part as never, { trace }));
// The rendered text, tags taken out and entities read.
const text = (html: string) =>
  html.replace(/<[^>]+>/g, "").replaceAll("&#x27;", "'").replaceAll("&quot;", '"').replaceAll("&amp;", "&");

test("the page reads version 3", () => {
  expect(READABLE_VERSIONS).toEqual([1, 2, 3]);
  expect(chosen.trace_version).toBe(3);
});

test("the narrative's first part is the tables named", () => {
  const told = text(draw(Narrative, chosen));
  expect(told).toContain("The tables named for your question");
  expect(told).not.toContain("What your question matched");
  expect(told).toContain("A language model, shown all 24 tables, named 2 for your question: catalog sales and customer address.");
});

test("the first disclosure tells how the tables were found, then retrieval's scores, labelled for what they do", () => {
  const shown = text(draw(Disclosures, chosen));
  expect(shown).toContain("How the tables were found: the model's naming, and retrieval's scores");
  expect(shown).not.toContain("Tables and columns retrieved, with scores");
  expect(shown).toContain("The tables the model named");
  expect(shown).toContain("catalog_sales, customer_address");
  // Made offline, the fixture holds no retrieval; one is lent from the stored version 2 trace.
  const scored = { ...chosen, retrieval: (storedV2 as unknown as TraceDocument).retrieval };
  const scores = text(draw(Disclosures, scored));
  expect(SCORES_LINE).toBe(
    "Retrieval's scores do not choose the tables: they set the order in which the tables are joined, and weigh the wording of your question where it points to one key over another.",
  );
  expect(scores).toContain(SCORES_LINE);
  // Retrieval's anchors chose nothing here, so the bound on them is not told, and no table is called one.
  expect(scores).not.toContain("A table is an anchor from a score of");
  expect(draw(Disclosures, scored)).not.toContain(">anchor</td>");
  expect(draw(Disclosures, storedV2 as unknown as TraceDocument)).toContain(">anchor</td>");
  expect(text(draw(Disclosures, storedV2 as unknown as TraceDocument))).not.toContain(SCORES_LINE);
  expect(shown).toContain(`kept by its sha256, ${chosen.linking!.prompt_user_sha256}`);
  expect(shown).toContain(chosen.linking!.prompt_system.slice(0, 60));
});

test("each table of the plan says who decided it", () => {
  expect(DECIDED_BY).toEqual({
    only_path: "the only route",
    shortest: "the shortest route",
    wording: "the wording of your question",
    declared_default: "this warehouse's declared default",
    model_reading: "a language model reading your question",
    alphabet: "the alphabet: no basis",
  });
  const shown = text(draw(Disclosures, chosen));
  expect(shown).toContain("Decided by: the alphabet: no basis");
  const byModel = structuredClone(chosen);
  byModel.paths!.attachments[1].decided_by = "model_reading";
  expect(text(draw(Disclosures, byModel))).toContain("Decided by: a language model reading your question");
});

test("the choices put to the model, each alternative named or not, and what came of it", () => {
  const shown = text(draw(Disclosures, chosen));
  expect(shown).toContain("What the model was asked about the choices the rules left open");
  const choice = chosen.choosing!.choices[0];
  for (const alternative of choice.alternatives) {
    expect(shown).toContain(`customer_address: ${alternative.letter}. ${alternative.route_id} — ${text(alternative.description)} · not named`);
  }
  expect(shown).toContain("Outcome: no route was named, and the choice was left to the alphabet.");
  expect(shown).toContain("The choosing prompt, exactly as it was sent");
  const named = structuredClone(chosen);
  named.choosing!.choices[0].named = choice.alternatives[1].letter;
  named.choosing!.choices[0].outcome = "applied";
  const told = text(draw(Disclosures, named));
  expect(told).toContain(`${choice.alternatives[1].route_id} — ${text(choice.alternatives[1].description)} · named`);
  expect(told).toContain("Outcome: the route named was taken.");
});

test("a choice left open keeps its amber block", () => {
  expect(alarms(chosen).map((alarm) => alarm.code)).toEqual(["arbitrary_choice"]);
  expect(draw(AmberBlock, chosen)).toContain('data-ask="amber"');
});

test("the model that writes the SQL is named as such", () => {
  expect(text(draw(Disclosures, chosen))).toContain("The model writing the query was asked 1 time");
});

test("the stages a version 3 trace ran, linking and choosing among them", () => {
  const shown = text(draw(Disclosures, chosen));
  expect(shown).toContain("A language model is naming the tables");
  expect(shown).toContain("Asking the model about any choice the rules left open");
});

test("the decline at linking: nothing located, and every part still drawn", () => {
  const shown = text(draw(Disclosures, declined));
  expect(shown).toContain("None: the model named no table that holds the answer.");
  expect(shown).toContain("This trace holds no retrieval.");
  expect(shown).toContain("No SQL was written.");
  expect(shown).not.toContain("What the model was asked about the choices");
  expect(text(draw(Narrative, declined))).toContain("A language model, shown all 24 tables, named none that holds the answer.");
  expect(draw(AmberBlock, declined)).toBe("");
  expect(fromTrace(declined)).toEqual({ nodes: [], edges: [], undrawn: [] });
});

test("the graph marks the tables the model named as the plan's anchors", () => {
  const graph = fromTrace(chosen);
  expect(graph.nodes.filter((node) => node.anchor).map((node) => node.id).sort()).toEqual(["catalog_sales", "customer_address"]);
});
