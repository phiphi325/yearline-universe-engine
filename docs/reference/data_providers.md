# Reference — Price data providers (Tiingo implemented; paid alternatives)

How this engine sources OHLCV, **why** the nightly producer uses a keyed provider, and **exactly how** the
provider system works — with **Tiingo** as the **implemented** choice (V13.9, `_load_from_tiingo`) plus honest
tradeoffs vs Alpha Vantage / Polygon / EOD Historical Data for the paid-alternative case.

> Educational research only; not financial advice. The data layer is `src/yearline_universe/data_loader.py`.
> **Pricing/limits below drift — always confirm against each vendor's current pricing page before buying.**

> **Status (V13.9): Tiingo is now the implemented provider.** After the head-to-head (§7), **Tiingo** was
> chosen over Alpha Vantage premium — it returns **adjusted close on the free tier** and covers a ~9-ticker
> nightly with huge headroom, where AV's adjusted endpoint is premium (~$50/mo). Shipped: `_load_from_tiingo`
> in `data_loader.py` (keyed via `TIINGO_API_KEY`), `scripts/run_nightly.py` (the producer), and
> `scripts/parity_check.py` (the migration safety gate). The **Alpha Vantage** sections below remain as the
> paid-alternative reference (and the throttle/adjusted-endpoint caveats generalize).

---

## 1. TL;DR / decision summary

- **Today the engine is keyless** (`cache → yfinance → yahoo_chart`). All free, no key. Fine for research and
  for **CI** (which uses the committed cache). The weak spot is the **nightly cron on a GitHub-hosted runner**:
  Yahoo throttles/blocks datacenter IPs, so live pulls can intermittently fail.
- **A paid provider fixes that** (authenticated REST API, not IP-scraped) and gives you an SLA + cleaner
  adjusted data. The cost is a key, a small code addition, and a **parity check** so you don't silently change
  the model's inputs.
- **The caveat that drove the choice:** the engine needs **split/dividend-adjusted close** (it ports yfinance
  `auto_adjust=True`). **Tiingo returns adjusted close on its free tier**, so it is the **implemented** provider
  (`_load_from_tiingo`, keyed via `TIINGO_API_KEY`; **§5.1**). Alpha Vantage's adjusted endpoint
  (`TIME_SERIES_DAILY_ADJUSTED`) is **premium** (~$50/mo) and its free tier is **raw** only — so AV "done
  correctly" means the paid plan (§6). For ~9-ticker adjusted daily EOD, **Tiingo (free / $10 Power) wins**;
  AV / Polygon / EOD remain documented alternatives (§7).
- **Whichever provider:** add it behind the chain (keep the cache fallback) and **run the parity check**
  (`scripts/parity_check.py`, §9) before trusting it — a provider swap changes *inputs*, not the contract shape.

---

## 2. How the provider system works today

`load_price_data(ticker, *, config, cache_dir, provider="auto", force_download=False)` walks a provider chain
and returns a standardized OHLCV frame (tz-naive `Date` index; `Open/High/Low/Close/Volume`):

```python
# src/yearline_universe/data_loader.py
_PROVIDERS = {
    "cache":       _load_from_cache,        # {cache_dir}/{TICKER}.csv   (offline, reproducible)
    "yfinance":    ...,                      # yf.download(auto_adjust=True)   — free, no key
    "yahoo_chart": ...,                      # Yahoo v8 chart HTTPS (curl_cffi/requests) — free, no key
}

provider="auto"  → ["cache","yfinance","yahoo_chart"]      # force_download=True drops "cache"
provider="X"     → ["X"]                                    # pin a single provider
```

Each provider returns a frame or `None`; the chain walks until one yields non-empty data, then tags
`df.attrs["provider"]`. Adding a provider = **one function + one registry entry + (optionally) a place in the
chain**. There is no env/secret reading in the loader today — a paid provider introduces the first one.

**Cache format** (what a refreshed nightly would write back): a per-ticker CSV with a `Date` column +
`Open,High,Low,Close,Volume`, **already adjusted** (yfinance auto-adjusted convention). `standardize_price_df`
also accepts an `Adj_Close`/`Adjusted_Close` column and folds it into `Close` when a plain `Close` is absent.

---

## 3. Why move to a paid provider

| Driver | Detail |
|---|---|
| **Cloud-runner IP blocking** | Yahoo (both `yfinance` and the v8 chart endpoint) rate-limits/blocks datacenter IPs. GitHub-hosted runners share Azure ranges → intermittent `404/429`/empty on a cron. A keyed REST API authenticates per-request and isn't IP-scraped. |
| **Reliability / SLA** | Yahoo is an unofficial, unsupported scrape; it breaks without notice. A paid API has documented uptime + support. |
| **Adjusted-data quality** | Split/dividend adjustment differs by vendor. A provider with first-class, documented adjusted close beats reverse-engineering Yahoo's. |
| **Reproducibility** | A pinned provider + a refreshed committed cache makes the nightly deterministic and auditable. |

What you **don't** need here: intraday/tick data, options chains, fundamentals, huge history. It's **daily
adjusted EOD for ~9 tickers** — the cheapest tier of any reputable vendor covers it.

---

## 4. The caveat that governs everything: **adjusted close**

The engine's MA250 / `distance_to_ma250_pct` / drawdown / trend math all assume a **split- and
dividend-adjusted** close (yfinance `auto_adjust=True`). If a new provider returns **raw** close (or adjusts
differently), every downstream number shifts — including the gated `YearlineContext` the consumer trusts.

**Therefore, any provider swap must:**
1. Use that provider's **adjusted close** (not raw), and
2. Pass a **parity check** against the current committed cache before you trust it (§9, step 3).

This is also why the AV *free* tier (raw only) is a trap for this engine (§6).

---

## 5. The integration pattern (drop-in)

A new provider mirrors `_load_from_yahoo_chart`: build the request, parse to an OHLCV frame, hand to
`standardize_price_df`, return `None` on any failure so the chain can fall back. The **key comes from an env
var** (never hard-coded, never committed).

```python
# src/yearline_universe/data_loader.py  (sketch — adapt and test against a parity check)
import os

def _load_from_alpha_vantage(ticker: str, config: StudyConfig) -> pd.DataFrame | None:
    key = os.environ.get("ALPHAVANTAGE_API_KEY")
    if not key:
        return None                                  # no key ⇒ silently fall through the chain
    # NOTE: *_ADJUSTED is the premium endpoint; the free TIME_SERIES_DAILY is RAW (unadjusted) — see §6.
    url = (
        "https://www.alphavantage.co/query"
        f"?function=TIME_SERIES_DAILY_ADJUSTED&symbol={ticker}"
        f"&outputsize=full&datatype=csv&apikey={key}"
    )
    try:
        import requests
        r = requests.get(url, timeout=40)            # honors HTTPS_PROXY automatically
        if r.status_code != 200 or not r.text:
            return None
        text = r.text.lstrip()
        # AV signals throttling/errors as JSON at HTTP 200 — detect and bail (so the chain falls back):
        if text.startswith("{"):                     # {"Note": ...} / {"Information": ...} / {"Error Message": ...}
            return None
        df = pd.read_csv(io.StringIO(r.text))         # columns: timestamp,open,high,low,close,adjusted_close,volume,...
    except Exception:
        return None
    if df is None or df.empty or "timestamp" not in df.columns:
        return None
    df = df.set_index("timestamp")
    # Use the ADJUSTED close as Close so it matches the engine's auto_adjust convention.
    if config.auto_adjust and "adjusted_close" in df.columns:
        df["close"] = df["adjusted_close"]
    df = df.rename(columns=str.title)                 # Open/High/Low/Close/Volume
    return _slice_window(standardize_price_df(df, ticker), config)

_PROVIDERS["alpha_vantage"] = lambda t, c, d: _load_from_alpha_vantage(t, c)
```

**Placing it in the chain** — two reasonable choices:
- **Explicit (recommended to start):** call with `provider="alpha_vantage"` from `run_nightly.py`, keeping
  `cache` as a committed fallback. Most predictable.
- **Auto-preferred:** make `provider="auto"` try `["cache", "alpha_vantage", "yfinance", "yahoo_chart"]` so AV
  leads live pulls but Yahoo still backstops. Only do this once parity (§9) passes.

**Add the dep** to `pyproject.toml`'s `live` extra if you use a vendor SDK (the sketch above only needs
`requests`, already implied). Keep `[live]` what the nightly installs.

### 5.1 Tiingo — the implemented provider (V13.9)

This is what actually ships — `_load_from_tiingo(ticker, config)` in `data_loader.py`:

- **Key:** `os.environ["TIINGO_API_KEY"]`, sent as an `Authorization: Token …` **header** (not in the URL, so
  it can't leak into logs/proxies). **No key ⇒ returns `None`** and the chain falls through — so the default
  `auto` order is unchanged unless the key is set (backward compatible).
- **Endpoint:** `GET https://api.tiingo.com/tiingo/daily/{ticker}/prices?startDate=…&format=csv` (honors
  `HTTPS_PROXY`).
- **Adjusted by default:** maps the **`adj*`** columns (`adjOpen/adjHigh/adjLow/adjClose/adjVolume`) onto
  `Open/High/Low/Close/Volume` when `config.auto_adjust` (the engine default) — matching yfinance's
  auto-adjusted convention — then through `standardize_price_df`. (Falls back to the raw fields only if `adj*`
  are absent.)
- **Throttle/error guard:** if Tiingo returns a JSON body (`{…}`/`[…]`) instead of CSV — its rate-limit/error
  shape at HTTP 200 — `_load_from_tiingo` returns `None` (fall through), so a throttle never parses as "empty
  data."
- **Chain placement:** registered in `_PROVIDERS` and added to `auto`
  (`cache → tiingo → yfinance → yahoo_chart`); the nightly calls it explicitly via `--provider tiingo`.

**Producer + safety net (shipped):**
- `scripts/run_nightly.py` — pre-flight freshness guard (emits `available:false` on a no-new-bar day) → the
  pooled gated run → `export_yearline_context` + `export_yearline_trend_series` keyed `{ticker}_{as_of}` →
  `yearline_run_status_{run_date}.json`; retry/backoff on every live fetch. (Calendar:
  `yearline_universe/market_calendar.py`.)
- `scripts/parity_check.py` — Tiingo adjusted close vs the committed Yahoo cache; diffs **distance-to-MA250**
  across the universe and fails beyond a tolerance. **Run it once before enabling the schedule.**

**To enable the nightly cron:** set the `TIINGO_API_KEY` repo secret (§8), run `parity_check.py`, then
uncomment `schedule:` in `.github/workflows/yearline_nightly.yml`.

---

## 6. Alpha Vantage specifics (a documented paid alternative)

- **Key:** free, instant from <https://www.alphavantage.co/support/#api-key>. Treat it as a secret anyway.
- **Endpoints:**
  - `TIME_SERIES_DAILY_ADJUSTED` → `open/high/low/close/**adjusted_close**/volume/dividend_amount/split_coefficient`.
    **This is what the engine needs — and it is a *premium* endpoint.**
  - `TIME_SERIES_DAILY` (free) → raw `open/high/low/close/volume`, **not adjusted**. Using it would silently
    feed unadjusted prices into MA250/distance math (§4) — don't, unless you add a correct adjustment step.
  - Params: `outputsize=full` (full history) vs `compact` (latest 100); `datatype=csv|json`.
- **Rate limits (verify current):** the **free** tier is small — recently cited at **~25 requests/day**
  (historically 5 req/min + 500/day). **Premium** plans raise this substantially (≈ 75 → 1200 req/min) at
  roughly **$50–$250/mo**. For ~9 tickers once nightly the *frequency* is trivial; the binding constraint is
  that **adjusted data is premium**, not the request count.
- **Throttle behavior (important gotcha):** when you exceed a limit, AV returns **HTTP 200 with a JSON body**
  like `{"Note": "…call frequency…"}` or `{"Information": "…"}` instead of CSV. You **must** detect that (the
  sketch checks for a leading `{`) and back off / fall through — otherwise you'll parse an "empty" frame and
  think the symbol has no data.
- **Bottom line for AV:** budget for the **premium plan** to get adjusted close. If you want to stay free for
  adjusted EOD, Tiingo/EODHD (§7) are the better fit.

---

## 7. Provider comparison (daily adjusted EOD; **verify current pricing**)

| Provider | Free tier | Adjusted close | Rate limit (free) | Paid entry (approx) | Fit for this engine |
|---|---|---|---|---|---|
| **Alpha Vantage** | yes, but **raw only** | **adjusted = premium** | ~25 req/day | ~$50/mo (75 req/min) | Works, but adjusted ⇒ pay. Your stated choice. |
| **Tiingo** | generous EOD, **incl. adjusted** | ✅ free | ~ hundreds/hr (per their terms) | ~$10–30/mo commercial | Often the **cheapest correct** option for adjusted EOD. |
| **EOD Historical Data** | limited trial | ✅ | trial-limited | ~$20/mo (all-world EOD) | Strong, cheap, generous limits. |
| **Polygon.io** | yes (delayed/EOD, 5/min) | ✅ | 5 req/min | ~$29/mo (unlimited + full history) | Great if you later want intraday/options too. |
| **yfinance/Yahoo** *(current)* | free, keyless | ✅ (`auto_adjust`) | none, but **IP-blocked on cloud** | — | Fine for research/CI; flaky for a hosted cron. |

I'm recording your **Alpha Vantage** decision — §5/§6 are the build path. But if the *only* goal is a reliable
adjusted-EOD nightly, **Tiingo** likely costs less for the same correctness. Your call.

---

## 8. Wiring the key into GitHub Actions

The key lives in **repo secrets**, never in the repo:

1. *Settings → Secrets and variables → Actions → New repository secret* → `ALPHAVANTAGE_API_KEY`.
2. In `.github/workflows/yearline_nightly.yml`, expose it to the produce step:
   ```yaml
   - name: Produce
     env:
       ALPHAVANTAGE_API_KEY: ${{ secrets.ALPHAVANTAGE_API_KEY }}
     run: python scripts/run_nightly.py --provider alpha_vantage --as-of "${{ inputs.as_of || 'latest' }}" --out exports/yearline_context
   ```
3. The provider reads `os.environ["ALPHAVANTAGE_API_KEY"]` (§5). **Never `echo` the key**; secrets are masked
   in logs but don't tempt fate.

This is the **only** place a `*_API_KEY` enters the system — which is why CI (`ci.yml`, offline against the
committed cache) needs **no** secret, and a key was wrong to imply for the keyless default stack.

> Editing `.github/workflows/*` may require the `workflows` permission your platform integration lacks (we hit
> a `403` doing exactly this). Add/modify workflow files with your own credentials. See
> `../tutorials/10_github_actions_automation_tutorial.md` §3.

---

## 9. Safe migration plan (don't silently change model inputs)

Tiingo is implemented; this is the rollout (and the template for any future provider swap):

1. **Get a Tiingo key**, set `TIINGO_API_KEY` locally. (`_load_from_tiingo` already ships in `data_loader.py`.)
2. **Keep fallbacks** — `auto` is `cache → tiingo → yfinance → yahoo_chart`, so a provider hiccup degrades,
   not breaks; the nightly pins `--provider tiingo` explicitly.
3. **Parity check (the important one) — shipped as `scripts/parity_check.py`.** It fetches each universe ticker
   from Tiingo and the committed cache, compares **adjusted close** (max / latest % divergence), and diffs
   **distance-to-MA250** at the latest common bar, failing beyond a tolerance (default 0.25pp). Run
   `TIINGO_API_KEY=… python scripts/parity_check.py` and investigate anything flagged (usually a
   dividend-adjustment difference) **before** trusting the swap.
4. **(Optional) refresh the committed cache** from Tiingo so CI + the cache fallback reflect the chosen source.
   The run manifest already records `data_provider` per ticker (informational, not the gated contract).
5. **Set the `TIINGO_API_KEY` Actions secret** (§8 pattern). The shipped `.github/workflows/yearline_nightly.yml`
   already reads it and runs `run_nightly.py --provider tiingo`, with `validate_contract_fixtures.py` as the
   pre-publish gate. Then **uncomment `schedule:`**.
6. **Monitor** the first scheduled runs (the `yearline_run_status_*` sentinel, divergence drift, holidays).

> A provider swap **does not** touch the `YearlineContext` / `YearlineTrendSeries` contract (`adapter_version`
> / `series_version` stay frozen) — it changes *inputs*, not the *shape*. But because it changes the numbers,
> treat it like any **output-changing** step: parity-checked, reviewed, and dated.

---

## 10. Pitfalls checklist
- [ ] Using **raw** instead of **adjusted** close (AV free tier) → wrong MA250/distance. (§4, §6)
- [ ] Treating AV's throttle JSON (`{"Note"/"Information"}` at HTTP 200) as "no data". (§6)
- [ ] No retry/backoff → a single 429 fails the nightly. Add bounded retries across the chain.
- [ ] Committing the key, or echoing it in logs. (§8)
- [ ] Skipping the parity check → silently shifting the model's inputs (and the consumer's gated context). (§9)
- [ ] Assuming free-tier limits are static — **they change; verify**. (§6, §7)

*Related: `../phased_design/phase_09/option_mgmt_handoff.md` §10.1 (keyless default + cloud-IP caveat),
`../tutorials/10_github_actions_automation_tutorial.md` (the Actions case study),
`../option-mgmt-integration/two_repo_strategy_and_deployment.md` §3 (deployment). Educational research only.*
