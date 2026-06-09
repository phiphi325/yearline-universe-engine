"""RS-4 demo — surface the gated retry-SUCCESS overlay live on the real 9-ticker universe.

Exports, per ticker, the ``retry_success_context`` block that RS-4 attaches to the statistical-context
envelope: P(success │ retry) (gated blend) + the composite P(reclaim ≤ H) = P(retry ≤ H) × P(success │
retry), surfaced only where BOTH the occurrence gate (Phase-4 calibration) and the success gate (RS-3)
pass. Also confirms the byte-identical-when-off guarantee at the envelope level.

Memory-light by design: builds the foundations + the compute-once success/calibration models ONCE, then
runs each ticker's *hazard layer* serially (not the full replay/semantic pipeline). Run from the repo root:
    python3 docs/phased_design/phase_08/run_rs4_success_overlay.py
Writes (into artifacts/): rs4_success_overlay_example.json, rs4_success_overlay_summary.csv.
Educational research only; not financial advice.
"""
import os, sys, json, csv, gc
sys.path.insert(0, "src")

from yearline_universe import load_universe_config
from yearline_universe.ticker_pipeline import _build_foundation
from yearline_universe.hazard import run_hazard_layer, build_hazard_daily_panel
from yearline_universe.calibration import build_calibration_model
from yearline_universe.blend_surface import build_blend_model
from yearline_universe.success_surface import build_success_surface_model
from yearline_universe.context_export import build_statistical_context_envelope

ART = "docs/phased_design/phase_08/artifacts"
os.makedirs(ART, exist_ok=True)
uni = load_universe_config("config/universe_mvp_software_like.yaml")
study0 = uni.study_for(uni.tickers[0])

# --- foundations once (cheap, deterministic) ---
foundations, pooled = {}, {}
for tc in uni.tickers:
    try:
        f = _build_foundation(tc, uni, cache_dir="data/price_cache", provider="cache")
        foundations[tc.ticker] = (tc, f)
        pooled[tc.ticker] = {"peer_group": tc.peer_group, "price_df": f["price_df"],
                             "recovery_table": f["recovery"], "live_diagnostic": f["live"]}
    except Exception as e:
        print(f"  skip {tc.ticker}: {type(e).__name__}: {e}")

# --- compute-once models (success overlay + pooled calibration for the occurrence gate) ---
success_model = build_success_surface_model(pooled, study0)
print(f"success_model available={success_model.get('available')} w={success_model.get('blend_weight_classifier')} "
      f"gate={success_model.get('gate')}")
# Phase-7 occurrence blend (the consumer-grade occurrence surface RS-4 composes against).
blend_model = build_blend_model(pooled, study0)
cal_model = None
try:
    any_tk = next(iter(pooled))
    panel0 = build_hazard_daily_panel(pooled, any_tk, study0)
    if not panel0.empty:
        cal_model = build_calibration_model(panel0)
    del panel0; gc.collect()
except Exception as e:
    print(f"  calibration model unavailable: {type(e).__name__}: {e}")

rows, example, n_surfaced = [], None, 0
for tk, (tc, f) in foundations.items():
    try:
        hz = run_hazard_layer(tk, tc.peer_group, f["price_df"], f["recovery"], f["live"], study0,
                              pooled_data=pooled, calibrate=True, calibration_model=cal_model,
                              surface_blend=True, blend_model=blend_model,
                              surface_success=True, success_model=success_model)
        sc = hz.get("success_context") or {}
    except Exception as e:
        print(f"  {tk}: hazard layer error {type(e).__name__}: {e}")
        sc = {"available": False, "warning": "error"}
    if not sc.get("available"):
        rows.append({"ticker": tk, "available": False, "p_success_given_retry": None,
                     "success_gate_passed": None, "composite_40d": None, "surfaced_40d": None,
                     "warning": sc.get("warning")})
        gc.collect(); continue
    comp = sc.get("successful_reclaim_within_horizon", {}) or {}
    comp40, comp60 = comp.get("40", {}), comp.get("60", {})
    surfaced = comp40.get("surfaced_probability")
    n_surfaced += int(surfaced is not None)
    rows.append({"ticker": tk, "available": True,
                 "p_success_given_retry": sc.get("p_success_given_retry"),
                 "success_gate_passed": sc.get("gate_passed"),
                 "composite_40d": comp40.get("p_successful_reclaim_within_h"),
                 "surfaced_40d": surfaced,
                 "surface_40d": comp40.get("occurrence_surface"),
                 "surfaced_60d": comp60.get("surfaced_probability"),
                 "surface_60d": comp60.get("occurrence_surface"),
                 "warning": None})
    if example is None:
        example = {"ticker": tk, "as_of": sc.get("as_of") or (f["live"] or {}).get("as_of_date"),
                   "retry_success_context": sc}
    gc.collect()

# --- byte-identical-when-off: same inputs, no success_context ⇒ key absent ---
card = {"as_of_date": "demo", "active_engine": "repair_retry_hazard_engine"}
env_off = build_statistical_context_envelope("DEMO", "Technology", "software_like", card, card,
                                             success_context=None)
env_on = build_statistical_context_envelope("DEMO", "Technology", "software_like", card, card,
                                            success_context={"available": True, "p_success_given_retry": 0.4})
off_ok = ("retry_success_context" not in env_off) and ("retry_success_context" in env_on)

json.dump(example or {"warning": "no_available_success_block"},
          open(f"{ART}/rs4_success_overlay_example.json", "w"), indent=2, default=str)
with open(f"{ART}/rs4_success_overlay_summary.csv", "w", newline="") as fh:
    wri = csv.DictWriter(fh, fieldnames=["ticker", "available", "p_success_given_retry",
                                         "success_gate_passed", "composite_40d", "surfaced_40d",
                                         "surface_40d", "surfaced_60d", "surface_60d", "warning"],
                         extrasaction="ignore")
    wri.writeheader()
    for r in rows:
        wri.writerow(r)

print("\n=== RS-4 SUCCESS OVERLAY (real universe) ===")
print(f"tickers={len(rows)}  with_available_block={sum(r['available'] for r in rows)}  "
      f"surfaced_composite_40d={n_surfaced}")
print(f"byte-identical-when-off (key absent in OFF envelope, present in ON): {off_ok}")
for r in rows:
    print(f"  {r['ticker']:>6}: avail={r['available']!s:>5}  P(success|retry)={r['p_success_given_retry']}  "
          f"gate={r['success_gate_passed']}  reclaim@40d={r.get('surfaced_40d')}({r.get('surface_40d')})  "
          f"reclaim@60d={r.get('surfaced_60d')}({r.get('surface_60d')})  {r['warning'] or ''}")
print(f"\nwrote {ART}/rs4_success_overlay_example.json + rs4_success_overlay_summary.csv")
