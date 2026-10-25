# Cartograph

Natural-language querying of complex data warehouses with schema-graph retrieval and explainable join-path selection.

## Evaluation

Cartograph was measured against a fair baseline: the same model (gpt-6-luna, temperature 0), given all 24 tables of the warehouse and all 107 keys between them, with no plan. The two prompts differ only in what Cartograph adds, and tests pin that. A frozen judge scores each answer's SQL on two counts: whether it read the tables the question needs, and whether it joined them along the expected path. Where a path cannot be checked, such as a join on a computed key, the answer leaves the path count. The protocol, the code's hashes and the predictions were committed before any run, and each mode ran five times.

The headline set, heldout2, is 12 questions frozen before linking and choosing were built. Neither the design nor the code was made with sight of them: only the evaluation code opens the file.

| heldout2, mean of 5 runs | Right tables | Right path |
|---|---|---|
| **Cartograph v2.1: linking and choosing (adopted)** | **100.0%** | **98.3%** |
| Cartograph v2: linking | 91.7% | 80.0% |
| Full schema (the baseline) | 100.0% | 100.0% |
| Cartograph v1: retrieval | 36.7% | 30.0% |

- v1's weakness was finding the tables: on heldout2, the tables retrieval chose held 67.6% of those the questions need. Linking, a model shown every table and asked to name the ones needed, named all of them.
- v2.1 matches the baseline on tables, and misses it on path by one answer in sixty.
- What Cartograph adds is not accuracy but an account: every join choice says how it was made (by a rule, by the model reading the question, or with no basis), and a choice with no basis is flagged.
- On the original set, used throughout development (15 questions scored), v2.1 found the right tables more often than the baseline (84.0% against 77.3%) and the right path less often (77.1% against 82.9%).
- The first held-out set (12 questions) was spent at step 10, and its failures there shaped v2, so it has been a development set since. On it, v2.1 and the baseline both found the right tables and the right path 91.7% of the time.
- dev2 (12 questions) was seen during development, and its pilot results helped shape v2.1. On it, v2.1 scored 100.0% on tables and 91.7% on path; the baseline, 100.0% and 100.0%.
- An answer from v2.1 averages 2.17 model calls and 9,194 input tokens on heldout2, with a median of 5.4 seconds and $0.000973 uncached. The whole of step 10b's evaluation cost $0.280944.

Limits: the sets are small (12 and 15 questions), so one question is a large step; one model; the judge checks tables and path, not the rows returned; one warehouse, TPC-DS.

The protocol is backend/eval/step10b_protocol.md, the runs and the report are in backend/eval/step10b/runs/, and the working record is CHECKPOINTS.md.

### The first evaluation: v1, 18 October 2026

*Kept as written then. Here "Cartograph" means v1, retrieval, and "held-out" is the first held-out set.*


Measured on 18 October 2026, by a protocol committed before any run (`backend/eval/step10_protocol.md`). Two modes, with the same model (gpt-6-luna, temperature 0) and the same rules: Cartograph's pipeline, and full-schema prompting, which gives the model all 24 tables and all 107 keys between them, with no plan. Each question was asked five times in each mode. What is measured is whether the generated SQL read the expected tables and joined along the expected path, not whether its numbers were right.

| | Tables | Path |
|---|---|---|
| Held-out, 12 questions never run before: Cartograph | 20% (17–25) | 20% (17–25) |
| Held-out: full schema | 90% (83–100) | 90% (83–100) |
| Development, the 15 questions the system was tuned against: Cartograph | 65% (60–67) | 67% (64–71) |
| Development: full schema | 79% (73–80) | 84% (79–86) |

Means of five runs, with their range in brackets. A sixteenth development question was deleted from scoring before the runs; the protocol gives the reason.

At 24 tables the whole schema fits easily in the model's context, and full-schema prompting wins, as the protocol predicted before the runs. The cause is retrieval. For 9 of the 11 answerable held-out questions it did not bring a table the question needed (the date table, for a year, in 5 of them), and the model, told to use only the tables it was given, declined rather than guess. Retrieval found 64% of the tables the held-out questions needed, against 92% on the questions it was tuned on. Where it brought every table, the two modes scored the same on 9 of 13 questions.

These figures do not measure Cartograph's other claim, which is a property rather than a result: it says when a join choice had no basis, and it checks that the SQL that ran joined the way its explanation says.

## Running it locally

Requires Docker Desktop with the WSL2 backend.

    ./scripts/bootstrap.sh
    docker compose up

`bootstrap.sh` writes a `.env` with generated passwords if one does not
already exist, and never overwrites an existing file. The repository
contains no credentials of any kind, in the working tree or in its
history, so this step is what makes `docker compose up` work on a clean
clone.

Once up:

- Frontend: http://localhost:3000
- API: http://127.0.0.1:8000/api/v1/health and /api/v1/ready

## Loading the warehouse

The stack comes up with an empty warehouse. One command builds it:

    ./scripts/warehouse.sh          # TPC-DS at scale factor 1, about five minutes
    ./scripts/warehouse.sh 0.01     # the same mechanism in thirty seconds

It needs the `duckdb` CLI and `python3` on the host. It generates TPC-DS
with DuckDB's `tpcds` extension, loads the 24 tables with `COPY`, adds the
17 primary keys, and applies the 102 foreign keys from the TPC toolkit's
`tpcds_ri.sql` — which is also parsed into `backend/overlays/tpcds.yaml`,
the source the schema graph is built from. Running it again drops and
rebuilds the warehouse tables; it never touches the application database.

`tpcds_ri.sql` belongs to the TPC and is not in this repository. The first
run downloads it with `curl` from a commit-pinned mirror of the toolkit and
checks its sha256; a failed download or a different file stops the build
before anything is changed. The overlay generated from it is committed.

At scale factor 1 the loaded warehouse measures 2.2 GB.

## Reading the schema

Once a warehouse is loaded, this prints what the application makes of it:

    docker compose exec backend python -m app.show_schema

It connects as the SELECT-only role, reads tables, columns and keys from
`pg_catalog`, merges the overlay (catalog first, with the source recorded
on every edge), and builds the schema graph: 24 tables and 425 columns as
nodes, 107 foreign keys as edges. 102 of those the database declares. The
other 5 are relationships the TPC's constraint file omits, including the
two-column keys that tie a return to its sale; they are asserted in the
overlay, marked `source: overlay`, and each carries a note saying what
evidence it rests on.

Column names such as `ss_ext_sales_price` are given readable names from the
overlay's `naming` section. That section is written by hand in
`backend/overlays/tpcds.naming.yaml`, and the hand-declared relationships
in `backend/overlays/tpcds.relationships.yaml`;
`backend/overlays/tpcds.yaml` is generated from them and never edited.
After changing either file:

    python3 backend/warehouse/ri.py write-overlay

## Finding join paths

    docker compose exec backend python -m app.show_paths catalog_sales customer_address

prints every route of up to three joins between two tables, the one
selected, and why. The rule is shortest first; a tie is broken by a
preference declared in the overlay if there is one, and alphabetically if
not. An alphabetical choice is reported as arbitrary, and the warning names
the routes that were not taken. A selected route that joins two "many"
sides through one table says so, and says what it does to the rows.
`--all` lists every alternative; `--max-joins 4` raises the limit.

## Retrieval: from a question to the tables that answer it

    docker compose exec backend python -m app.ingest_schema
    docker compose exec backend python -m app.show_retrieval "How much did each store sell last year?"

The first stores the warehouse's schema in the application database and
embeds it, once; it needs `OPENAI_API_KEY` in `.env`. The second puts one
question through retrieval: what its words matched, which tables became
anchors and which were set aside or cut, the tree that joins them and how
each was attached, every warning, and the close calls. A question that has
not been asked before is embedded, which is a paid call of a few dozen
tokens; the last line says what the run cost.

    docker compose exec backend python -m app.show_eval --step6

runs the sixteen questions of the frozen evaluation set
(`backend/eval/questions.yaml`) and reports where retrieval agrees with
them and where it does not. At the settings in use it holds every expected
table for 11 of 15 questions and brings 34 tables too many; it agrees
strictly with none. `CHECKPOINTS.md` has the whole record.

## Asking a question

    docker compose exec backend python -m app.ask "How do purchases on the web split by the buyer's level of education?"

puts one question through the whole pipeline and prints its answer and its
trace: the tables a language model, shown every table, named for it; each
choice the rules left open and what the model named for it; the tables
the model writing the query was shown and the joins selected for it, each
attempt, the SQL that ran, the joins that SQL really made beside the ones
selected, and the first rows. It makes two or three paid calls to the
model (`gpt-6-luna`): naming the tables, choosing when the rules leave a
choice open, and writing the SQL. Together they cost about a tenth of a
cent, written to a spend ledger; the last line gives the running total.

What the pipeline guarantees, and what it only reports:

- The SQL is parsed and refused unless it is one read-only SELECT over
  tables and columns the schema holds. A write, or a function known for a
  side effect, ends the question; the model is not asked again. The
  warehouse role could not write in any case.
- The SQL that runs is byte for byte the SQL that was checked.
- The joins the SQL makes are read out of it and compared with the joins
  selected. If they differ the query still runs and the trace says
  **diverged**, naming the joins. A model that takes another route is
  reported, not corrected.
- When the model, shown every table, names none that holds the answer,
  the question ends there, and the reply is "This cannot be answered from
  this schema: shown every table, the model named none that holds the
  answer." No query is written.
- When the model writing the query says the tables it was shown do not
  hold the answer, the reply is "This cannot be answered from the tables
  found for it: the model writing the query, shown those tables, said
  they do not hold the answer." That is a statement about the tables it
  was shown, not about the warehouse.

The trace is stored, as the API stores it: the query's id is printed, and
`GET /api/v1/queries/<id>` returns the trace with its plain-English
account. The rows are not stored.
