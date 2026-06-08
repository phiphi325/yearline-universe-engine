# Integrating yearline-universe into option-mgmt-2026 — analysis & plan

How the **yearline-universe statistical-context engine** (this repo) should feed the
**`option-mgmt-2026` MSFT option risk-management engine**
([github.com/phiphi325/option-mgmt-2026](https://github.com/phiphi325/option-mgmt-2026)).

> Both systems are **educational decision-support, not financial advice**, and **neither
> executes trades**. This folder is analysis + plan only — no code in either repo is changed by it.

## What each side is (one line each)

- **option-mgmt-2026** — an engine-first, audit-trail, *no-execution* decision engine that answers
  "what should a long-term MSFT holder do today?" via a pure-Python pipeline (Market State → Flow
  Score → Recommendation → Strike Selector → Execution → Confidence → `DailyDecision`), surfaced over
  a FastAPI + Next.js "Today screen." Engine `1.6.0`, ~840 tests.
- **yearline-universe** — a ticker-agnostic, universe-first **MA250/yearline repair-trend statistical
  context** engine that emits a versioned `SingleTickerStatisticalContextEnvelope` (repair/retry
  hazard, calibrated `P(retry ≤ H)` + a gated classifier blend, conditional days-to-touch,
  post-confirmation trend) as an **evidence overlay** flagged `must_not_auto_execute: true`.

## The thesis (why this fits)

option-mgmt's Market State is **short-horizon** (IV regime, dealer-gamma flow, ADX, breakout, event
proximity, max-pain). yearline supplies a **complementary, medium-horizon, structural axis the engine
does not currently model**: *is MSFT in a yearline repair, how likely/soon is the retouch, is it a
high- or low-readiness repair, is it in a post-confirmation uptrend?* That is exactly the context that
should shape an options overlay — a still-falling repair argues defensive/collar; the days-to-touch
range informs expiry/DTE; the gated retry probability informs directional bias. The two engines are
**orthogonal time scales**, not redundant.

And the cultural fit is unusually strong: both are **engine-first, deterministic, auditable,
no-execution, disclaimer-gated**, and both share a **"deterministic V1 → gated ML upgrade"** discipline
(option-mgmt's "ML replaces a node without changing the interface"; yearline's "empirical estimator =
baseline, classifier blend = the gated ML upgrade").

## The crux — respect the pure-engine boundary

`packages/engine` is **pure-function Python, no I/O, no heavy deps** (ADR-0005, CI-enforced;
`check_no_broker_imports.sh`). yearline is **heavy** (pandas/numpy/scipy/scikit-learn) and **does I/O**
(price cache, universe pooling). So yearline **cannot be a dependency of `packages/engine`.** It
doesn't need to be: option-mgmt already computes heavy upstream objects (`ChainSnapshot`,
`MarketStateResult`, `FlowScore`) **outside** the engine and passes them **in** as validated value
objects. **yearline fits that exact slot** — it runs in the **ingestion/jobs layer**, persists its
envelope, and the engine consumes a lightweight, Pydantic-validated **`YearlineContext`** value object.

## Recommendation in one paragraph

Treat yearline as an **external statistical-context provider** delivered via a **persisted, versioned
artifact** (loose coupling), consumed by the engine as a `YearlineContext` value object — never as a
library import into the pure engine. Start with the **lowest-coupling, highest-speed** increment (a
**read-only evidence panel** on the Today screen, no decision change), then graduate to **gated engine
consumption** (new `rules.yaml` clauses + a confidence component used **only where yearline's trust
gate passes**), with the yearline contract version folded into option-mgmt's replay hash. This is also
the natural home for yearline's already-planned **V13.8 "repo-integration adapter."**

## Reports in this folder

| Doc | What it covers |
|---|---|
| [assessment.md](assessment.md) | The deep assessment: what option-mgmt is (architecture, invariants, replay model), what yearline emits, **what yearline adds that option-mgmt lacks**, the **boundary analysis** (why not a library import; where it runs), the **contract / determinism / gate-respect**, the ML-node-swap alignment, and a fit/risk scorecard. |
| [integration_design_and_plan.md](integration_design_and_plan.md) | The plan: the `YearlineContext` value object, ingestion/persistence, the replay-hash extension, three coupling options (A engine-input / B market-state / C read-only panel), a **phased PR roadmap** (OM-Y0…Y5 on the option-mgmt side + the yearline-side V13.8 adapter), acceptance gates, risks, and open questions for you. |
| [two_repo_strategy_and_deployment.md](two_repo_strategy_and_deployment.md) | The zoom-out: **how to maintain two repos** going forward. Repo-topology paths (two-repos-loose-coupling vs pinned-package vs monorepo-merge vs submodule, with pros/cons + a decision matrix), **deployment** (yearline as a nightly batch + handoff-store options; option-mgmt web/api/db), and **UX** — given no frontend yet on either, build **one** app (option-mgmt's Today screen) with yearline as an embedded evidence panel and keep yearline headless. Plus a maintenance model (contract ownership, versioning, contract tests, solo-maintainer ergonomics). |

Nothing here is built; it is the assessment + plan for your review.
