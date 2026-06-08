# Phase 3 — Hazard Hardening (port the V12.4.1 empirical horizon policy)

**Status:** ✅ DELIVERED (2026-06-07) — ports the user's authoritative P40 fix in
`docs/uploaded/yearline_attempt_analysis_v12_FINAL_research_complete_PATCHED_P40_02.ipynb`
**Part of:** the V13.3 phased roadmap (see `../README.md`)
**Theme:** stop surfacing the saturating state-hold-forward step by adopting V12's
own fix — **empirical completed-path horizon probabilities** — as the canonical
P(retry ≤ H), with the logistic model kept only for `hazard_today` and the
state-hold-forward curve demoted to a labelled diagnostic.

> Educational research only. Not financial advice. Evidence overlay; no execution.
> **This phase deliberately CHANGES the hazard numbers** (canonical P(retry≤H) is
> replaced) → it is a gated before/after review, not output-preserving.

---

## 1. Objective

Port the **V12.4.1 "empirical horizon" policy** (benchmark notebook cells 95 / 108 /
138) into V13 so the surfaced retry probability is no longer the ill-posed step
(P60 = P90 = 1.000; P40 = 0.002/0.30/0.51) diagnosed in
`../../V13_data_and_report_analysis.md` §2.2.

## 2. What the benchmark actually does (and what V13 will copy)

The fix is a **probability-policy** change, **not** a model re-fit. Three moves:

1. **Keep the discrete-time logistic hazard model, but use it only for
   `hazard_today`** (the one-day instantaneous conditional hazard). Its features,
   `class_weight="balanced"` logistic, and coefficients are **unchanged** — the
   collinear `distance`/`required_rebound` pair is *not* dropped, nothing is
   standardised or regularised. (So the earlier "drop a feature / standardise /
   regularise" idea is **dropped** — it is not what the benchmark did.)
2. **Demote the state-hold-forward curve to a labelled diagnostic**
   (`*_model_state_hold_forward_diagnostic`). It is no longer surfaced as
   P(retry ≤ H). This removes the saturation from the canonical output.
3. **Canonical P(retry ≤ H) = empirical completed-path estimator.** From the daily
   at-risk panel, per completed transition compute
   `remaining_trading_days_to_retry = event_trading_day − trading_days_since_touch`;
   for a given state, borrow strength from **similar historical states** via a
   hierarchical bucket fallback and Bayesian shrinkage to a universe prior:
   - state buckets: `days_since_touch` × `distance_to_ma250` × `drawdown_so_far`
     (+ categorical `ticker` / `group` / `transition` / `from_canonical_quality`);
   - scope ladder (first with ≥ 25 rows wins): ticker+transition+quality+state →
     ticker+transition+state → group+transition+state → universe+transition+state →
     group+state → universe+state → group+transition → universe+transition →
     all-completed;
   - `P = (k + S·prior) / (n + S)`, prior = universe rate, `S = 8`;
   - policy tag `v12_4_1_empirical_horizon_calibrated`.

Crucially, the benchmark applies the empirical estimator **everywhere the canonical
P is used** — including the per-day **replay** series (cell 108), so the
`mode_state` / engine-handoff (`repair_retry_probability_building`, gated on
`P60 ≥ 0.50`) is driven by the **empirical** P60, not the model step. The model
curve is retained alongside as a `_model_state_hold_forward_diagnostic` column.

> **Why this is the right fix (vs. re-specifying the model forward scenario):** the
> empirical estimator is the same "borrow strength from similar historical states"
> idea as the Phase 2 nearest-neighbour timing estimator, it is what the
> **V12.6 calibration report actually scores** (cell 138 — predicted ≈ observed,
> see §2.3), and it is the user's authoritative choice.

## 3. Scope

In scope (multi-file — the empirical P is canonical everywhere it appears):
- **`hazard.py`** — add the empirical estimator: reference-row builder from the
  panel (`remaining_trading_days_to_retry` + buckets), `empirical_horizon_probabilities_for_row`
  (hierarchical fallback + shrinkage), and integrate into `run_hazard_layer`'s
  context (canonical `horizon_probabilities` = empirical; `hazard_today` = model;
  `diagnostic_model_state_hold_forward` = the old curve).
- **`replay.py`** — build the reference once from the hazard panel; per replay day
  set `p_retry_within_{h}d` = empirical (canonical) + keep
  `p_retry_within_{h}d_model_state_hold_forward_diagnostic`; `mode_state_replay` from
  the empirical P40/P60. (The vectorised model-curve scoring is retained for the
  diagnostic columns only.)
- **`context_export.py`** — `retry_hazard_context` surfaces the empirical P
  (canonical) + `reference_n` / `reference_scope` + a `diagnostic_model_state_hold_forward`
  block + the policy tag.
- **`semantic.py`** — gating unchanged in shape (it gates `p_retry_within_*`, now
  empirical); carry the diagnostic columns through.

Out of scope:
- The calibration evaluation + isotonic transform + gating (**Phase 4** — ports the
  V12.6 harness, which already scores this empirical estimator).
- Data freshness / pooled-universe reference enrichment (**Phase 5** — more tickers
  ⇒ more reference rows ⇒ higher scopes pass the ≥25 threshold).

## 4. Acceptance criteria & results

| Criterion | Target | Result |
|---|---|---|
| No saturation in canonical output | empirical P60/P90 **not pinned at 1.000** | ✅ MSFT canonical P60 = **0.924** (data-driven), vs the diagnostic curve's 1.000 |
| P40 stable single vs pooled | spread collapses | ✅ MSFT P40 = **0.781 single == 0.781 pooled** (vs the old model toe 0.002/0.30/0.51) |
| Faithful policy | curve demoted; canonical tagged; mode-state from empirical | ✅ `diagnostic_model_state_hold_forward` block; policy `v13_empirical_horizon_calibrated`; `mode_state_replay` from empirical P40/P60 |
| Transparent provenance | reference scope + n on the canonical P | ✅ MSFT P40 `reference_scope = group_transition`, `n = 239` |
| Schema additive | `retry_hazard_context` keeps its keys + new sub-fields; other blocks byte-identical | ✅ keys preserved; added `probability_policy`, `p_retry_within_40d_reference_{n,scope}`, `diagnostic_model_state_hold_forward` |
| Guards + tests | no-hardcoded-ticker holds; tests green | ✅ **41/41** (added `tests/test_hazard_empirical.py`, +3) |

### Before / after — MSFT 2026-06-05 (`retry_hazard_context`)

`hazard_today` = 0.0001 (logistic one-day hazard, retained).

| horizon | **canonical** (empirical completed-path) | diagnostic (model state-hold-forward = the old canonical) |
|---|---|---|
| P10 | 0.304 | 0.001 |
| P20 | 0.512 | 0.010 |
| P40 | **0.781** | 0.286 |
| P60 | **0.924** | **1.000** ← the step |

The empirical canonical reflects "how often did similar historical states retouch
within H trading days" (here `group_transition` scope, n=239 — MSFT is the lone
`mega_cap_software` peer, so the state-specific scopes don't yet reach the ≥25 floor
and it falls back to transition-only; Phase 5's wider universe will let the
deep-below state-conditioned scopes qualify). The model's saturating step is retained
verbatim as the diagnostic. AAPL (trend engine active) → `retry_hazard_context.active`
= false, `diagnostic_model_state_hold_forward` = null (gated).

### Files changed (as-built)

- `src/yearline_universe/hazard.py` — empirical estimator (`build_empirical_horizon_reference`,
  `empirical_horizon_probabilities_for_row`, buckets + scope ladder + shrinkage);
  `run_hazard_layer` surfaces empirical as canonical + the diagnostic block.
- `src/yearline_universe/replay.py` — per replay day: canonical `p_retry_within_{h}d`
  = empirical, `*_model_state_hold_forward_diagnostic` kept, `mode_state_replay` from
  empirical P; reference built once from the fitted panel; cache schema bumped to v2.
- `src/yearline_universe/context_export.py` — `retry_hazard_context` policy + reference
  provenance + diagnostic block.
- `tests/test_hazard_empirical.py` (new, +3); `docs/phased_design/phase_03/artifacts/`.

## 5. Files expected to change

`src/yearline_universe/hazard.py`, `replay.py`, `context_export.py`, `semantic.py`;
`tests/` (empirical estimator + no-saturation + envelope); docs: this README +
`artifacts/` (MSFT before/after horizon table), spec §8, analysis §2.2/§2.3.

## 6. Cross-check basis

- `docs/uploaded/yearline_attempt_analysis_v12_FINAL_research_complete_PATCHED_P40_02.ipynb`
  — cells 95 (live), 108 (replay), 138 (calibration). The authoritative source.
- `docs/uploaded/yearline_v12_calibration_walkforward_report_v12_6.pdf` — the
  empirical estimator's horizon calibration (Phase 4 baseline).
- `../../V13_data_and_report_analysis.md` §2.2 (step diagnosis) and §2.3 (the
  empirical-estimator reconciliation).

## 7. Decision gate → Phase 4

With the canonical P(retry ≤ H) now an empirical, non-saturating, pool-stable
quantity, **Phase 4** ports the V12.6 calibration/walk-forward harness (which scores
exactly this estimator), adds an isotonic transform + purged transition-aware splits,
fills `calibration_context`, and **gates** the surfaced probability.
