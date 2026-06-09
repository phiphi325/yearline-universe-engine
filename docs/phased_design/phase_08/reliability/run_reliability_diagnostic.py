"""RS-3 reliability diagnostic — true calibration vs. base-rate shrinkage (real 9-ticker universe).

Run from the repo root:  python3 docs/phased_design/phase_08/reliability/run_reliability_diagnostic.py
Writes (into this folder): rs3_reliability_diagnostic.json, rs3_reliability_diagram.svg,
rs3_prediction_histogram.svg (SVG so the figures render on GitHub and stay vector-sharp).
Educational research only.
"""
import os, sys, json
sys.path.insert(0, "src")
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from yearline_universe import load_universe_config
from yearline_universe.ticker_pipeline import _build_foundation
from yearline_universe.success_models import build_success_model_table
from yearline_universe.success_calibration import success_oof_surfaces
from yearline_universe.success_reliability import success_reliability_diagnostic, reliability_curve

HERE = "docs/phased_design/phase_08/reliability"
uni = load_universe_config("config/universe_mvp_software_like.yaml")
pooled = {}
for tc in uni.tickers:
    try:
        f = _build_foundation(tc, uni, cache_dir="data/price_cache", provider="cache")
        pooled[tc.ticker] = {"peer_group": tc.peer_group, "price_df": f["price_df"],
                             "recovery_table": f["recovery"], "live_diagnostic": f["live"]}
    except Exception as e:
        print(f"  skip {tc.ticker}: {type(e).__name__}: {e}")

table = build_success_model_table(pooled)
diag = success_reliability_diagnostic(table, n_splits=5)
json.dump(diag, open(f"{HERE}/rs3_reliability_diagnostic.json", "w"), indent=2, default=str)

sh = diag["shrinkage"]
ps = diag["per_surface"]
print("=== RS-3 RELIABILITY DIAGNOSTIC (real universe) ===")
print(f"n={diag['n']}  base_rate={diag['base_rate']}  blend_w(clf)={diag['blend_weight_classifier']}")
for name in ("classifier_raw", "empirical_baseline", "blend"):
    s = ps[name]
    print(f"  {name:>18}: AUC {s['auc']}  MACE {s['mace']}  std {s['std']}  resolution {s['resolution']}")
print("\n--- base-rate shrinkage decomposition ---")
print(f"  variance_shrinkage_index   = {sh['variance_shrinkage_index']}  (1 - var(blend)/var(raw))")
print(f"  MACE raw {sh['mace_raw']} -> blend {sh['mace_blend']}  (total gain {sh['total_mace_gain']})")
print(f"  pure shrink-to-base MACE   = {sh['mace_pure_shrink_to_base']}  (same variance, NO empirical info)")
print(f"  half shrink-to-base MACE   = {sh['mace_half_shrink_to_base']}")
print(f"  gain from shrinkage        = {sh['gain_from_shrinkage']}   from empirical info = {sh['gain_from_empirical_information']}")
print(f"  FRACTION OF GAIN FROM SHRINKAGE = {sh['fraction_of_gain_from_shrinkage']}")
print(f"  resolution lost to shrinkage    = {sh['resolution_lost_to_shrinkage']}  (sharpness traded for calibration)")
print(f"  mean abs pull of raw extremes   = {sh['mean_abs_pull_of_raw_extremes']}")

# --- figures on the REAL surfaces ---
surf = success_oof_surfaces(table, n_splits=5)
y = surf["y"]; raw = surf["surfaces"]["classifier_raw"]; blend = surf["surfaces"]["blend"]
base = surf["base_rate"]
m = np.isfinite(raw) & np.isfinite(blend) & np.isfinite(y)
y, raw, blend = y[m], raw[m], blend[m]
cr = reliability_curve(y, raw, 5); cb = reliability_curve(y, blend, 5)
mace_raw, mace_blend = round(ps["classifier_raw"]["mace"], 4), round(ps["blend"]["mace"], 4)

# Panel A — reliability diagram
fig, ax = plt.subplots(figsize=(7, 6))
ax.plot([0, 1], [0, 1], "k--", alpha=0.5, label="perfect calibration")
ax.plot(cr["bin_pred"], cr["bin_true"], "s-", color="0.6", label=f"raw classifier (MACE {mace_raw})")
ax.plot(cb["bin_pred"], cb["bin_true"], "o-", color="crimson", lw=2.2, label=f"blend (MACE {mace_blend})")
ax.axvline(base, color="slategray", ls=":", label=f"base rate ({base:.2f})")
ax.set_title("RS-3 reliability diagram (leave-one-ticker-out)", fontweight="bold")
ax.set_xlabel("mean predicted P(success)"); ax.set_ylabel("observed success fraction")
ax.legend(loc="upper left"); ax.grid(True, ls=":", alpha=0.6); ax.set_xlim(0, 1); ax.set_ylim(0, 1)
fig.tight_layout(); fig.savefig(f"{HERE}/rs3_reliability_diagram.svg"); plt.close(fig)

# Panel B — prediction-density histogram (the shrinkage visualizer)
fig, ax = plt.subplots(figsize=(7, 6))
bins = np.linspace(0, 1, 16)
ax.hist(raw, bins=bins, alpha=0.4, color="0.5", edgecolor="black", label=f"raw classifier (std {ps['classifier_raw']['std']})")
ax.hist(blend, bins=bins, alpha=0.75, color="crimson", edgecolor="black", label=f"blend (std {ps['blend']['std']})")
ax.axvline(base, color="navy", lw=2, ls="--", label=f"base rate ({base:.2f})")
ax.set_title(f"RS-3 prediction density — variance shrinkage {sh['variance_shrinkage_index']}", fontweight="bold")
ax.set_xlabel("predicted P(success)"); ax.set_ylabel("count")
ax.legend(loc="upper right"); ax.grid(True, ls=":", alpha=0.6)
fig.tight_layout(); fig.savefig(f"{HERE}/rs3_prediction_histogram.svg"); plt.close(fig)

print(f"\nwrote {HERE}/rs3_reliability_diagnostic.json + rs3_reliability_diagram.svg + rs3_prediction_histogram.svg")
