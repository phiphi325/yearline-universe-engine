# Tutorial — Automating a two-repo ML/analytics pipeline with GitHub Actions

**Case study:** `yearline-universe-engine` (this repo, the *producer*) ⇄ `option-mgmt-2026` (the *consumer*).
**Audience:** a solo maintainer wiring continuous integration + a scheduled data job across two repositories.
**You'll learn:** the two workflow archetypes (validate-on-change vs. produce-on-schedule), how to make CI
reproducible, how to gate a scheduled publisher safely, and the **real gotchas** we hit doing exactly this
(permissions, OOM, data-source IP blocking, contract drift).

> Educational research only; not financial advice. Nothing here executes trades; every artifact carries
> `must_not_auto_execute: true`.

---

## 0. The shape of the problem

Two repos that meet only at a **versioned JSON contract** (see
[`../option-mgmt-integration/two_repo_strategy_and_deployment.md`](../option-mgmt-integration/two_repo_strategy_and_deployment.md)):

```
yearline (producer, heavy, I/O)                 option-mgmt (consumer, lean pure engine)
  nightly batch ──► YearlineContext.json ──►  jobs layer ingests ──► engine reads a value object
  (+ YearlineTrendSeries.json for the plot)      (never imports yearline)
```

Automation has **two jobs**, and conflating them is the first mistake:

| Workflow | Trigger | Question it answers | This repo |
|---|---|---|---|
| **CI** | every push / PR | "Is the code + the contract still correct?" | `.github/workflows/ci.yml` |
| **Producer (nightly)** | schedule / manual | "Refresh today's data and publish the artifact." | `.github/workflows/yearline_nightly.yml` |

CI must be **fast, deterministic, and offline**. The producer is **slow, networked, and stateful**. Keep them
in separate workflow files with separate triggers.

---

## 1. CI: validate on every change

### 1.1 The workflow
```yaml
name: ci
on:
  push:
    branches: ["**"]
  pull_request:
permissions:
  contents: read
concurrency:
  group: ci-${{ github.ref }}
  cancel-in-progress: true        # supersede stale runs on the same ref
jobs:
  test:
    runs-on: ubuntu-latest
    timeout-minutes: 30
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.9", cache: pip }
      - name: Install
        run: |
          python -m pip install --upgrade pip
          pip install -e ".[dev,viz]"   # base deps + pytest + matplotlib; live providers NOT needed
          pip install jsonschema
      - name: Test suite (process-isolated; OOM-safe)
        run: bash scripts/run_tests.sh
      - name: Contract test — fixtures conform to the pinned schemas
        run: python scripts/validate_contract_fixtures.py
```

### 1.2 Lesson — make CI *reproducible*, which means **vendor the test data**
Several tests spin up the real-data universe pipeline. They read a committed price cache
(`data/price_cache/{TICKER}.csv`) and config (`config/*.yaml`) — **no network** (`provider="cache"`). A fresh
CI checkout has no local state, so anything a test reads **must be in the repo**:

- We confirmed `.gitignore` allows the cache (`data/replay_state/` and backups are ignored, the CSVs are not),
  and **vendored the 9 universe CSVs + 3 config files** in the same PR that added CI. Now CI runs against the
  *exact* bytes validated locally.
- Runtime-generated dirs (`data/replay_state/`) are created on demand (`mkdir(parents=True, exist_ok=True)`)
  and tests write them under `tmp_path`, so their absence on a clean checkout is fine.

> **Rule of thumb:** if a test needs a file, either commit it or generate it in `tmp_path`. "It works on my
> machine because the cache is sitting there" is the #1 reason a green local suite goes red in CI.

### 1.3 Lesson — single-process `pytest` can OOM; isolate per file
The suite passed locally in one process (107 tests, ~7 min) — but the heavy real-data tests hold large pandas
frames, and on a memory-constrained runner the cumulative footprint can OOM (SIGKILL / exit 137). The repo
ships `scripts/run_tests.sh`, which runs **one file per process** so memory is reclaimed between files:

```bash
for f in tests/test_*.py; do
  python3 -m pytest -q -p no:cacheprovider "$f" || fail=1
done
[ "$fail" -eq 0 ]   # the script's exit code is the CI signal
```
CI calls that script instead of bare `pytest`. (On a fat machine plain `pytest` is fine; per-file is the safe
default for CI runners and sandboxes.)

### 1.4 Lesson — a **contract test** is the cheapest cross-repo insurance
The producer and consumer agree on a versioned JSON shape. `scripts/validate_contract_fixtures.py` validates
every committed fixture against the **in-code** JSON schemas + version pins (`ADAPTER_VERSION`,
`TREND_SERIES_VERSION`) **and** asserts the committed `*_schema.json` files haven't drifted from the code. The
consumer (`option-mgmt-2026`, OM-Y1) runs the *same* fixtures against its Pydantic model. Same bytes, both
sides, CI on both — drift is caught the day it's introduced, not in production.

---

## 2. The producer: produce on a schedule

### 2.1 The workflow (manual-first)
```yaml
name: yearline-nightly
on:
  workflow_dispatch:               # manual: run a specific as_of on demand
    inputs:
      as_of: { description: "YYYY-MM-DD (blank = latest bar)", required: false, type: string }
  # schedule:                      # ENABLE ONLY when prerequisites are met (see §2.3)
  #   - cron: "30 6 * * 2-6"       # ~01:30 ET Tue–Sat (after US close + data settle), in UTC
permissions: { contents: read }
concurrency: { group: yearline-nightly, cancel-in-progress: false }
jobs:
  produce:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.9", cache: pip }
      - run: pip install -e ".[live]" jsonschema     # live = yfinance + curl_cffi
      - name: Produce
        env:
          TIINGO_API_KEY: ${{ secrets.TIINGO_API_KEY }}    # the cron's keyed provider (V13.9; see §2.2)
        run: |
          ARGS="--provider tiingo --out exports/yearline_context"
          if [ -n "${{ inputs.as_of }}" ]; then ARGS="$ARGS --as-of ${{ inputs.as_of }}"; fi
          python scripts/run_nightly.py $ARGS
      - name: Validate before publishing
        run: python scripts/validate_contract_fixtures.py   # don't poison the feed
      - uses: actions/upload-artifact@v4
        with: { name: yearline-context, path: exports/yearline_context/*.json, if-no-files-found: error }
```
(Note: pass `--as-of` only when provided — `run_nightly.py` defaults to today's last completed session; a
literal `"latest"` would fail date parsing.)

The shape is always **produce → validate → publish**. The validate step is the same contract test from CI,
reused as a pre-publish gate: a malformed artifact fails the run *before* anyone downstream ingests it.

### 2.2 Where the data comes from (keyless by default; the cron uses a keyed provider)
The **default + CI** path is keyless: `load_price_data(provider="auto")` walks
`cache → tiingo → yfinance → yahoo_chart` (`src/yearline_universe/data_loader.py`), and with no key the
`tiingo` step no-ops:

1. `cache` — committed CSVs (offline; what **CI** uses — **no secret**).
2. `yfinance` — `yf.download(...)`, auto-adjusted Yahoo bars. **Free, no key.**
3. `yahoo_chart` — Yahoo's v8 chart HTTPS endpoint via `curl_cffi`/`requests`. **Free, no key.**

So CI needs **no secret** (a `DATA_API_KEY` placeholder we briefly carried was simply wrong). But the
**nightly cron** is the exception, and here's the teaching beat:

> **The caveat that forced a real decision — cloud-runner IP blocking.** Yahoo throttles/blocks **datacenter
> IPs**, and GitHub-hosted runners share them, so hosted live pulls intermittently 404/429/return empty.
> Escalating fixes: (1) the built-in `yfinance → yahoo_chart` fallback + `curl_cffi` impersonation; (2)
> **retry/backoff** (`run_nightly.py` has it); (3) a **self-hosted runner** (your own IP); (4) a **keyed
> provider** — **which is what we did.**
>
> **Resolution (V13.9): the cron uses Tiingo** (`--provider tiingo`, keyed via the `TIINGO_API_KEY` secret) —
> an authenticated API that isn't IP-scraped, with **adjusted** EOD on its free tier (chosen over Alpha
> Vantage premium). So a `*_API_KEY` *does* enter — but only for the **cron**, never for CI. Adjusted-close
> matters: a provider swap must pass `scripts/parity_check.py` (vs the committed cache) before you trust it.

For the full paid-provider build + migration guide (drop-in provider code, the **adjusted-close caveat**,
key-as-secret wiring, a vendor comparison, and a **parity check**), see
[`../reference/data_providers.md`](../reference/data_providers.md).

### 2.3 Lesson — gate the *schedule* on readiness, not a date
A daily artifact with **no consumer is just noise**. We kept the nightly on `workflow_dispatch` (manual) and
only plan to uncomment `schedule:` once:

- **The consumer can ingest it** — `option-mgmt-2026` OM-Y2 (ingest + persist + hydrate) is live (verified on
  Postgres). ✅
- **CI validates the artifact** — `ci.yml` + the contract test are in. ✅
- **A `scripts/run_nightly.py` entrypoint exists** — the one remaining operational TODO.

Order matters: **OM-Y1 (contract pinned) → OM-Y2 (ingest) → CI → then `schedule:`.**

### 2.4 Lesson — three invariants every scheduled publisher needs
1. **Idempotent publish** — key artifacts by `{ticker}_{as_of}` so a re-run overwrites cleanly; no duplicate
   rows downstream.
2. **Market-calendar awareness** — on weekends/holidays/no-new-bar, emit `available:false` or a `is_stale`
   envelope, **not** a half-built one. (The contract already has graceful-abstention shapes for this.)
3. **The schedule changes *data*, never the *contract*.** The nightly job must **never** bump
   `adapter_version` / `series_version` — those are reviewed PRs with a coordinated consumer-side pin bump. A
   cron silently changing a contract is how you break a downstream at 2am.

---

## 3. The gotcha that will bite you first: the `workflows` permission

When we pushed the two workflow files through the platform's GitHub integration, the API returned:

```
403 Resource not accessible by integration   (POST /repos/.../git/trees)
```

Everything *else* in the same push (code, docs, the vendored data) succeeded. The cause: **creating or
modifying any file under `.github/workflows/` requires the `workflows` permission**, which the integration
token didn't have. GitHub rejects the *entire tree* if it contains a workflow file the token can't write.

**How to handle it:**
- **Split the push:** commit everything except `.github/workflows/*` via the integration (this lands the
  validator, docs, data), then add the two workflow files with a credential that *has* the permission.
- **Add the workflow files with your own account** (a human push, or a PAT/clone with `workflow` scope) — what
  we did here. The branch picks them up and the PR completes.
- **Or grant the app `Workflows: write`** if the app's permissions are yours to edit (often they're not, for a
  platform-managed app).

> **Takeaway:** treat `.github/workflows/` as a privileged path. Automation that writes code may *not* be able
> to write the automation itself — design for a human (or a scoped token) to land workflow files.

---

## 4. Verifying it works

The repo is public, so the first runs are visible on the **Actions** tab without auth. After the merge we
confirmed:
- `ci` ran on the PR **and** on the push to `main`, installed, ran the per-file suite (107 tests) + the
  contract test, and went **green** on a clean runner against the vendored cache.
- `yearline-nightly` correctly **did not** trigger on push — it's `workflow_dispatch`-only until the
  `schedule:` block is uncommented.

If your CI integration can't read run status programmatically, the Actions web UI (or `gh run list` /
`gh run watch` locally) is the fallback. **Don't merge to a protected `main` before the first run is green** —
but note that on a brand-new CI there are no required-status-checks yet, so the first green is observational.

---

## 5. Sequencing checklist (copy this)

```
[ ] Contract pinned + fixtures committed (producer) and a contract test on BOTH sides
[ ] CI: offline, reproducible (vendored test data), OOM-safe runner, contract test as a gate
[ ] CI green on a clean runner (not just locally)
[ ] Consumer can ingest the artifact (don't schedule before this)
[ ] Producer workflow added (mind the `workflows` permission) — workflow_dispatch FIRST
[ ] Prove idempotency + market-calendar handling via manual dispatch / backfill
[ ] Decide data source: keyless (yfinance/Yahoo) vs paid (then add a secret); plan for cloud-IP blocking
[ ] Only now: uncomment `schedule:`; keep "never bump the contract in cron" as an invariant
```

---

## 6. Files referenced in this case study
- CI: `.github/workflows/ci.yml`
- Nightly producer: `.github/workflows/yearline_nightly.yml` (+ the annotated pointer at
  `docs/phased_design/phase_09/ci/yearline_nightly.yml`)
- Per-file test runner: `scripts/run_tests.sh`
- Producer contract test: `scripts/validate_contract_fixtures.py`
- Data layer: `src/yearline_universe/data_loader.py`
- Enablement checklist + data-source detail: `docs/phased_design/phase_09/option_mgmt_handoff.md` §10 / §10.1
- Two-repo strategy & deployment: `docs/option-mgmt-integration/two_repo_strategy_and_deployment.md` §3

*Educational research only; not financial advice; `must_not_auto_execute`.*
