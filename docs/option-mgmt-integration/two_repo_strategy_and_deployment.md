# Two-repo strategy — paths forward, deployment & UX

*How `yearline-universe-engine` (this repo) and
[`option-mgmt-2026`](https://github.com/phiphi325/option-mgmt-2026) should coexist and evolve. A
decision doc for a **solo maintainer**, building on `assessment.md` + `integration_design_and_plan.md`.
Educational research; not financial advice; neither system executes trades.*

> **Premise (as of today):** two repos, and **no real user-facing frontend on either** yet
> (option-mgmt has Next.js *scaffolding* only; yearline is headless). That makes this the right moment
> to decide topology + deployment + UX *before* a frontend locks choices in.

---

## 1. The situation today

| | yearline-universe-engine | option-mgmt-2026 |
|---|---|---|
| Role | **Statistical-context provider** (MA250 repair/retry evidence) | **Decision engine** ("what should I do today?") |
| Stack | Python lib; **heavy** deps (pandas/numpy/scipy/scikit-learn); universe price cache; notebook + JSON exports | Monorepo: Next.js 16 web + FastAPI api + **lean pure-Python** engine; Postgres; Pydantic→TS codegen |
| Frontend | none (headless) | scaffolding only — **no real UI yet** |
| Releases | phase-based (Phases 1–7) | milestone + engine-version (semver, CI-enforced bump) |
| Data | universe OHLCV cache + **proprietary `docs/uploaded`** (gitignored) | user CSV / ingestion → Postgres |
| Shared DNA | educational, **no execution**, deterministic, auditable, disclaimer-gated | same |

The two are **deliberately different kinds of software**: yearline is a compute-heavy, data-hungry
*research/analytics* engine; option-mgmt is a *lean, audited decision service*. That difference is the
single biggest input to the topology decision.

## 2. Repo topology — the paths forward

### Option 1 — Two repos, loose coupling via a **persisted, versioned artifact** ✅ recommended (start here)
yearline runs on its own schedule and **publishes its envelope to a store**; option-mgmt **reads** it.
No shared code.

- **Pros:** loosest coupling; each repo keeps its own stack/CI/release cadence; yearline stays reusable
  beyond option-mgmt; smallest blast radius; **most days you work in one repo only**; respects the
  pure-engine boundary by construction (no import path exists).
- **Cons:** a versioned **contract** to manage; potential schema drift (mitigated by a contract test);
  two deploy pipelines; the handoff store is one more moving part.

### Option 2 — Two repos, yearline as a **pinned Python package dependency** (jobs layer only)
option-mgmt's **ingestion job** `pip`-installs a pinned `yearline-universe` and calls its adapter
in-process; the **pure engine still never imports it**.

- **Pros:** clean dependency semantics + versioning (PyPI/private index/git tag); no separate store;
  single source of the code.
- **Cons:** heavy data-science deps land in the jobs environment (bigger image, slower CI for that
  service); publishing/versioning overhead; tighter lifecycle coupling than Option 1.

### Option 3 — **Monorepo merge** (fold yearline into option-mgmt) ❌ not recommended
Add yearline as `packages/yearline` or `apps/jobs/yearline`.

- **Pros:** one repo, one issue tracker, atomic cross-cutting changes, shared tooling.
- **Cons (decisive):** drags **heavy deps + the universe price cache + proprietary `docs/uploaded`**
  into the lean engine repo; strains its **pure-engine / lean-deps ethos** (ADR-0005) and CI (coverage,
  engine-version-bump, no-broker guards) even if isolated; **kills yearline's standalone reuse**;
  conflates two very different release disciplines; larger, slower repo. The whole integration analysis
  hinges on keeping yearline *out* of the engine — a merge fights that.

### Option 4 — **Git submodule / subtree** ❌ not recommended
- **Pros:** shared code without publishing.
- **Cons:** submodule UX is famously error-prone (detached HEADs, double commits); poor solo-maintainer
  ergonomics; subtree hides history. Little upside over Options 1/2.

### Recommendation
**Keep two repos.** Start with **Option 1** (artifact handoff — loosest, fastest, safest), and graduate
to **Option 2** only if you later want in-process freshness without a store. **Never** Option 3/4. This
is exactly the boundary the integration analysis established: yearline is an upstream *producer*, never
a dependency of the pure engine.

## 3. Deployment

### yearline — a scheduled **batch**, not a service
It has no request path; it runs nightly, pools the universe, and emits the MSFT envelope.

- **Where:** simplest for a solo maintainer → a **GitHub Actions scheduled workflow** in this repo
  (no infra to run; already on GitHub). Alternatives as scale grows: a Cloud Run / Fly **scheduled job**
  (container). It needs a fresh price cache → the nightly job also does the data pull (yearline already
  has incremental-cache + staleness flags).
- **Handoff store (where the envelope lands):**

  | Store | Pros | Cons |
  |---|---|---|
  | **Object storage** (S3/GCS/R2) JSON blob | immutable, versioned, clean decoupling; cheap | one more service + auth; option-mgmt needs a pull step |
  | **GitHub release asset / a small data branch** | dead simple, git-auditable, **zero new infra** | git churn; only good for a small daily JSON |
  | **Row in option-mgmt's Neon Postgres** | single source of truth; transactional; option-mgmt already uses it | yearline needs write creds into option-mgmt's DB (coupling); schema coordination |

  **Recommendation:** begin with **object storage or a GitHub release asset** (loosest, no shared DB);
  move to the **Neon table** once option-mgmt's ingestion job + `yearline_context` table exist (then it's
  just "the latest row," matching `inputs_hydration_service.py`).

- **When to enable the schedule (don't cron too early).** The producer is **ready** to schedule (pins
  frozen; graceful `is_stale` / `available:false` abstention), but a daily artifact with **no consumer is
  noise.** Sequence: **OM-Y1** (contract pinned) → **OM-Y2** (ingest + persist) → *then* a cron. **Ship it
  `workflow_dispatch`-first** (manual `as_of` runs, no cron); flip to `schedule:` only once OM-Y2 ingests it
  **and** a contract test validates the artifact in CI (this repo has **no CI yet** — that's a
  prerequisite). Gate the cron behind: **idempotent publish** (key by `{ticker}_{as_of}`),
  **market-calendar awareness** (no-new-bar ⇒ `available:false`, not a half build), **secrets + cost** (the
  pooled-universe data pull), and the rule that the **nightly job never bumps
  `adapter_version`/`series_version`** (schedule = *data* freshness only). A ready-to-activate, **inert**
  template lives at `docs/phased_design/phase_09/ci/yearline_nightly.yml`; the full checklist is
  `phase_09/option_mgmt_handoff.md` §10.

### option-mgmt — already specced
web → **Vercel**; api → **Fly.io**; db → **Neon Postgres**; `docker-compose` for local; Redis cache in
Phase 2. Nothing here changes for the integration except adding the **ingest step** that reads yearline's
artifact and an optional **evidence panel** on the Today screen.

### Solo-maintainer guidance
Minimise moving parts: **one nightly GitHub Action in yearline → one small artifact → one ingest step in
option-mgmt.** Everything else (the Neon table, Redis, Cloud Run) is an optional later optimisation.

## 4. User experience — **one app, not two**

Because there is **no frontend on either repo yet**, this is a clean-slate choice — and the answer is
clear:

- **Build a single user-facing app: option-mgmt's Today screen.** It already has the API + the
  scaffolding + the "single `DailyDecision`" product thesis. yearline is **surfaced inside it** as an
  **evidence panel** (integration Option C): the repair/trend state, gated `P(retry ≤ H)`, the
  days-to-touch range — with its **trust-gate state and staleness shown honestly**, and the standing
  disclaimer / `must_not_auto_execute` framing preserved.
- **Keep yearline headless.** Its "UX" is notebooks + JSON exports for research/inspection. Do **not**
  build a second consumer-facing frontend.
- **Optional later:** a lightweight **read-only research view** for the maintainer to inspect the
  universe/calibration/pooling (or keep using notebooks). This is a *researcher* tool, not the
  *end-user* experience, and can wait.

Why this matters now: building **two** frontends would double the surface, split the user's attention
("which app do I open?"), and duplicate the disclaimer/auth/design system. One app with yearline as a
section is simpler to build, maintain, and reason about — and matches option-mgmt's single-output
("one `DailyDecision`") principle.

## 5. Maintenance model (how to actually run two repos)

- **Contract ownership.** yearline owns the envelope schema (`schema_version` + `model_stack_version`);
  option-mgmt owns the consumer `YearlineContext` model and **pins an accepted version range**. A
  **contract test on both sides** (a shared fixture envelope) catches drift in CI; keep a small
  **compatibility matrix** (which yearline versions which option-mgmt accepts).
- **Independent release cadence.** yearline ships by phase; option-mgmt by milestone/engine-version.
  They coordinate **only at the contract boundary** — a yearline release that doesn't change the
  consumed subset needs *no* option-mgmt change.
- **CI/CD.** Each repo keeps its own pipeline; add **one cross-repo contract test** (option-mgmt CI
  validates the latest yearline fixture against `YearlineContext`). yearline's nightly Action is its
  "deploy."
- **Versioning & replay.** option-mgmt folds yearline's version + a consumed-field hash into its
  `inputs_hash` (a 4th replay pin) so a yearline-influenced decision stays replayable.
- **Docs & tracking.** Per-repo docs stay put; `docs/option-mgmt-integration/` (this folder) is the
  shared reference — mirror a short pointer in option-mgmt's `docs/enhancements/`. Use labels or a single
  project board for cross-repo work.
- **Branch + squash + conventional commits** on both (option-mgmt mandates it; this repo follows it).
- **Solo ergonomics.** Loose coupling is the whole point: most changes touch exactly one repo; the
  boundary is small, versioned, and testable.

## 6. Decision matrix

Topology options scored for a solo maintainer (✅ strong · ◐ medium · ❌ weak):

| Criterion | 1. Artifact (loose) | 2. Package dep | 3. Monorepo merge | 4. Submodule |
|---|---|---|---|---|
| Separation of concerns | ✅ | ✅ | ❌ | ◐ |
| Keeps engine lean/pure (ADR-0005) | ✅ | ✅ | ❌ | ◐ |
| Coupling / coordination overhead | ◐ (contract) | ◐ | ✅ (atomic) | ❌ |
| Deploy simplicity | ✅ | ✅ | ◐ | ◐ |
| yearline reusable standalone | ✅ | ✅ | ❌ | ◐ |
| Blast radius of a change | ✅ | ◐ | ❌ | ◐ |
| Solo-maintainer ergonomics | ✅ | ◐ | ◐ | ❌ |
| Handles heavy deps + proprietary data | ✅ | ◐ | ❌ | ◐ |

**Verdict:** **two repos, Option 1 (artifact) now → Option 2 (package) if needed**, a **single frontend
(option-mgmt) with yearline as an embedded evidence panel**, yearline deployed as a **nightly batch**,
and a **versioned, contract-tested boundary**. Avoid the monorepo merge — it trades a little
coordination overhead for breaking the lean-engine boundary the whole design rests on.

## 7. Open questions for you

1. **Handoff store:** object storage / GitHub release asset (loosest) vs a Neon table (tightest)? (I'd
   start with the artifact.)
2. **Frontend timing:** build option-mgmt's Today screen first (with the yearline panel baked in), or
   wire the data path before any UI?
3. **Standalone yearline research UI:** ever wanted, or are notebooks enough?
4. **Hosting:** is the Vercel + Fly.io + Neon plan firm, and is a GitHub Actions nightly acceptable for
   yearline's batch?
5. **Cadence:** is once-nightly fresh enough for the overlay, or do you want intraday refresh later?

## 8. Bottom line

Keep them **two repos** with a **small, versioned, contract-tested seam**; deploy yearline as a
**nightly batch** that publishes one artifact; build **one frontend** (option-mgmt) and show yearline as
an **evidence panel** inside it. This keeps each system true to its nature — yearline the heavy research
engine, option-mgmt the lean audited decision service — while a thin, honest, gated contract carries the
value across. *Two engines, one screen, one contract.*
