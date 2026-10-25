/**
 * The trace document and the bodies that cross the API, as the backend
 * declares them (app/core/trace_document.py, app/api_models.py; IR-03,
 * IR-04). Written by hand to mirror those; nothing here adds a field.
 *
 * trace_version 2 adds `plain` to a warning. A trace stored at version 1
 * has none, and is read as it was written (DD-06, DD-07).
 *
 * trace_version 3 (step 10b, v2.1) adds `linking`, `choosing` and each
 * attachment's `decided_by`. The backend leaves each out when absent, so
 * every one is optional here, and a trace of version 1 or 2 is read as it
 * was written.
 */

export type Outcome = "answered" | "not_answerable" | "validation_failed" | "model_failed" | "execution_failed";
export type Conformance = "conforms" | "diverged" | "incomplete" | "not_checked";
export type WarningState = "followed" | "other_route_taken" | "not_used" | "unknown" | "not_applicable";
export type EdgeUse = "present" | "partial" | "missing";
export type KeySource = "catalog" | "overlay";

/** The versions this page can draw. */
export const READABLE_VERSIONS = [1, 2, 3];

export type SchemaRef = { snapshot_id: number | null; hash: string; preferences_hash: string };

export type SettingsInForce = {
  alpha: number | null;
  anchor_cut: number | null;
  anchor_cap: number | null;
  margin: number | null;
  subgraph_bound: number;
  max_joins: number;
  model: string | null;
  temperature: number | null;
};

export type Candidate = {
  kind: "table" | "column";
  name: string;
  table: string;
  column: string | null;
  semantic_score: number;
  keyword_score: number;
  combined: number;
  rank: number;
};

export type TableScore = { table: string; score: number; best: string };
export type AnchorBound = { cut: number; cap: number; excluded_by_cap: string[]; set_aside_as_rivals: string[] };
export type Considered = { element: string; score: number };
export type Rival = { table: string; element: string; score: number };

export type TermAlternative = {
  term: string;
  kind: string;
  chosen_table: string;
  chosen_element: string;
  considered: Considered[];
  rivals: Rival[];
  partners: Rival[];
};

export type CloseCall = {
  term: string;
  chosen: string;
  rival: string;
  chosen_score: number;
  rival_score: number;
  text: string;
};

export type Retrieval = {
  anchors: string[];
  tables: TableScore[];
  candidates: Candidate[];
  candidates_total: number;
  anchor_bound: AnchorBound;
  term_alternatives: TermAlternative[];
  close_calls: CloseCall[];
};

export type Column = {
  name: string;
  data_type: string;
  readable: string;
  primary_key: boolean;
  foreign_key: boolean;
};

export type Node = { table: string; readable: string; anchor: boolean; in_tree: boolean; columns: Column[] };

/** One foreign key, from the table that holds it. */
export type SchemaEdge = {
  from_table: string;
  from_columns: string[];
  to_table: string;
  to_columns: string[];
  source: KeySource;
  constraint: string | null;
  note: string | null;
};

export type Subgraph = { nodes: Node[]; edges: SchemaEdge[] };

export type Join = SchemaEdge & { walked: string; description: string };
export type Route = { id: string; tables: string[]; length: number; joins: Join[]; description: string };
export type Alternative = Route & { status: "tied" | "withdrawn" };

/** trace_version 3: who decided an attachment, from its rule. */
export type DecidedBy = "only_path" | "shortest" | "wording" | "declared_default" | "model_reading" | "alphabet";

export type Attachment = {
  anchor: string;
  anchor_readable: string;
  order: number;
  attached_to: string | null;
  attached_to_readable: string | null;
  rule: string | null;
  reason: string;
  preference_reasons: string[];
  discovered: number;
  selected: Route | null;
  alternatives: Alternative[];
  /** trace_version 3. Absent before it, and for the seed. */
  decided_by?: DecidedBy | null;
};

export type SelectedEdge = SchemaEdge & { use: EdgeUse | null };

export type ActualEdge = {
  left_table: string;
  left_column: string;
  right_table: string;
  right_column: string;
  outer: boolean;
  union: boolean;
  /** Not a join the tree selected, nor implied by one. */
  foreign: boolean;
};

export type Unchecked = { reason: string; detail: string };

export type Warning = {
  code: string;
  text: string;
  about: string[];
  joins: string[];
  other_joins: string[];
  state: WarningState;
  loud: boolean;
  /** trace_version 2: a plain sentence for a loud warning. Absent at version 1. */
  plain?: string | null;
};

export type SubgraphBound = { limit: number; dropped_anchors: string[]; excluded: string[] };

export type Paths = {
  declined: boolean;
  decline_reason: string | null;
  unconnected: string[];
  max_joins: number;
  anchors: string[];
  seed: string | null;
  tables: string[];
  attachments: Attachment[];
  selected_edges: SelectedEdge[];
  actual_edges: ActualEdge[];
  actual_classes: string[][];
  cross_joins: string[][];
  unchecked: Unchecked[];
  diverged: boolean | null;
  subgraph_bound: SubgraphBound;
  warnings: Warning[];
};

export type Message = { role: string; content: string };

export type Attempt = {
  number: number;
  outcome: string;
  messages_added: Message[];
  requested_model: string;
  returned_model: string | null;
  fingerprint: string | null;
  reply: string | null;
  sql: string | null;
  tokens_in: number;
  tokens_cached: number;
  tokens_out: number;
  cost_usd: number;
  duration_ms: number;
  findings: string[];
  tables_outside_prompt: string[];
  failure: string | null;
};

export type Generation = { prompt_system: string; prompt_user: string; tables_shown: string[]; attempts: Attempt[] };

export type Validation = {
  syntax: string;
  read_only: string;
  references: string;
  findings: string[];
  conformance: Conformance | null;
  conformance_findings: string[];
  validated_sha256: string | null;
  executed_sha256: string | null;
};

export type Execution = {
  sql: string;
  tables: string[];
  columns: string[];
  row_count: number;
  truncated: boolean;
  row_cap: number;
  duration_ms: number;
  statement_timeout: string | null;
  failure: string | null;
  sqlstate: string | null;
  error: string | null;
};

/** One model call of linking or choosing: what it sent beyond the one
 *  before, what came back, and what it cost. */
export type ModelCall = {
  number: number;
  outcome: string;
  messages_added: Message[];
  reply: string | null;
  fault: string | null;
  requested_model: string;
  returned_model: string | null;
  fingerprint: string | null;
  tokens_in: number;
  tokens_cached: number;
  tokens_out: number;
  cost_usd: number;
  duration_ms: number;
  failure: string | null;
  failure_class: string | null;
};

export type LinkAttempt = ModelCall & { tables: string[] };

/** trace_version 3: how the tables were found. The user message is kept by
 *  its sha256: it is rebuilt from the snapshot of that hash and the question. */
export type Linking = {
  prompt_system: string;
  prompt_user_sha256: string;
  snapshot_hash: string;
  /** [] for the decline at linking; null when no reply of the form asked for came back. */
  tables: string[] | null;
  attempts: LinkAttempt[];
};

export type ChoiceOffered = { letter: string; route_id: string; description: string };

export type ChoiceAsked = {
  number: number;
  anchor: string;
  attached_to: string;
  alternatives: ChoiceOffered[];
  alphabet: string;
  named: string | null;
  named_route: string | null;
  outcome: "applied" | "left_open" | "not_met";
};

export type ChoiceArose = { anchor: string; attached_to: string; alternatives: string[] };

export type ChooseAttempt = ModelCall & { named: (string | null)[] };

/** trace_version 3: the choices the rules left open, put to the model. */
export type Choosing = {
  prompt_system: string | null;
  prompt_user: string | null;
  choices: ChoiceAsked[];
  arose: ChoiceArose[];
  attempts: ChooseAttempt[];
  fault: string | null;
};

export type Narrative = { found: string; route: string; not_taken: string; sql: string; result: string };

export type TraceDocument = {
  trace_version: number;
  query_id: string;
  user_id: number;
  question: string;
  created_at: string;
  outcome: Outcome;
  code: string | null;
  message: string | null;
  schema_ref: SchemaRef;
  settings: SettingsInForce;
  retrieval: Retrieval | null;
  subgraph: Subgraph | null;
  paths: Paths | null;
  generation: Generation | null;
  validation: Validation | null;
  execution: Execution | null;
  timings: Record<string, number>;
  tokens_in: number;
  tokens_out: number;
  cost_usd: number;
  had_ambiguity: boolean;
  duration_ms: number;
  narrative: Narrative;
  /** trace_version 3. */
  linking?: Linking;
  choosing?: Choosing;
};

/** One cell of a result. A numeric arrives as text, exact; a date in ISO form. */
export type Value = string | number | boolean | null;

/** IR-02, IR-03. Every outcome of the pipeline is one of these. */
export type QueryOut = {
  query_id: string;
  status: Outcome;
  code: string | null;
  message: string | null;
  columns: string[];
  /** Null when nothing was run, or when a stored query is read back (DR-15). */
  rows: Value[][] | null;
  row_count: number | null;
  truncated: boolean | null;
  trace: TraceDocument;
};
