# Checkpoints

One entry per deliverable boundary, written at the moment each was reached
rather than reconstructed afterwards. Three questions each: what works now
that did not before, what was built that is not yet understood well enough
to explain, and what the next single deliverable is.

---

## checkpoint-01-stack — 12 September 2026

**What works now that did not before.** Four services come up from one
`docker compose up` — Postgres with pgvector, Redis, FastAPI, Next.js —
and that claim is proven rather than assumed: a fresh clone of the public
repository, with no `.env`, no `node_modules` and no built images, reaches
SMOKE PASS in 67 seconds. A browser at localhost:3000 reads both databases
through separate credentials in a single request. CI is green on push.
NFR-07 is demonstrated by refusal, not asserted: the warehouse role is
denied a write, denied the application database, capped by
`statement_timeout` at the role, and read-only by default transaction.

**To dissect.** Built with assistance and used correctly, but not yet
understood well enough to defend under questioning:
- the application factory, and why `Depends(get_settings)` plus
  `dependency_overrides` interact the way they do
- the anonymous-volume mechanism that makes `node_modules` resolve to the
  image's copy rather than the bind mount underneath it
- `ALTER DEFAULT PRIVILEGES`, and why it grants on tables that do not exist
- compose healthchecks with `depends_on: condition: service_healthy`, and
  what ordering guarantee that actually gives
- Next 16's generated route types, and why `tsc` alone cannot check them

**Next single deliverable.** Step 2: TPC-DS generated via DuckDB, loaded
into Postgres, `tpcds_ri.sql` applied, and a test asserting the expected
edge count and the expected multi-path pairs — T-01 and R-09, the highest
risk item in the project. Plus measuring the loaded database size against
Neon's 0.5 GB ceiling.

### Carried forward

Deliberate deferrals, recorded while the reasoning is fresh:

1. The backend connects to the application store as a **superuser**; DD-02
   says "ordinary read-write". Revisit at step 8 — a role can be added to a
   live database, so this is not a now-or-never decision.
2. `statement_timeout = 30s` on the warehouse role is a guess with no
   measurement behind it. Revise on evidence.
3. The default `postgres` maintenance database is still reachable by
   `cartograph_ro`. The accurate claim is "the warehouse plus an empty
   maintenance database", not "the warehouse only".
4. Day Zero's "roughly 90 KB/s" is wrong by about tenfold — measured closer
   to 1 MB/s. Correct that document.
5. Frontend `node_modules` exists twice on purpose: the image builds its own
   from the lockfile, the host copy serves the editor. **Any dependency
   change requires rebuilding the frontend image**, and the editor will not
   warn you.
6. `eslint@9.39.5` arrives flagged as unsupported, via `eslint-config-next`.
7. Starlette now wants `httpx2` rather than `httpx` for its test client.
8. Neither Dockerfile uses a non-root user. The backend writes root-owned
   `.pytest_cache` and `__pycache__` into the repository — gitignored, so
   harmless today, but it is why NFR-24 wants this fixed at step 12.
9. The backend image carries `pytest` and `httpx` into the runtime.
10. `NEXT_PUBLIC_API_BASE_URL` is read at runtime by `next dev` but inlined
    at build time by `next build`. Vercel needs it as a build variable.
11. GitHub Actions raises Node 20 deprecation annotations for
    `checkout@v4`, `setup-python@v5` and `setup-node@v4`. Bump the majors.
12. **Every** `docker compose` command needs `.env`, not only `up` — the
    `${VAR:?}` guards apply to `down`, `ps` and `config` too. So
    `bootstrap.sh` runs before any compose command.
13. The smoke check is deliberately absent from CI. It needs a cold build
    and four services on a shared runner, and a flaky pipeline is worse
    than none. It earns its minutes at step 7, when it asserts the pipeline
    rather than liveness.
14. Free-tier Postgres is 0.5 GB (Neon). Scale factor 1 will not fit;
    plan a reduced factor for the deployed instance and measure the loaded
    size rather than estimating it.
