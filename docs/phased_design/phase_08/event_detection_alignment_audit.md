# Audit — are strict & loose attempts properly aligned + time-correctly processed per ticker?

*A correctness audit of the attempt/event-detection layer that underpins Phase-8 RS-1's success
labels. Question: do the **loose** and **strict** attempts line up and get processed correctly from a
**time-series perspective, for each ticker**? Verdict: **yes** — all per-ticker integrity checks pass;
one (non-blocking) hardening recommendation. Educational research only.*

---

## 0. Why this matters to RS-1

RS-1's success label (`y_success`) is derived from the recovery table's `next_attempt_success`, which
comes from the **canonical events** built by `event_detection.build_canonical_events`. If strict/loose
attempts were mis-ordered, double-counted, cross-contaminated between tickers, or mis-mapped to the
price index, the success labels would inherit the error. This audit checks they are not.

## 1. How strict & loose attempts are detected and reconciled (`event_detection.py`)

- **Two independent triggers** per bar (`_prepare_detector_frame`), both requiring `sustained_below`:
  - **strict** — `High ≥ MA250` AND a *fresh* cross from below (`~above_close.shift(1)`).
  - **loose** — the bar straddles a ±`band` (1%) zone around MA250 (`Low ≤ MA250·(1+band)` and
    `High ≥ MA250·(1−band)`).
- **`detect_source_attempts(ticker, df, detector)`** runs the V10-parity state machine **separately**
  for `"strict"` and `"loose"`, per ticker, advancing `i = end_work_loc + 1` after each attempt (so an
  attempt's lifecycle is never re-counted). `trading_loc` is mapped back to the **original** price index
  via `df.index.get_indexer([touch_date])`.
- **`build_canonical_events(ticker, df, source)`** consolidates them, sorted by `(trading_loc, detector)`:
  1. each **strict** hit → its own cluster **anchor**;
  2. each **loose** hit → attached to the **nearest strict anchor** iff within
     `canonical_touch_merge_trading_days` (=2), else
  3. chained into **loose-only** clusters (non-chaining: a new cluster starts when a hit is > merge from
     the current anchor).
  Per cluster: the representative is the earliest **strict** row (quality `"strict"`) else the earliest
  loose row (`"loose_only"`); `canonical_outcome` = success > pending > fail across the cluster's rows;
  a `canonical_warning` flags any cluster whose span exceeds the merge window.
- **`assign_canonical_rounds`** walks events in `canonical_touch_date` order; `round` increments after a
  `success`, `canonical_attempt_no` resets to 1.

**Per-ticker isolation** is by construction: `_build_foundation` calls both detectors and the
canonicaliser on **one ticker's** `price_df`; `trading_loc` is therefore that ticker's own index
position.

## 2. Audit method

For each of the 9 universe tickers, rebuild strict + loose source attempts and the canonical events,
then check (script: `artifacts/…`, reproduced from `/tmp/audit_event_alignment.py`):

| Check | What it proves |
|---|---|
| `monotonic_time` | canonical events strictly increasing in `trading_loc` (chronological, no re-ordering) |
| `dup_loc` = 0 | no two canonical events on the same bar |
| `date_loc_map_ok` | `canonical_touch_date == price_df.index[trading_loc]` → correct raw-index mapping **and** no cross-ticker mixing |
| `loc_in_range` | every `trading_loc` ∈ [0, len(df)) |
| `strict_q == n_strict_src` | every strict attempt → exactly one strict-quality canonical anchor (none lost/duplicated) |
| `min_strict_gap > merge` | strict anchors never fall within the merge window ⇒ no missed strict-vs-strict merge |
| `rounds_monotonic` + `attempt_reset_ok` | rounds/attempts assigned in time order; attempt resets to 1 only after a success |
| `span_warnings` | clusters that exceed the merge window |
| `outcome_disagree` | merged strict+loose clusters where the two detectors disagreed on outcome |

## 3. Results (9-ticker universe, `merge = 2` trading days)

| ticker | strict_src | loose_src | canonical | strict_q | loose_only | merged | outcome_disagree | monotonic | dup_loc | date↔loc ok | min_strict_gap | rounds_ok | attempt_reset_ok | span_warn |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| MSFT | 24 | 35 | 40 | 24 | 16 | 19 | 0 | ✓ | 0 | ✓ | 5 | ✓ | ✓ | 0 |
| AAPL | 19 | 24 | 25 | 19 | 6 | 18 | 0 | ✓ | 0 | ✓ | 5 | ✓ | ✓ | 0 |
| GOOGL | 24 | 32 | 37 | 24 | 13 | 19 | 0 | ✓ | 0 | ✓ | 6 | ✓ | ✓ | 0 |
| AMZN | 29 | 34 | 40 | 29 | 11 | 23 | 0 | ✓ | 0 | ✓ | 6 | ✓ | ✓ | 0 |
| META | 18 | 24 | 27 | 18 | 9 | 15 | 0 | ✓ | 0 | ✓ | 7 | ✓ | ✓ | 0 |
| NVDA | 16 | 22 | 24 | 16 | 8 | 14 | 0 | ✓ | 0 | ✓ | 5 | ✓ | ✓ | 0 |
| QQQ | 20 | 22 | 27 | 20 | 7 | 15 | 0 | ✓ | 0 | ✓ | 9 | ✓ | ✓ | 0 |
| XLK | 18 | 24 | 26 | 18 | 8 | 16 | **1** | ✓ | 0 | ✓ | 7 | ✓ | ✓ | 0 |
| IGV | 16 | 18 | 20 | 16 | 4 | 14 | 0 | ✓ | 0 | ✓ | 19 | ✓ | ✓ | 0 |
| **Σ** | **184** | **235** | **266** | **184** | **82** | **153** | **1** | all ✓ | 0 | all ✓ | ≥5 | all ✓ | all ✓ | 0 |

## 4. Findings

1. **Chronological integrity is exact.** Every ticker's canonical events are strictly time-ordered
   (`monotonic_time`), with no duplicate bars and correct `date ↔ trading_loc` mapping. Rounds and
   attempt numbers are assigned in time order and reset correctly after a success.
2. **No cross-ticker contamination.** `date_loc_map_ok` holds for all tickers — each canonical event's
   date resolves to *that ticker's own* index position. Per-ticker isolation (asserted by code reading
   of `_build_foundation`) is confirmed empirically.
3. **Strict attempts are preserved 1:1.** Aggregate `strict_q (184) == n_strict_src (184)` — every
   strict attempt becomes exactly one strict-quality canonical anchor; none dropped or double-counted.
4. **Loose attempts merge correctly.** 235 loose hits → 153 absorbed into the nearest strict anchor
   within the 2-day window + 82 loose-only clusters. **Zero** `span_warnings`; min strict-anchor gap is
   5–19 (all > merge=2), so strict anchors never need strict-vs-strict merging (the state machine spaces
   them by each attempt's lifecycle).
5. **Strict/loose rarely disagree.** Only **1 of 153** merged clusters (XLK) had the two detectors
   disagree on outcome; the canonical "optimistic" policy (success > pending > fail) therefore almost
   never changes the label. The one case is recorded in the artifacts.

**Net: the loose and strict attempts are properly lined up and time-correctly processed per ticker.**
RS-1's success labels rest on a sound event timeline.

## 5. Hardening recommendation (non-blocking)

`build_canonical_events` relies on an **unenforced single-ticker precondition**: `trading_loc` is a
per-ticker index position, so passing a *pooled* multi-ticker `source_attempts` frame would let the
nearest-anchor matching cross-contaminate tickers. All current callers (`_build_foundation`) pass
per-ticker frames, so this is correct today — but a one-line defensive guard would prevent future
misuse:

```python
# at the top of build_canonical_events, after the empty check:
if source_attempts["ticker"].nunique() > 1:
    raise ValueError("build_canonical_events expects a single ticker's source_attempts")
```

(Optional; deferred — it changes no behaviour and is not required for RS-1.)

## 6. Artifacts

- `artifacts/event_detection_alignment_audit.csv` — the per-ticker table above.
- `artifacts/event_detection_alignment_audit.json` — `all_ok`, the aggregate, and per-ticker detail
  (incl. `merge_window_trading_days`).

Reproduce: `/tmp/audit_event_alignment.py` (rebuilds strict/loose attempts + canonical events per ticker
and runs every check).
