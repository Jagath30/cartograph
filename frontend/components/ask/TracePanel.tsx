"use client";

import { count, seconds } from "@/lib/ask/format";
import { alarms, quietWarnings } from "@/lib/ask/warnings";
import type { DecidedBy, ModelCall, TraceDocument } from "@/lib/trace/types";

import { CopyButton } from "./Actions";
import { StageTimes } from "./StageIndicator";

/**
 * The amber block (DD-19, DD-21, FR-45). It is there only when the trace
 * stored a warning as loud; on any other question the panel is quiet.
 * Each sentence is one the trace holds.
 */
export function AmberBlock({ trace }: { trace: TraceDocument }) {
  const shown = alarms(trace);
  if (shown.length === 0) return null;
  return (
    <section
      className="rounded-md border-2 border-amber-400 bg-amber-50 p-6 text-amber-950"
      role="alert"
      data-ask="amber"
    >
      <h2 className="text-base font-semibold">
        {shown.length === 1
          ? "This answer rests on a choice the system had no basis for"
          : `This answer rests on ${shown.length} choices the system had no basis for`}
      </h2>
      <ul className="mt-2 space-y-2">
        {shown.map((alarm, index) => (
          <li key={index} className="text-[15px] leading-relaxed">
            {alarm.sentence}
          </li>
        ))}
      </ul>
    </section>
  );
}

const PARTS: { key: "found" | "route" | "not_taken" | "sql"; label: string }[] = [
  { key: "found", label: "What your question matched" },
  { key: "route", label: "How it was linked" },
  { key: "not_taken", label: "What was not used" },
  { key: "sql", label: "What the query did" },
];

/** trace_version 3: the tables were named by a model, not matched (stop F1). */
const PARTS_LINKED: typeof PARTS = [
  { key: "found", label: "The tables named for your question" },
  { key: "route", label: "How it was linked" },
  { key: "not_taken", label: "What was not used" },
  { key: "sql", label: "What the query did" },
];

/**
 * The narrative: the stored plain-English account (FR-26, NFR-17, DD-06).
 * Its strings are shown as they were stored. What came back is told
 * beside it, with the results.
 */
export function Narrative({ trace }: { trace: TraceDocument }) {
  return (
    <section data-ask="narrative">
      <h2 className="text-sm font-semibold uppercase tracking-wide text-gray-600">How this answer was reached</h2>
      <dl className="mt-3 space-y-3">
        {(trace.linking ? PARTS_LINKED : PARTS).map((part) => (
          <div key={part.key}>
            <dt className="text-xs font-medium uppercase tracking-wide text-gray-500">{part.label}</dt>
            <dd className="mt-0.5 text-[15px] leading-relaxed text-gray-900">{trace.narrative[part.key]}</dd>
          </div>
        ))}
      </dl>
    </section>
  );
}

function Score({ value }: { value: number }) {
  return <span className="tabular-nums">{value.toFixed(3)}</span>;
}

function Listed({ label, items }: { label: string; items: string[] }) {
  if (items.length === 0) return null;
  return (
    <p className="mt-2 text-sm">
      <span className="text-gray-600">{label}:</span> <span className="font-mono text-xs">{items.join(", ")}</span>
    </p>
  );
}

const HEAD = "mt-6 text-xs font-semibold uppercase tracking-wide text-gray-600 first:mt-0";
const SUMMARY = "cursor-pointer select-none rounded-md border border-gray-200 bg-white px-4 py-3 text-sm font-medium text-gray-800 hover:bg-gray-50";
const BODY = "rounded-b-md border border-t-0 border-gray-200 bg-white px-4 py-4";
// A table wider than the screen scrolls sideways in its own box, which
// takes focus so that it can be scrolled by keyboard (polish D).
const SIDEWAYS = "mt-2 overflow-x-auto";

/** One model call of linking or choosing, on one line. */
function Call({ label, call }: { label: string; call: ModelCall }) {
  return (
    <li>
      <span className="font-medium">{label}:</span> {call.outcome}
      <span className="text-gray-600">
        {" "}
        &middot; {call.returned_model ?? call.requested_model} &middot; {count(call.tokens_in)} tokens in,{" "}
        {count(call.tokens_out)} out &middot; ${call.cost_usd.toFixed(6)} &middot; {seconds(call.duration_ms)}
      </span>
      {(call.fault ?? call.failure) && <span className="block text-red-800">{call.fault ?? call.failure}</span>}
    </li>
  );
}

/** The owner's words at stop F1. */
export const SCORES_LINE =
  "Retrieval's scores do not choose the tables: they set the order in which the tables are joined, and weigh the wording of your question where it points to one key over another.";

/** trace_version 3: what the model, shown every table, named (stop F1). */
function Named({ linking }: { linking: NonNullable<TraceDocument["linking"]> }) {
  return (
    <>
      <h3 className={HEAD}>The tables the model named</h3>
      <p className="mt-2 text-sm">
        {linking.tables === null ? (
          "No reply of the form asked for came back."
        ) : linking.tables.length === 0 ? (
          "None: the model named no table that holds the answer."
        ) : (
          <span className="font-mono text-xs">{linking.tables.join(", ")}</span>
        )}
      </p>
      <ul className="mt-2 space-y-2 text-sm">
        {linking.attempts.map((attempt) => (
          <Call key={attempt.number} label={`Naming, attempt ${attempt.number}`} call={attempt} />
        ))}
      </ul>
      <details className="mt-3">
        <summary className="cursor-pointer text-sm text-gray-700">The naming prompt, as it was sent</summary>
        <Code label="The naming prompt's system message">{linking.prompt_system}</Code>
        <p className="mt-2 break-all text-sm text-gray-700">
          The user message held every table and key of the warehouse, and the question. It is kept by its sha256,{" "}
          <span className="font-mono text-xs">{linking.prompt_user_sha256}</span>, and is rebuilt from schema snapshot{" "}
          <span className="font-mono text-xs">{linking.snapshot_hash.slice(0, 12)}</span> and the question.
        </p>
      </details>
    </>
  );
}

/** The first of the two disclosures: what retrieval found, with its scores
 *  (FR-09, FR-41); at trace_version 3, the tables the model named first. */
function Retrieved({ trace }: { trace: TraceDocument }) {
  const retrieval = trace.retrieval;
  const bound = trace.paths?.subgraph_bound;
  const linking = trace.linking;
  const named = linking?.tables ?? [];
  return (
    <details data-ask="retrieved">
      <summary className={SUMMARY}>
        {linking ? "How the tables were found: the model's naming, and retrieval's scores" : "Tables and columns retrieved, with scores"}
      </summary>
      <div className={BODY}>
        {linking && <Named linking={linking} />}
        {!retrieval ? (
          <p className={linking ? "mt-6 text-sm text-gray-600" : "text-sm text-gray-600"}>This trace holds no retrieval.</p>
        ) : (
          <>
            {linking && (
              <p className="mt-6 text-sm text-gray-700">{SCORES_LINE}</p>
            )}
            <h3 className={HEAD}>Tables, by score</h3>
            <div className={SIDEWAYS} role="region" aria-label="Tables, by score" tabIndex={0}>
            <table className="text-sm">
              <thead className="text-left text-xs text-gray-500">
                <tr>
                  <th className="pr-6 font-medium">table</th>
                  <th className="pr-6 font-medium">score</th>
                  <th className="pr-6 font-medium">best element</th>
                  <th className="font-medium">&nbsp;</th>
                </tr>
              </thead>
              <tbody>
                {retrieval.tables.map((entry) => (
                  <tr key={entry.table}>
                    <td className="pr-6 font-mono text-xs">{entry.table}</td>
                    <td className="pr-6">
                      <Score value={entry.score} />
                    </td>
                    <td className="pr-6 font-mono text-xs text-gray-600">{entry.best}</td>
                    <td className="text-xs text-gray-600">
                      {linking ? (named.includes(entry.table) ? "named" : "") : retrieval.anchors.includes(entry.table) ? "anchor" : ""}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            </div>
            {linking ? (
              bound && <p className="mt-2 text-sm text-gray-700">The plan may hold {bound.limit} tables.</p>
            ) : (
              <>
                <p className="mt-2 text-sm text-gray-700">
                  A table is an anchor from a score of <Score value={retrieval.anchor_bound.cut} />, and at most{" "}
                  {retrieval.anchor_bound.cap} are taken.
                  {bound && ` The plan may hold ${bound.limit} tables.`}
                </p>
                <Listed label="Left out by the limit on anchors" items={retrieval.anchor_bound.excluded_by_cap} />
                <Listed label="Set aside as rivals of a table taken" items={retrieval.anchor_bound.set_aside_as_rivals} />
              </>
            )}
            <Listed
              label={linking ? "Named tables dropped to keep the plan within its limit" : "Anchors dropped to keep the plan within its limit"}
              items={bound?.dropped_anchors ?? []}
            />
            <Listed label="Tables the limit kept out" items={bound?.excluded ?? []} />

            <h3 className={HEAD}>What each word of the question was weighed against</h3>
            <ul className="mt-2 space-y-1.5 text-sm">
              {retrieval.term_alternatives.map((term) => (
                <li key={`${term.kind}:${term.term}`}>
                  <span className="font-medium">&ldquo;{term.term}&rdquo;</span>{" "}
                  <span className="text-gray-600">chose</span>{" "}
                  <span className="font-mono text-xs">{term.chosen_element}</span>
                  <span className="text-gray-600">
                    {" "}
                    over{" "}
                    {term.considered
                      .filter((other) => other.element !== term.chosen_element)
                      .map((other) => `${other.element} ${other.score.toFixed(3)}`)
                      .join(", ") || "nothing else"}
                  </span>
                </li>
              ))}
            </ul>

            {retrieval.close_calls.length > 0 && (
              <>
                <h3 className={HEAD}>Close calls</h3>
                <ul className="mt-2 list-disc space-y-1 pl-5 text-sm">
                  {retrieval.close_calls.map((call, index) => (
                    <li key={index}>{call.text}</li>
                  ))}
                </ul>
              </>
            )}

            <h3 className={HEAD}>
              The {retrieval.candidates.length} best-scored of {count(retrieval.candidates_total)} tables and columns
            </h3>
            <div className={SIDEWAYS} role="region" aria-label="The best-scored tables and columns" tabIndex={0}>
            <table className="text-sm">
              <thead className="text-left text-xs text-gray-500">
                <tr>
                  <th className="pr-6 font-medium">rank</th>
                  <th className="pr-6 font-medium">element</th>
                  <th className="pr-6 font-medium">by meaning</th>
                  <th className="pr-6 font-medium">by wording</th>
                  <th className="font-medium">combined</th>
                </tr>
              </thead>
              <tbody>
                {retrieval.candidates.map((candidate) => (
                  <tr key={candidate.name}>
                    <td className="pr-6 tabular-nums text-gray-600">{candidate.rank}</td>
                    <td className="pr-6 font-mono text-xs">{candidate.name}</td>
                    <td className="pr-6">
                      <Score value={candidate.semantic_score} />
                    </td>
                    <td className="pr-6">
                      <Score value={candidate.keyword_score} />
                    </td>
                    <td>
                      <Score value={candidate.combined} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            </div>
          </>
        )}
      </div>
    </details>
  );
}

/** trace_version 3: who decided each attachment, in words (stop F1). */
export const DECIDED_BY: Record<DecidedBy, string> = {
  only_path: "the only route",
  shortest: "the shortest route",
  wording: "the wording of your question",
  declared_default: "this warehouse's declared default",
  model_reading: "a language model reading your question",
  alphabet: "the alphabet: no basis",
};

const SETTLED: Record<NonNullable<TraceDocument["choosing"]>["choices"][number]["outcome"], string> = {
  applied: "the route named was taken",
  left_open: "no route was named, and the choice was left to the alphabet",
  not_met: "the route named could not be taken, and the choice was left to the alphabet",
};

/** trace_version 3: each choice the rules left open, as it was put to the
 *  model, and what it named. */
function Choices({ choosing }: { choosing: NonNullable<TraceDocument["choosing"]> }) {
  return (
    <>
      <h3 className={HEAD}>What the model was asked about the choices the rules left open</h3>
      <ul className="mt-2 space-y-3 text-sm">
        {choosing.choices.map((choice) => (
          <li key={choice.number}>
            <ul className="space-y-1">
              {choice.alternatives.map((alternative) => (
                <li key={alternative.letter}>
                  <span className="font-mono text-xs">{choice.anchor}</span>: {alternative.letter}.{" "}
                  <span className="font-mono text-xs">{alternative.route_id}</span> &mdash; {alternative.description}{" "}
                  <span className="text-gray-600">
                    &middot; {choice.named === alternative.letter ? "named" : "not named"}
                  </span>
                </li>
              ))}
            </ul>
            <span className="mt-1 block text-gray-700">Outcome: {SETTLED[choice.outcome]}.</span>
          </li>
        ))}
      </ul>
      {choosing.arose.length > 0 && (
        <Listed label="Choices that arose after the rebuild, never asked about" items={choosing.arose.map((arose) => arose.anchor)} />
      )}
      {choosing.fault && <p className="mt-2 text-sm text-red-800">Every choice was left open: {choosing.fault}</p>}
      <ul className="mt-2 space-y-2 text-sm">
        {choosing.attempts.map((attempt) => (
          <Call key={attempt.number} label={`Choosing, attempt ${attempt.number}`} call={attempt} />
        ))}
      </ul>
      {choosing.prompt_system && choosing.prompt_user && (
        <details className="mt-3">
          <summary className="cursor-pointer text-sm text-gray-700">The choosing prompt, exactly as it was sent</summary>
          <Code label="The choosing prompt's system message">{choosing.prompt_system}</Code>
          <Code label="The choosing prompt's user message">{choosing.prompt_user}</Code>
        </details>
      )}
    </>
  );
}

// Code keeps its lines: a wide one scrolls sideways inside its own box.
const CODE = "mt-2 overflow-x-auto whitespace-pre rounded bg-gray-50 p-3 font-mono text-xs text-gray-900";

/** A block of code. It takes focus, so that one wider than the screen can
 * be scrolled by keyboard (polish D: axe's scrollable-region-focusable). */
function Code({ label, children }: { label: string; children: string }) {
  return (
    <pre className={CODE} role="region" aria-label={label} tabIndex={0}>
      {children}
    </pre>
  );
}

/** The second: the SQL, how it was checked, what it took and what it cost (FR-17, FR-32, NFR-17). */
function Generated({ trace }: { trace: TraceDocument }) {
  const { generation, validation, execution, paths } = trace;
  const sql = execution?.sql ?? generation?.attempts.at(-1)?.sql ?? null;
  const quiet = quietWarnings(trace);
  return (
    <details data-ask="generated">
      <summary className={SUMMARY}>Generated SQL &middot; validation &middot; timings &middot; cost</summary>
      <div className={BODY}>
        <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
          <h3 className={HEAD}>The SQL {execution ? "that was run" : sql ? "the model wrote; it was not run" : ""}</h3>
          {sql && <CopyButton label="Copy SQL" text={() => sql} />}
        </div>
        {sql ? <Code label="The SQL">{sql}</Code> : <p className="mt-2 text-sm text-gray-600">No SQL was written.</p>}

        {generation && (
          <>
            <h3 className={HEAD}>
              {trace.linking ? "The model writing the query" : "The model"} was asked {generation.attempts.length}{" "}
              {generation.attempts.length === 1 ? "time" : "times"}
            </h3>
            <ul className="mt-2 space-y-2 text-sm">
              {generation.attempts.map((attempt) => (
                <li key={attempt.number}>
                  <span className="font-medium">Attempt {attempt.number}:</span> {attempt.outcome}
                  <span className="text-gray-600">
                    {" "}
                    &middot; {attempt.returned_model ?? attempt.requested_model} &middot; {count(attempt.tokens_in)} tokens
                    in, {count(attempt.tokens_out)} out &middot; ${attempt.cost_usd.toFixed(6)} &middot;{" "}
                    {seconds(attempt.duration_ms)}
                  </span>
                  {attempt.failure && <span className="block text-red-800">{attempt.failure}</span>}
                  {attempt.findings.map((finding) => (
                    <span key={finding} className="block text-red-800">
                      {finding}
                    </span>
                  ))}
                  {attempt.sql && attempt.sql !== sql && <Code label={`The SQL of attempt ${attempt.number}`}>{attempt.sql}</Code>}
                </li>
              ))}
            </ul>
            <Listed label="Tables the model was shown" items={generation.tables_shown} />
          </>
        )}

        <h3 className={HEAD}>Validation</h3>
        {validation ? (
          <>
            <p className="mt-2 text-sm">
              Parses: <strong>{validation.syntax}</strong> &middot; read-only: <strong>{validation.read_only}</strong>{" "}
              &middot; every table and column exists: <strong>{validation.references}</strong> &middot; joins against
              the plan: <strong>{validation.conformance ?? "not checked"}</strong>
              {paths?.diverged && " (the query did not follow the plan)"}
            </p>
            {[...validation.findings, ...validation.conformance_findings].length > 0 && (
              <ul className="mt-2 list-disc space-y-1 pl-5 text-sm">
                {[...validation.findings, ...validation.conformance_findings].map((finding) => (
                  <li key={finding}>{finding}</li>
                ))}
              </ul>
            )}
            {(paths?.unchecked ?? []).map((unchecked) => (
              <p key={unchecked.detail} className="mt-2 text-sm text-amber-900">
                Not read with confidence ({unchecked.reason}): {unchecked.detail}
              </p>
            ))}
          </>
        ) : (
          <p className="mt-2 text-sm text-gray-600">Nothing was validated: no SQL reached that stage.</p>
        )}

        {execution && (
          <>
            <h3 className={HEAD}>Execution</h3>
            <p className="mt-2 text-sm">
              {execution.failure
                ? `Failed (${execution.failure}${execution.sqlstate ? `, SQLSTATE ${execution.sqlstate}` : ""}).`
                : `${count(execution.row_count)} ${execution.row_count === 1 ? "row" : "rows"} in ${seconds(execution.duration_ms)}.`}{" "}
              On the read-only connection, limit {execution.statement_timeout ?? "not recorded"}, at most{" "}
              {count(execution.row_cap)} rows.
            </p>
            {execution.error && <Code label="The database's message">{execution.error}</Code>}
          </>
        )}

        {paths && paths.attachments.some((attachment) => attachment.selected) && (
          <>
            <h3 className={HEAD}>The plan, table by table</h3>
            <ul className="mt-2 space-y-2 text-sm">
              {paths.attachments.map((attachment) => (
                <li key={attachment.anchor}>
                  <span className="font-mono text-xs">{attachment.anchor}</span>
                  {attachment.attached_to && (
                    <>
                      {" "}
                      <span className="text-gray-600">joined to</span>{" "}
                      <span className="font-mono text-xs">{attachment.attached_to}</span>
                    </>
                  )}
                  {attachment.rule && <span className="text-gray-600"> &middot; rule: {attachment.rule}</span>}
                  {attachment.decided_by && (
                    <span className="block text-gray-600">Decided by: {DECIDED_BY[attachment.decided_by]}</span>
                  )}
                  <span className="block text-gray-800">{attachment.reason}</span>
                </li>
              ))}
            </ul>
          </>
        )}

        {trace.choosing && trace.choosing.choices.length > 0 && <Choices choosing={trace.choosing} />}

        {quiet.length > 0 && (
          <>
            <h3 className={HEAD}>Raised about the plan, and not about this answer</h3>
            <ul className="mt-2 space-y-2 text-sm">
              {quiet.map((warning, index) => (
                <li key={index}>
                  <span className="text-gray-600">
                    {warning.code} &middot; {warning.state.replaceAll("_", " ")}:
                  </span>{" "}
                  {warning.text}
                </li>
              ))}
            </ul>
          </>
        )}

        <h3 className={HEAD}>Timings</h3>
        <div className="mt-2 max-w-xl">
          <StageTimes timings={trace.timings} traceVersion={trace.trace_version} />
          <p className="mt-1 flex justify-between gap-6 border-t pt-1 text-sm font-medium">
            <span>In all</span>
            <span className="tabular-nums">{trace.duration_ms} ms</span>
          </p>
        </div>

        <h3 className={HEAD}>Cost and settings</h3>
        <p className="mt-2 text-sm">
          ${trace.cost_usd.toFixed(6)} &middot; {count(trace.tokens_in)} tokens in, {count(trace.tokens_out)} out
          &middot; model {trace.settings.model ?? "none"} at temperature {trace.settings.temperature ?? "—"}
        </p>
        <p className="mt-1 break-all font-mono text-xs text-gray-600">
          schema snapshot {trace.schema_ref.snapshot_id ?? "—"} {trace.schema_ref.hash.slice(0, 12)} &middot; preferences{" "}
          {trace.schema_ref.preferences_hash.slice(0, 12)} &middot; alpha {trace.settings.alpha ?? "—"} &middot; cut{" "}
          {trace.settings.anchor_cut ?? "—"} &middot; cap {trace.settings.anchor_cap ?? "—"} &middot; margin{" "}
          {trace.settings.margin ?? "—"} &middot; bound {trace.settings.subgraph_bound} &middot; max joins{" "}
          {trace.settings.max_joins} &middot; query {trace.query_id}
        </p>

        {generation && (
          <details className="mt-6">
            <summary className="cursor-pointer text-sm text-gray-700">The prompt, exactly as it was sent</summary>
            <Code label="The prompt's system message">{generation.prompt_system}</Code>
            <Code label="The prompt's user message">{generation.prompt_user}</Code>
          </details>
        )}
      </div>
    </details>
  );
}

/** Everything technical, behind two disclosures (DD-19). */
export function Disclosures({ trace }: { trace: TraceDocument }) {
  return (
    <div className="space-y-3">
      <Retrieved trace={trace} />
      <Generated trace={trace} />
    </div>
  );
}
