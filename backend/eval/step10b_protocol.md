# Step 10b: the evaluation protocol

DRAFT for the owner's review, 20 October 2026. Not committed. Once
approved it is committed alone with `eval/step10b/frozen_code.json`,
before any question of the fresh held-out set is put to a model, and
before `eval/heldout2.yaml` is read by anything. Modelled on step 10's
protocol (`eval/step10_protocol.md`), which stands unchanged.

## 1. What this measures, and the two claims

Design section 12, DD-20, FR-37, FR-38. Four sets of questions are
answered in four modes, and the SQL each mode generates is measured:
whether it read the expected tables, and whether it joined along the
expected path.

Two claims, kept apart (DD-20):

- **The empirical claim**, measured here: whether linking (v2) and
  choosing (v2.1) bring Cartograph's table and path correctness up to
  full-schema prompting's, which won at step 10 (items 91 to 97). It may
  come out even or against. Whatever it says is reported.
- **The structural claim**, not measured here and never presented as a
  result: Cartograph says when a join choice had no basis in its rules,
  and when the basis was the model's reading of the question rather than
  a rule. Full-schema prompting has no notion of a choice. It is a
  property of the system, shown by the trace, not by these numbers.

Not measured: whether the numbers an answer returned were right (FR-37's
scope). That limit is stated wherever the result is reported.

## 2. What is frozen

From the commit that holds this protocol, nothing that answers a question
or measures an answer changes until the runs are over.

- The files are listed with their sha256 in `eval/step10b/frozen_code.json`,
  written by `python -m app.run_step10b --write-manifest` in this commit:
  `backend/app/*.py`, `app/core/*.py`, `app/shell/*.py`,
  `overlays/*.yaml`, `eval/calibration.json`, `eval/questions.yaml`,
  `requirements.txt` (the patterns of step 10's manifest). `run_step10b
  --run K` refuses to start if any file differs from the manifest, or if
  a file was added or removed, and without `--head`, the full sha of the
  commit it runs on.
- The sets are not in the manifest. Each has its own pinned sha256,
  checked by its loader (`app.run_step10b.load_set`) before it is parsed:
  - `eval/heldout2.yaml`: `ffd28657f7fd1c44d519b84fc63f87e02e0f4504dadfe0393c99b275de20f3d1`
  - `eval/questions.yaml`: `a92f27c2f3fee9c1d178e21504eadc87aa9ec6a622e896561064e798d1100440`
  - `eval/heldout.yaml`: `14e1441e5c9717c519c00410ad27c1bf9b78caf07184141064dffc2377f806fe`
  - `eval/dev2.yaml`: `4d6dd232aa75181e79c0efa162bd1ea118c305e5d3899aed91654076046c13da`
- The step 5, 6, 7 and 10 instruments, the prompt builder, step 10's
  judge (`app/core/eval_step10.py`) and step 10's harness are frozen and
  unchanged; step 10b's runner imports them.
- A fault found after this commit, in the system or in the harness, is
  recorded and reported. It is not fixed before the runs, and no run is
  repeated because of it (section 14).

## 3. The sets

Run in this order in every run, each labelled for what it is wherever it
is reported.

- **heldout2, the headline.** Twelve questions, H1 to H12, drafted from
  the schema alone, outside the project, by a drafter who had seen no
  evaluation result (dev2 is the same drafter's first set); reviewed and
  approved by the owner; frozen at `8577fce` before any v2 code was
  written. The planning chat and every building session have never seen
  it. Before the first run it was read only by code, at its freeze, by a
  check that printed nothing of it but counts and "ok"; in the runs its
  only reader is its loader, called only inside `--run`, after every
  refusal. One question expects a decline.
- **The original set, scored on 15.** The set retrieval was tuned against
  at step 6 and developed against in the retrieval pass (7b). Q15 expects
  a decline. **Question 14 is deleted from scoring in all four modes**
  (item 54; DR-16, T-04; the owner's ruling, 16 October 2026): it names no
  sales channel, yet its expectation assumes the store channel. It is
  still run and its raw results are reported apart (`EXCLUDED` in
  `app/core/eval_step10.py`, pinned by a test).
- **The first held-out set, 12 questions.** Spent at step 10: its
  failures there (items 91 and 92) shaped v2. A development set now. H9
  expects a decline.
- **dev2, 12 questions.** An earlier draft by the same drafter, seen by
  the planning chat; its pilot results shaped v2.1 (its H4, H7 and H8).
  A development set. H12 expects a decline.

## 4. The four modes, and what each adds

The principle, as at step 10 (the owner's ruling on ruling 11): the modes
differ only in what Cartograph contributes.

- **The same in all four:** the generation call's system message, the
  frozen `GENERAL_RULES`, byte for byte; the question text; every table
  rendered by the frozen `render_tables`; the model (`gpt-6-luna`, effort
  `none`, temperature 0, 800 output tokens for generation); the reply's
  form; the validator; the conformance checker; the executor (the
  read-only role, 30-second timeout, 1,000-row cap); one orchestrator.
- **Retrieval (v1)** is the pipeline the application runs today, at the
  settings left by step 6 and the retrieval pass (alpha 0.5, anchor cut
  0.5, cap 5, margin 0.107051, three joins, subgraph bound 10, the
  preferences in force). It adds: which tables (retrieval's anchors and
  the tree's bridges), which joins (DD-12: shortest, the question's
  wording, a declared preference, the alphabet), and the instruction to
  use them (the frozen `JOIN_RULES`).
- **Linked (v2)** adds a linking call before the tree. The same model, at
  an output cap of 400 tokens, is shown every table and every key (its
  user message is full-schema mode's, byte for byte, under the owner's
  `LINK_RULES`) and names the tables the question needs, bridges
  included; an empty list is the decline at linking. The named tables are
  the anchors of the frozen tree; retrieval still scores the question,
  for the anchors' order and the wording's evidence only. From the tree
  on, it is v1.
- **Chosen (v2.1)** is linked, plus one choosing call where the tree holds
  a choice the alphabet made (DD-12's last rule): the same model, at an
  output cap of 400, is shown the question and, for each such choice, its
  alternatives (each as its join condition and the Explainer's sentence,
  listed in the order of the sha256 of the question's text and the route's
  id, never the alphabet's), and names for each the alternative the
  question means, or none. A named route decides that choice for this
  question alone, ranked after DD-12's other rules and ahead of the
  alphabet, and the tree is rebuilt with it; one call, one rebuild. No
  call when the tree holds no such choice. A choosing call that fails
  keeps the v2 tree, every choice left open (option i).
- **Full schema** is step 10's baseline, unchanged: all 24 tables and all
  107 keys under the owner's wording, no tree, no tie-break, no declared
  preference.
- **Retries.** At most two a question, of the kinds DD-15 as amended
  allows, one budget shared by linking, choosing and generation (NFR-15).
- **Linked and chosen are paired** (the owner's ruling 8 at stop A2). In
  each run one linking call per question serves both: the first of the
  two in the run's order makes it, the other is handed the same reply,
  so the difference between them is choosing's alone. The call is counted
  in each mode's tokens, as each would pay for it in the application, and
  once in the spend; each record says whether its linking was made there
  or shared.
- The fairness is pinned by tests: step 10's (`tests/core/test_full_schema.py`,
  `tests/test_step10_fairness.py`); linking's user message is the
  full-schema prompt's byte for byte (`tests/core/test_linking.py`); the
  generation prompt in linked and chosen is the frozen builder's for the
  tree (`tests/test_pipeline_linked.py`); with nothing chosen, every tree
  is byte-identical to v1's (`tests/core/test_chosen_in_tree.py`, held to
  1,902 trees recorded before v2.1's code).

## 5. The measures and the scoring rules

As at step 10, by step 10's frozen judge, in all four modes alike.

- **Table correctness.** The tables the validated SQL reads, as the
  frozen `extract_joins` lists them, equal the question's `tables`, plus
  exactly one of `tables_one_of` where it has any. One too many is wrong,
  as one too few is.
- **Path correctness.** The joins the SQL performs, compared by the
  frozen `compare` with the question's `joins`; with `one_of`
  alternatives, correct when any choice CONFORMS.
- **Declines.** A question expected to be declined is correct on both
  measures if and only if the answer is a decline, at whatever stage it
  was decided (linking, locate or the model). Any other question
  declined: wrong on both, and in both denominators.
- **Failures that leave no accepted SQL** (validation failure after
  retries; a malformed reply after retries; a reply cut off): wrong on
  both, in both denominators.
- **Execution failures are measured on their SQL**, as at step 10;
  execution outcomes are reported beside the score, by failure word.
- **Transport and provider failures** are not the method's behaviour:
  the call is retried up to twice with backoff (5 and 20 seconds); if it
  still fails, the run is void (section 9). Each failed call is recorded
  with its role (generation, linking, choosing) and its exception's class
  name, never its text.
- **The SQL is executed** in every mode the same way. Recorded as at step
  10: outcome, failure word and SQLSTATE, row count and truncated flag,
  duration, and a sha256 over the rows in canonical order. No value is
  kept (DR-15).

Diagnostics outside the score, reported beside it:

- **Declines by stage**, per set and mode: at linking, at locate, by the
  model.
- **Recall and precision of tables:** for linked, the tables linking
  named, the tables of the tree, and the tables shown to the model; for
  chosen, the tree's and the shown; for retrieval, the tree's and the
  shown; summed over the runs. `tables_one_of` is one slot.
- **Choosing**, chosen mode: how many located answers were asked; the
  choices asked, named (applied, not met), left open, and arisen after
  the rebuild; failed choosing calls; how often the alphabet's own pick
  was named, and how often the first-listed alternative was; and how
  often the route named is one the expectation lists.
- **Identical rows**, for chosen against full schema, linked against full
  schema, and chosen against linked: where both executed ok and neither
  result was truncated, whether the rows hashes are equal. It compares
  values returned, not correctness.
- **Item 60** (today's date, empty results), **attribute joins**, and
  `not_checked` by the instrument's reason, per set and mode, as at step
  10.

## 6. Denominators

As at step 10. `not_checked` leaves the path denominator, counted by the
instrument's reason; it leaves the table denominator too only where the
reader could not resolve every table reference. `diverged` wins over
`not_checked`. Declines and failures never leave a denominator. Each
run's denominators are reported beside its counts.

## 7. Attribute joins

As at step 10: any join condition between two columns neither of which
is a key, counted beside the verdict in every mode, never in a score.

## 8. Tokens

Per set and mode, input and output tokens, in total and as a mean per
answer, every answer of question 14 included: generation's, and apart
from it linking's and choosing's. Dollars depend on the provider's cache
and so on run order; they appear only in the spend lines (the owner's
ruling 4 at step 10's stop 2).

## 9. Runs, order and combination

- **N = 5 complete runs**, fixed here and not changed after any result.
- **Each run is its own command:** `python -m app.run_step10b --run K
  --head SHA`, K from 1 to 5, SHA read beforehand by its own `git
  rev-parse HEAD`. Before anything is opened or paid for, the runner
  refuses unless the manifest matches, runs 1 to K-1 each have a complete
  file, run K and every later run have none, run K has been void fewer
  than three times, no run was stopped by the step cap, and there is no
  report. A run runs in the background with its output to a log, and the
  session waits for it to end; never two paid commands at once, and none
  chained to another.
- **Each complete run's files are committed as soon as the run ends**,
  in a commit of their own, before the next run starts. The five may name
  different HEAD commits; the manifest check, recorded in each run file,
  shows the code was the same.
- **The report is its own command**, `python -m app.run_step10b
  --report`, after the fifth complete run. It reads the kept files only,
  calls nothing that is paid, and never overwrites.
- **Order.** In each run: heldout2, then the original set, then the first
  held-out set, then dev2, each in file order. For each question the four
  modes, in an order that rotates with the run: R-L-C-F in run 1, L-C-F-R
  in run 2, C-F-R-L in run 3, F-R-L-C in run 4, R-L-C-F in run 5.
- **A void run** (a transport or provider failure of any call that
  outlasts its retries, a failure to embed, the model's spending ceiling,
  the step cap) is recorded with its cause in a file of its own
  (`run-0K-void-N.json`) and replaced by a complete fresh run of the same
  number. Three void runs in a row stop the step, and so does a run
  stopped by the step cap. No run is ever voided, kept or replaced
  because of its results.
- **Per run:** each measure's count over its denominator, per set and
  mode. **Combined:** the mean of the five runs' fractions, with their
  range. **Per question:** correct in k of n measured runs, per measure
  and mode, stable or not, and how many distinct SQL texts. **Question by
  question:** which of two modes was right in more runs. Descriptive
  only; with 12 to 15 questions a set, no significance is claimed.

## 10. Spend

- Measured in the second pilot (80 answers, $0.04183696): a linking
  prompt is about 7,890 tokens; a full-schema prompt about 7,870; a
  generation prompt over a tree 448 to 3,549; a choosing prompt 297 to
  466. A run is 52 questions in four modes, 208 answers, 52 linking calls
  and, at the pilot's rate (6 in 20 questions), about 16 choosing calls. At the pilot's cost
  per answer, about $0.11 a run, $0.55 for five.
- **The step's cap: $1.00** on ledger purposes beginning `step10b`
  (the owner's ruling 8 at stop A2), the pilots' $0.08347241 included.
  Before every answer the runner checks that the spend so far plus that
  answer's worst case stays within it: three uncached attempts at the
  output cap for each call the answer can make (retrieval and full schema
  $0.0048172; linked $0.0088966; chosen $0.0129938, its choosing prompt
  bounded by the largest prompt any call sends). If not, the run is void
  and nothing more is run.
- The model's own ceiling on the whole ledger stays as a backstop. Every
  embedding is on the ledger.

## 11. What is recorded, and where

The committed, hashed files are the evaluation's record. In
`backend/eval/step10b/runs/`: `run-0K.json` per complete run and
`run-0K-void-N.json` per void run, each recording its HEAD commit and the
manifest check's result, with every answer whole: as at step 10, plus
where a decline was decided, the expected tables, the linking (the tables
named, each attempt's tokens, cost, fault and exception class, whether it
was shared and from which mode), and the choosing (each choice asked, its
alternatives as listed, the alphabet's pick, what was named, the outcome,
what arose after the rebuild, each attempt); `prompts/` with every prompt
exactly as sent (linking, choosing and generation), by its sha256; and
`report.txt`, made by its own command after the fifth complete run. The
runner creates every file exclusively.

## 12. The headline, and the adoption rule

**The headline**, in this order (the owner's ruling 9 at stop A2): on
heldout2, chosen against full schema; then linked against full schema;
then chosen against linked; then the four modes side by side. Each as
the mean of five runs with its range, tables and path. Then the original
set, the first held-out set and dev2, each labelled as in section 3, in
the same form. The README will say that dev2 helped shape v2.1.

**The adoption rule**, fixed here before any run: the application adopts
whichever of Cartograph's three modes (retrieval, linked, chosen) has the
highest mean path correctness on heldout2. A tie goes to the higher mean
table correctness on heldout2, then to the mode with fewer model calls
per answer. Full schema is the baseline, not a candidate: it explains
nothing. The trace version, the narrative, the panel, the Intro, the
stage estimate, the README and the demonstration tests change only after
the evaluation, and only for the mode adopted (the owner's ruling 7 at
stop A).

## 13. Predictions, written before any run

Written by the session that built v2 and v2.1, before any question of
heldout2 had been seen by that session or the planning chat, or put to
a model. They rest on step 10's results, on
the two pilots over dev2 and d1 to d8, and on what the code does. Each
is decided by a figure in the report; "within one question" on a set of
12 means a difference of at most 8.4 percentage points between means.

1. **Linking finds the tables.** On heldout2, linked's mean table
   correctness is at least 80.0%, and at least full schema's mean minus
   8.4 points. *Reason:* linking is shown exactly what full schema is
   shown, and names tables, which is the part of the task full schema
   got right at step 10 (90.0% on the first held-out set); on dev2 it
   named all 36 expected tables twice, with no surplus. *Decided by:* the
   two means for heldout2, tables.
2. **Choosing never makes chosen worse than linked.** On heldout2,
   chosen's mean path correctness is at least linked's. *Reason:* the two
   are paired on one linking call, so they differ only where the
   alphabet chose, and by the variation between two generation calls on
   the same prompt; there choosing names a route or leaves the choice
   open, and on dev2 every route it named was the expected one. *Decided
   by:* the two means for heldout2, path.
3. **Chosen comes within one question of full schema on path.** On
   heldout2, |chosen's mean path correctness minus full schema's| is at
   most 8.4 points. *Reason:* with the right tables, what is left is the
   choice of keys; the alphabet's choices are now put to the model, and
   what remains is the wording rule's own errors (H8's kind), which full
   schema does not make. On dev2 they differed by one question. *Decided
   by:* the two means for heldout2, path.
4. **Retrieval stays far behind.** On heldout2, retrieval's mean table
   correctness is at most 40.0%. *Reason:* its recall of needed tables
   decided step 10 (items 91 and 68: 20.0% on the first held-out set) and
   dev2 in both pilots (3 of 12); nothing in it changed. *Decided by:*
   retrieval's heldout2 table mean.
5. **Linking's recall and precision are high on a set it never saw.**
   Summed over five runs on heldout2, the tables named by linking reach a
   recall of at least 90% and a precision of at least 90%. *Reason:* as
   in prediction 1; on dev2 both were 100%. *Decided by:* the "tables
   named by linking" line for heldout2.
6. **The expected decline is declined at linking.** In linked and
   chosen, heldout2's one decline question is declined in at least 4 of
   5 runs, at linking in each of those. *Reason:* dev2's H12 and d8 were
   declined at linking in both pilots. *Decided by:* that question's
   per-run verdicts and the declines-by-stage lines.
7. **Choosing is asked on a minority of answers.** On every set, chosen
   asks on at most half of its located answers, summed over the runs.
   *Reason:* with linking naming the bridges, most trees hold no tie the
   alphabet breaks; on dev2 it asked on 3 of 11, on d1 to d8 on 3 of 7.
   *Decided by:* the "choosing:" line per set.
8. **Where choosing names a route, it is usually the expected one.**
   Over all four sets and five runs, of the named routes whose question
   has an expectation, at least two thirds are a route the expectation
   lists. *Reason:* 3 of 3 on dev2; the alternatives are short and their
   sentences name what each key means. *Decided by:* the "named the route
   the expectation lists" lines, summed.
9. **No strong lean to the first-listed alternative.** Over all four
   sets and five runs, the first-listed alternative is named in at most
   60% of the choices where any was named. *Reason:* 2 of 5 in the
   second pilot, against 3 of 5 for the alphabet's own pick; a lean
   would show above 60% with two alternatives in most choices. *Decided
   by:* the "named the first-listed alternative" lines, summed.
10. **Linking is stable.** On heldout2, linking names the same tables in
    all five runs for at least 10 of the 12 questions. *Reason:* the
    joins were stable at step 10 where the SQL text was not (item 63),
    and naming tables is a shorter answer than SQL. *Decided by:* the
    run files (each linked record's named tables), counted per question.
11. **The original set: linked and chosen beat retrieval on tables.**
    Chosen's and linked's mean table correctness on the original 15 are
    each at least retrieval's, which was tuned against it (65.3% at step
    10). *Reason:* linking does not miss `date_dim` (item 68) or a sales
    table (item 55). *Decided by:* the original set's table means.
12. **The adoption rule picks chosen.** *Reason:* predictions 2 and 4.
    *Decided by:* section 12's rule applied to the heldout2 means.
13. **No run is void, and the step stays under $0.75.** *Reason:* no
    void in the second pilot; about $0.55 forecast. *Decided by:* the
    run files and the spend lines.

A result against v2 or v2.1, or even, is a finding, and is reported as
such, without softening. In particular, if full schema still leads on
path, the README says so, and says what Cartograph's trace gives that
full schema's does not (the structural claim, section 1), as a property.

## 14. The commitment, and faults found after this commit

Whatever the runs say is reported, in the order of section 12: heldout2
first, then the original set, the first held-out set and dev2, labelled
as in section 3, then the diagnostics. No question is added, removed or
rescored after this commit; no run is added. The structural claim is
stated as a property, never as a result.

A fault found after this commit, in the system or in the harness, is
recorded in CHECKPOINTS.md with what it touched, and reported with the
result. It is not fixed before the runs. A fault that makes a run's
record wrong (as against its result unwelcome) is reported, and the run
stands as recorded unless the owner rules otherwise, in writing, with
the reason.

If the harness itself stops a run before it completes (a crash, not a
result), the session stops and reports. Any fix is made only on the
owner's written ruling, tests first, in its own commit with a new
manifest, recorded in CHECKPOINTS.md; completed runs stand, and the
stopped run is replaced by a complete fresh run of the same number.

If a fault is found in the scoring after the runs, the kept SQL may be
scored again only on the owner's written ruling; both the original and
the corrected figures are then reported, with the reason.
