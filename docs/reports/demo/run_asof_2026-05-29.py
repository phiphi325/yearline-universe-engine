"""Point-in-time run of the V13 engine AS OF end-of-day 2026-05-29 (no data after that Friday).

Truncates every ticker's price history to < 2026-05-30 (exclusive end => includes the 05-29 close) via
StudyConfig.end, then runs the full universe pipeline with the consumer overlays enabled
(pool_hazard + calibrate + surface_blend + surface_success). Dumps the per-ticker envelopes + a compact
summary for the report. Educational research only.
"""
import sys, os, json, dataclasses
sys.path.insert(0, "src")
from yearline_universe import load_universe_config
from yearline_universe.ticker_pipeline import run_universe_pipeline

OUT = "docs/reports/demo"
os.makedirs(OUT, exist_ok=True)
AS_OF = "2026-05-29"
END_EXCLUSIVE = "2026-05-30"   # `< end` => last bar = 2026-05-29

uni = load_universe_config("config/universe_mvp_software_like.yaml")
study2 = dataclasses.replace(uni.study, end=END_EXCLUSIVE)
uni2 = dataclasses.replace(uni, study=study2, as_of=AS_OF)

res = run_universe_pipeline(uni2, cache_dir="data/price_cache", provider="cache",
                            pool_hazard=True, calibrate=True, surface_blend=True, surface_success=True)

def _g(d, *path, default=None):
    for k in path:
        if not isinstance(d, dict):
            return default
        d = d.get(k)
        if d is None:
            return default
    return d

envelopes, summary = {}, []
for tk, r in res.ticker_results.items():
    env = getattr(r, "latest_context", {}) or {}
    envelopes[tk] = env
    rh = env.get("retry_hazard_context", {}) or {}
    rs = env.get("retry_success_context")
    blend = (rh.get("direct_classifier_blend") or {}).get("per_horizon", {}) if isinstance(rh.get("direct_classifier_blend"), dict) else {}
    comp = (rs or {}).get("successful_reclaim_within_horizon", {}) if rs else {}
    row = {
        "ticker": tk, "status": r.status,
        "sector": env.get("sector"), "peer_group": env.get("peer_group"),
        "as_of": env.get("as_of"), "data_as_of": _g(env, "source", "data_as_of"),
        "active_engine": _g(env, "active_engine_context", "active_engine"),
        "mode_state": _g(env, "active_engine_context", "mode_state"),
        "distance_to_ma250_pct": _g(env, "repair_retry_context", "distance_to_ma250_pct"),
        "drawdown_so_far_pct": _g(env, "repair_retry_context", "drawdown_so_far_pct"),
        "below_ma250_depth_pct": _g(env, "repair_retry_context", "below_ma250_depth_so_far_pct"),
        "required_rebound_pct": _g(env, "repair_retry_context", "required_rebound_to_ma250_pct"),
        # occurrence (canonical empirical)
        "p_retry_10": rh.get("p_retry_within_10d"), "p_retry_20": rh.get("p_retry_within_20d"),
        "p_retry_40": rh.get("p_retry_within_40d"), "p_retry_60": rh.get("p_retry_within_60d"),
        "p_retry_40_calibrated": rh.get("p_retry_within_40d_calibrated"),
        "occ_gate_40_passed": _g(rh, "calibration_gate_40d", "passed"),
        # occurrence blend (Phase 7), per horizon prob + gate
        "blend_40": _g(blend, "40", "blend_probability"), "blend_40_gate": _g(blend, "40", "gate_passed"),
        "blend_60": _g(blend, "60", "blend_probability"), "blend_60_gate": _g(blend, "60", "gate_passed"),
        # success (RS-4)
        "success_available": bool(rs and rs.get("available")),
        "p_success_given_retry": (rs or {}).get("p_success_given_retry"),
        "success_clf": (rs or {}).get("classifier_probability"),
        "success_emp": (rs or {}).get("empirical_probability"),
        "success_emp_scope": (rs or {}).get("empirical_reference_scope"),
        "success_gate_passed": (rs or {}).get("gate_passed"),
        "reclaim_40": _g(comp, "40", "surfaced_probability"), "reclaim_40_surface": _g(comp, "40", "occurrence_surface"),
        "reclaim_60": _g(comp, "60", "surfaced_probability"), "reclaim_60_surface": _g(comp, "60", "occurrence_surface"),
        "trend_state": _g(env, "post_confirmation_trend_context", "trend_state"),
    }
    summary.append(row)

json.dump(envelopes, open(f"{OUT}/asof_2026-05-29_envelopes.json", "w"), indent=2, default=str)
json.dump({"universe": res.universe_name, "as_of": res.as_of, "tickers": summary},
          open(f"{OUT}/asof_2026-05-29_summary.json", "w"), indent=2, default=str)

print(f"universe as_of = {res.as_of}")
print(f"{'tk':>6} {'engine':>26} {'dist_ma250':>10} {'pR40':>6} {'occgate40':>9} {'P(succ)':>8} {'sgate':>6} {'reclaim40':>9} {'reclaim60':>9}")
for row in summary:
    eng = (row['active_engine'] or '')[:26]
    print(f"{row['ticker']:>6} {eng:>26} {str(row['distance_to_ma250_pct']):>10} {str(row['p_retry_40']):>6} "
          f"{str(row['occ_gate_40_passed']):>9} {str(row['p_success_given_retry']):>8} {str(row['success_gate_passed']):>6} "
          f"{str(row['reclaim_40']):>9} {str(row['reclaim_60']):>9}")
print(f"\ndata_as_of per ticker: {sorted(set(str(r['data_as_of']) for r in summary))}")
print(f"wrote {OUT}/asof_2026-05-29_envelopes.json + asof_2026-05-29_summary.json")
