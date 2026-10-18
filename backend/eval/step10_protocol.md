# Step 10: the evaluation protocol

DRAFT for the owner's review, 16 October 2026. Not committed. Once
approved it is committed alone with `eval/step10/frozen_code.json`, before
any question of either frozen set is put to a model in this step, and
before `eval/heldout.yaml` is opened.

## 1. What this measures, and the two claims

Design section 12, DD-20, FR-37, FR-38. Both question sets are answered
in two modes, and the SQL each mode generates is measured: whether it
read the expected tables, and whether it joined along the expected path.

Two claims, kept apart (DD-20):

- **The empirical claim**, measured here: that graph retrieval produces
  more correct join paths and tables than full-schema prompting. It may
  come out even or against. Whatever it says is reported.
- **The structural claim**, not measured here and never presented as a
  result: Cartograph reports when a choice between join paths had no
  basis. Full-schema prompting has no notion of a choice. It is a
  property of the system, shown by the trace, not by these numbers.

Not measured: whether the numbers an answer returned were right (FR-37's
scope). That limit is stated wherever the result is reported.

## 2. What is frozen

From the commit that holds this protocol, nothing that answers a question
or measures an answer changes until the runs are over.

- The files are listed with their sha256 in `eval/step10/frozen_code.json`,
  written by `python -m app.run_step10 --write-manifest` in this commit:
  `backend/app/*.py`, `app/core/*.py`, `app/shell/*.py`,
  `overlays/*.yaml`, `eval/calibration.json`, `eval/questions.yaml`,
  `requirements.txt`. `run_step10 --run K` refuses to start if any file
  differs from the manifest, or if a file was added or removed, and
  without `--head`, the full sha of the commit it runs on.
- `eval/heldout.yaml` is not in the manifest. It has its own pinned
  sha256 (`14e1441e...806fe`), which its only reader,
  `app.shell.step10.load_heldout`, checks before parsing.
- The step 5, 6 and 7 instruments, the sets and the prompt builder were
  frozen before this step and are unchanged.
- A fault found after this commit, in the system or in the harness, is
  recorded and reported. It is not fixed before the runs, and no run is
  repeated because of it (section 13).

## 3. The sets

- **The held-out set, the headline, for both modes.** Twelve questions,
  H1 to H12, written after step 7 closed from the schema alone, frozen at
  `2db2709`, never run, embedded or sent to a model before this step. H9
  expects a decline.
- **The original set, beside it, scored on 15.** Labelled plainly as the
  set the system was tuned and developed against: retrieval was tuned
  against it at step 6, and it was the development set of the retrieval
  pass (7b). Q15 expects a decline.
- **Question 14 is deleted from scoring in both modes** (item 54; DR-16,
  T-04; the owner's ruling, 16 October 2026). It names no sales channel,
  yet its expectation assumes the store channel, so no answer is
  determined by the question. It is still run in both modes and its raw
  results are reported apart. The deletion is pinned by a test
  (`EXCLUDED` in `app/core/eval_step10.py`).

## 4. The two modes

The principle (the owner's ruling on ruling 11): the two modes' prompts
differ only in what Cartograph contributes.

- **The same in both:** the system message, the frozen `GENERAL_RULES`,
  byte for byte; the question text; every table rendered by the frozen
  `render_tables`; the model (`gpt-6-luna`, effort `none`, temperature 0,
  800 output tokens); the reply's form; the retries (at most two, of the
  kinds DD-15 as amended allows); the validator; the conformance checker;
  the executor (the read-only role, 30-second timeout, 1,000-row cap).
  One orchestrator serves both.
- **Retrieval mode** is the pipeline the application runs, at the
  settings left by step 6 and the retrieval pass (alpha 0.5, anchor cut
  0.5, cap 5, margin 0.107051, three joins, subgraph bound 10, the
  preferences in force). It adds the selection: which tables (the tree's,
  then tables on tied alternatives), which joins, and the instruction to
  use them (the frozen `JOIN_RULES`).
- **Full-schema mode** gets all 24 tables in alphabetical order and,
  where retrieval mode has its join section, every one of the graph's 107
  keys, catalog and overlay alike, unmarked, each line exactly as the
  frozen builder renders it, under the owner's wording, word for word:

  > Keys between tables. These are the keys by which the tables above can
  > be joined. A line with two conditions joined by AND is one key and
  > needs both. Two tables may be joined by more than one key. Use only
  > the tables the question needs: a table listed above need not appear
  > in the query.

  No tree, no tie-break, no declared preference. A clarification of
  DD-20, owed to the Design: the Design never specified a full-schema
  mode without relationships; it calls that mode "not handicapped", and
  giving it every key keeps that true. The README will say the baseline
  was given every key.
- The fairness is pinned by tests (`tests/core/test_full_schema.py`,
  `tests/test_step10_fairness.py`): system messages byte-identical; user
  messages byte-identical once the tables and the join or keys section
  are cut out; the tables part exactly `render_tables` for the 24; the
  keys section exactly the graph's 107 keys; each of d4's tree edges the
  same line in both prompts.

## 5. The measures and the scoring rules

Each rule is held by a test on hand-written SQL in
`tests/core/test_eval_step10.py`, against questions of the original set
and hand-built held-out-shaped questions.

- **Table correctness, both modes.** The tables the validated SQL reads,
  as the frozen `extract_joins` lists them (FROM, CTEs, subqueries in
  WHERE and SELECT), equal the question's `tables`, plus exactly one of
  `tables_one_of` where it has any (Q8's "one channel", Q13's "a sales
  fact"). This is step 6's own `tables_as_expected`, applied to the SQL's
  tables. One table too many is wrong, as one too few is.
- **Path correctness, both modes.** The joins the SQL performs, read by
  the frozen `extract_joins`, compared by the frozen `compare` (by
  equivalence class, DD-13 as clarified) with the question's `joins`.
  Every choice of one alternative per `one_of` entry is compared: the
  path is correct when one choice CONFORMS (every expected join present,
  nothing foreign, no cross join, no half-joined key). Q14's two item
  keys fall into one class through the composite key; Q13's mixed-channel
  choices can never conform. A join to a table outside the expectation is
  foreign: diverged, wrong.
- **Declines.** Q15 and H9: correct on both measures if and only if the
  answer is a decline, whether retrieval declined (no anchors, or anchors
  that cannot be joined) or the model did. Any other question declined:
  wrong on both, and in both denominators.
- **Failures that leave no accepted SQL.** Validation failure (after
  retries) and model failure (a malformed reply after retries, a reply
  cut off by the output cap): wrong on both, in both denominators.
- **Execution failures are measured on their SQL** (the owner's ruling 1
  at the review of stop 2). An answer whose validated SQL fails at
  execution, a timeout or any database error, is scored on tables and
  path like any other: DD-20 measures the SQL, and a timeout depends on
  this machine and the scale factor, not on the method. The pilot's d5,
  a cross join that timed out, would be path wrong: diverged. Execution
  outcomes are reported beside the score, per set and mode, by failure
  word.
- **Transport and provider failures** are not the method's behaviour:
  the call is retried up to twice with backoff (5 and 20 seconds); if it
  still fails, the run is void (section 8).
- **The SQL is executed** in both modes, the same way. Recorded: the
  outcome, the failure word and SQLSTATE (never the database's message,
  item 78), the row count and truncated flag, the duration, and a sha256
  over the rows sorted into a canonical order (a capped result's hash
  covers only the rows returned). No value is kept (DR-15).
- **Retrieval recall and precision, retrieval mode only**, a diagnostic
  outside the headline: the tables shown to the model, and separately
  the tables of the selected tree, against the expected tables;
  `tables_one_of` is one slot, filled by any one of them, the others
  counted as surplus. Q15 and H9 have none. A retrieval decline counts as
  nothing shown.

Diagnostics outside the score, reported beside it:

- **Identical rows** (the owner's ruling 3 at the review of stop 2). For
  each question and run where both modes executed ok and neither result
  was truncated: whether the two rows hashes are equal. Counted per set,
  listed per question, question 14 left out. This compares the values
  returned, not their correctness, and a different column order counts as
  different. It shows where the joins agree and the numbers do not
  (finding 26, item 90).
- **Item 60.** Each answer records whether its SQL takes today's date
  (`current_date`, `now()`, `current_timestamp`) and whether its result
  was empty. Q1 asks about "last year"; the warehouse's sales end in
  January 2003. Empty results are a known limit, outside the score.
- **Item 61.** Semi-joins read as `not_checked`; their count by reason is
  reported per mode, so that a difference in how often each mode writes
  them is visible.

## 6. Denominators

The protocol's reading of Design section 12's sentence "questions whose
SQL returned not_checked drop out of the denominator", owed to the Design
as a clarification (the owner's ruling 5):

- `not_checked` leaves the **path** denominator. It is counted, by the
  instrument's reason.
- It leaves the **table** denominator too only where the reader could
  not resolve every table reference: an unread source in some FROM, or
  text that is not one SELECT. Counted, by reason.
- `diverged` wins over `not_checked` inside the frozen comparison, so a
  known wrong join is counted, never dropped.
- Declines and failures never leave a denominator.
- Each run's denominators are reported beside its counts.

## 7. Attribute joins

Item 66 was closed at stop 2 as not occurring as recorded: it expected two
summaries joined on one dimension attribute to read as diverged, and the
frozen comparison reads `item.i_category = item.i_category` as conforming,
since a column set equal to itself is implied by any path. In its place,
under their own name: an attribute join is any join condition between two
columns neither of which is a key. Attribute joins are counted beside the
verdict, in both modes, and never in a score.

## 8. Runs, order and combination

- **N = 5 complete runs**, fixed here and not changed after any result.
- **How the runs are made.** Each run is its own command:
  `python -m app.run_step10 --run K --head SHA`, K from 1 to 5, SHA the
  commit it runs on, read beforehand by its own `git rev-parse HEAD`.
  Before anything is opened or paid for, the runner refuses unless the
  manifest matches, runs 1 to K-1 each have a complete file and run K has
  none. A run that may outlast the session's command time limit runs in
  the background, with its output to a log, and the session waits for it
  to end; never two paid commands at once, and none chained to another.
- **Each complete run's files are committed as soon as the run ends**,
  in a commit of their own, before the next run starts. So the five runs
  may name different HEAD commits; the manifest check, recorded in each
  run file, shows that the code was the same.
- **The report is its own command**, `python -m app.run_step10 --report`,
  after the fifth complete run. It reads the kept files only, calls
  nothing that is paid, and never overwrites.
- **Order.** In each run the held-out set first, then the original, each
  in file order. For each question both modes, retrieval first in odd
  runs and full schema first in even ones.
- **A void run** (a transport or provider failure that outlasts its
  retries, a failure to embed, the model's spending ceiling) is recorded
  with its cause, in a file of its own (`run-0K-void-N.json`), and
  replaced by a complete fresh run of the same number, by running
  `--run K` again. Three void runs in a row stop the step, and so does a
  run stopped by the step cap. No run is ever voided, kept or replaced
  because of its results.
- **Per run:** each measure's count over its denominator, per set and
  mode.
- **Combined:** the mean of the five runs' fractions, with their range
  (lowest and highest). The headline is "the mean of five runs, with its
  range" (Charter criterion 6; a wording amendment is owed).
- **Per question:** correct in k of n measured runs, per measure and
  mode; stable when every run gave the same verdict; how many distinct
  SQL texts the runs produced.
- **The comparison:** question by question, which mode was right in more
  runs, per measure. Descriptive only; with 12 and 15 questions no
  significance is claimed.

## 9. Spend

- Measured in the pilot: a full-schema prompt is about 7,865 tokens, a
  retrieval prompt 776 to 3,028. Five runs of 28 questions in two modes
  are 280 answers. Uncached, about $0.13 in all; the provider's prompt
  cache cut the second pilot to a sixth of the first.
- **The step's cap: $0.30** on ledger purposes beginning `step10`, the
  pilots' $0.009359 included. Before every answer the runner checks that
  the spend so far plus that question's worst case (three uncached
  attempts at the output cap with the full-schema prompt: $0.004817)
  stays within it. If not, the run is void and nothing more is run.
- The model's own ceiling of $0.50 on the whole ledger stays as a
  backstop. Every embedding is on the ledger (item 89).
- **The cost of each mode is reported in input and output tokens, not
  dollars** (the owner's ruling 4 at stop 2): the provider's prompt cache
  makes dollars depend on the order of the runs (the second pilot cost a
  sixth of the first for the same prompts). Per set and mode: input and
  output tokens, in total and as a mean per answer, every answer of
  question 14 included. Dollars appear only in the spend lines, as what
  was paid.

## 10. What is recorded, and where

The committed, hashed files are the evaluation's record (the owner's
ruling 6; the Design's `eval_runs` table is owed an amendment). In
`backend/eval/step10/runs/`: one JSON file per run, `run-0K.json`, and
one per void run, `run-0K-void-N.json`, each made by its own `--run K`
command and recording the HEAD commit it ran on and the manifest check's
result (the five may name different commits; the manifest shows the code
was the same), with every answer whole (its retrieval, its prompt's hash
and size, every attempt with its SQL, tokens and cost, the model ID
returned, the extraction, every choice's comparison, the scores, the
execution's summary and rows hash); `prompts/` with each prompt exactly
as sent, by its sha256; and `report.txt`, made from those files alone by
its own command, `--report`, after the fifth complete run. The runner
creates every file exclusively: it never overwrites one, and stops
before any paid call if the run's file or the report already exists.

## 11. Predictions, written before any run

The original set's predictions rest on what is known of it: retrieval's
trees today miss an expected table for Q4 and Q7 (`date_dim`, item 68),
Q13 (`item`) and Q14 (`store_returns`, excluded). The held-out set's
rest on nothing but the system's known behaviour; its questions have not
been read.

1. **The headline will not favour retrieval.** On the held-out set,
   full-schema mode's table correctness will be at least retrieval's mean,
   and its path correctness at least retrieval's mean minus one question.
   The reason: a table retrieval does not bring cannot be recovered, and
   the model declines (item 62), which scores wrong on both; with every
   key in front of it, the model picks the keys retrieval's evidence and
   preferences would have picked, as it did on d4 and d7 in the pilot.
2. **The original set: retrieval loses Q4, Q7 and Q13 on tables** in
   every run, by a decline or by SQL without the missing table. Full
   schema gets each of the three right on tables in at least 3 of 5 runs.
3. **Retrieval declines more often than full schema** on each set, summed
   over the runs. Full schema declines nothing but Q15 and H9.
4. **Q15 and H9 are declined in both modes in at least 4 of 5 runs.**
5. **Where both modes answer a question, their joins agree** in most
   runs: on at least two thirds of such question-runs the two modes' path
   verdicts are the same.
6. **The joins are stable where the SQL text is not** (item 63): on each
   set and in each mode, at least 80% of questions have the same path
   verdict in all five runs, while at least a third of questions produce
   more than one distinct SQL text.
7. **`not_checked` is rare:** at most 2 path verdicts per mode per set
   per run.
8. **Retrieval recall is lower on the held-out set than on the original
   15**, which it was tuned against.
9. **No run is void**, and the step's spend stays under $0.10.

A null result, or one against retrieval, is a finding: at 24 tables,
with every key given, full-schema prompting is not handicapped (Design
section 12). It is reported as such, without softening.

## 12. The commitment

Whatever the runs say is reported, in this order: the held-out set's
figures for both modes, then the original set's, labelled as above, then
the diagnostics. No question is added, removed or rescored after this
commit; no run is added. The structural claim is stated as a property,
never as a result.

## 13. Faults found after this commit

Recorded in CHECKPOINTS.md with what they touched, and reported with the
result. Not fixed before the runs. A fault that makes a run's record
wrong (as against its result unwelcome) is reported, and the run stands
as recorded unless the owner rules otherwise, in writing, with the
reason.

If the harness itself stops a run before it completes (a crash, not a
result), the session stops and reports. Any fix is made only on the
owner's written ruling, tests first, in its own commit with a new
manifest, recorded in CHECKPOINTS.md; completed runs stand, and the
stopped run is replaced by a complete fresh run of the same number.

If a fault is found in the scoring after the runs, the kept SQL may be
scored again only on the owner's written ruling; both the original and
the corrected figures are then reported, with the reason.
