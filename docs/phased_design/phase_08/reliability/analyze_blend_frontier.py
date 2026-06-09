"""RS-3 production-adjustment analysis — blend-weight & isotonic frontier (real 9-ticker universe).

Tests the proposal: "drop the lazy 0.5/0.5; move to 0.8 raw + 0.2 empirical, or fit isotonic on the
pooled classifier — squeeze back the AUC 0.710 discrimination while keeping MACE strictly under 0.10."

We sweep the convex blend weight w (P = w·raw + (1-w)·empirical) and add both isotonic surfaces, scoring
each on the SAME leave-one-ticker-out OOF surfaces used by the RS-3 gate:
  * AUC (rank / "alpha")      * MACE (calibration; the 0.10 gate)
  * resolution (Murphy sharpness — what shrinkage destroys)   * variance-shrinkage index
Then an EPISODE-CLUSTER BOOTSTRAP of MACE for the key candidates, to ask the risk-control question
honestly: is any *sharper* surface *strictly* (with margin) below 0.10 on n=162, or only borderline?

Run from the repo root:
    python3 docs/phased_design/phase_08/reliability/analyze_blend_frontier.py
Writes: rs3_blend_frontier.json, rs3_blend_frontier.csv, rs3_blend_frontier.svg. Educational research only.
"""
import os, sys, json, csv
sys.path.insert(0, "src")
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from yearline_universe import load_universe_config
from yearline_universe.ticker_pipeline import _build_foundation
from yearline_universe.success_models import build_success_model_table
from yearline_universe.success_calibration import success_oof_surfaces
from yearline_universe.success_reliability import brier_decomposition
from yearline_universe.models import _binned_mace, _auc
from yearline_universe.calibration import GATE_MAX_MACE  # 0.10

HERE = "docs/phased_design/phase_08/reliability"
GATE = GATE_MAX_MACE

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
surf = success_oof_surfaces(table, n_splits=5)
assert surf.get("available"), surf
y = np.asarray(surf["y"], float)
raw = np.asarray(surf["surfaces"]["classifier_raw"], float)
emp = np.asarray(surf["surfaces"]["empirical_baseline"], float)
iso_raw = np.asarray(surf["surfaces"]["classifier_isotonic"], float)
iso_blend = np.asarray(surf["surfaces"]["blend_isotonic"], float)
episode = np.asarray(surf["episode"])
base = float(surf["base_rate"])
emp_filled = np.where(np.isfinite(emp), emp, base)
var_raw = float(np.var(raw[np.isfinite(raw)]))


def metrics(p):
    m = np.isfinite(p) & np.isfinite(y)
    pp, yy = np.clip(p[m], 0, 1), y[m]
    bd = brier_decomposition(yy, pp)
    var = float(np.var(pp))
    return {"auc": _auc(yy, pp), "mace": _binned_mace(yy, pp),
            "resolution": bd["resolution"], "std": round(float(np.std(pp)), 4),
            "var": round(var, 5),
            "vsi": (round(1 - var / var_raw, 3) if var_raw > 1e-12 else None),
            "n": int(m.sum())}


rows = []
# pure empirical (w=0) and the full blend sweep up to pure raw (w=1)
for w in [0.0, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]:
    p = np.clip(w * raw + (1 - w) * emp_filled, 0, 1)
    name = {0.0: "empirical (w=0.0)", 1.0: "raw classifier (w=1.0)"}.get(w, f"blend w={w:.1f}")
    rows.append({"surface": name, **metrics(p)})
rows.append({"surface": "isotonic(raw)", **metrics(iso_raw)})
rows.append({"surface": "isotonic(blend 0.5)", **metrics(iso_blend)})

for r in rows:
    r["gate_pass"] = bool(r["mace"] is not None and r["mace"] <= GATE)

# --- episode-cluster bootstrap of MACE (respects within-episode correlation) ---
def bootstrap_mace(p, n_boot=2000, seed=0):
    rng = np.random.default_rng(seed)
    m = np.isfinite(p) & np.isfinite(y)
    pp, yy, ep = np.clip(p[m], 0, 1), y[m], episode[m]
    uniq = np.unique(ep)
    idx_by_ep = {e: np.where(ep == e)[0] for e in uniq}
    out = []
    for _ in range(n_boot):
        pick = rng.choice(uniq, size=len(uniq), replace=True)
        sel = np.concatenate([idx_by_ep[e] for e in pick])
        val = _binned_mace(yy[sel], pp[sel])
        if val is not None:
            out.append(val)
    out = np.array(out)
    return {"mace_mean": round(float(out.mean()), 4),
            "mace_p05": round(float(np.percentile(out, 5)), 4),
            "mace_p95": round(float(np.percentile(out, 95)), 4),
            "p_under_gate": round(float(np.mean(out <= GATE)), 3)}

boot_targets = {"blend w=0.5": np.clip(0.5 * raw + 0.5 * emp_filled, 0, 1),
                "blend w=0.7": np.clip(0.7 * raw + 0.3 * emp_filled, 0, 1),
                "blend w=0.8": np.clip(0.8 * raw + 0.2 * emp_filled, 0, 1),
                "raw classifier (w=1.0)": raw,
                "isotonic(raw)": iso_raw}
boot = {k: bootstrap_mace(v) for k, v in boot_targets.items()}

# --- choose the sharpest gate-passing surface (resolution-max s.t. MACE<=gate) ---
passing = [r for r in rows if r["gate_pass"] and r["resolution"] is not None]
sharpest = max(passing, key=lambda r: r["resolution"]) if passing else None
# the same, but requiring the bootstrap upper bound (p95) under the gate — the "strict" reading
strict_ok = {k for k, b in boot.items() if b["mace_p95"] <= GATE}

out = {"n": int(np.isfinite(raw).sum()), "base_rate": round(base, 4), "gate_max_mace": GATE,
       "var_raw": round(var_raw, 5), "auc_raw": metrics(raw)["auc"],
       "frontier": rows, "bootstrap_mace": boot,
       "sharpest_gate_passing_pointwise": (sharpest["surface"] if sharpest else None),
       "surfaces_with_p95_under_gate": sorted(strict_ok),
       "notes": [
           "All surfaces are leave-one-ticker-out OOF; resolution = Murphy informative sharpness.",
           "AUC is rank-based: a monotone recalibration (isotonic) preserves it; a flat-anchor blend "
           "barely changes it (empirical AUC ~0.49), so the 'alpha' is NOT what shrinkage destroys.",
           "gate_pass is pointwise MACE<=0.10; p95<=0.10 is the stricter 'with margin on n=162' reading.",
           "Educational research only; not financial advice.",
       ]}
json.dump(out, open(f"{HERE}/rs3_blend_frontier.json", "w"), indent=2, default=str)

with open(f"{HERE}/rs3_blend_frontier.csv", "w", newline="") as fh:
    wri = csv.writer(fh)
    wri.writerow(["surface", "auc", "mace", "resolution", "std", "vsi", "gate_pass"])
    for r in rows:
        wri.writerow([r["surface"], r["auc"], r["mace"], r["resolution"], r["std"], r["vsi"], r["gate_pass"]])

# --- figure: the calibration frontier (MACE vs resolution), gate line, blend path ---
fig, ax = plt.subplots(figsize=(8, 6))
blendrows = [r for r in rows if r["surface"].startswith(("blend", "raw", "empirical"))]
bx = [r["resolution"] for r in blendrows]; by = [r["mace"] for r in blendrows]
ax.plot(bx, by, "o-", color="0.5", label="convex blend path (w: 0→1)")
for r in blendrows:
    ax.annotate(r["surface"].replace(" classifier", "").replace("blend ", ""),
                (r["resolution"], r["mace"]), fontsize=8, xytext=(4, 4), textcoords="offset points")
for r in rows:
    if r["surface"].startswith("isotonic"):
        ax.plot(r["resolution"], r["mace"], "D", ms=11, label=r["surface"])
ax.axhline(GATE, color="crimson", ls="--", lw=2, label=f"MACE gate ({GATE})")
ax.fill_between([0, max(bx) * 1.15], 0, GATE, color="green", alpha=0.06)
ax.set_xlabel("resolution  (informative sharpness — higher is better →)")
ax.set_ylabel("MACE  (miscalibration — lower is better ↓)")
ax.set_title("RS-3 calibration frontier — sharpness you can buy under the 0.10 gate", fontweight="bold")
ax.set_xlim(0, max(bx) * 1.15); ax.set_ylim(0, max(by) * 1.15)
ax.legend(loc="upper left", fontsize=9); ax.grid(True, ls=":", alpha=0.6)
fig.tight_layout(); fig.savefig(f"{HERE}/rs3_blend_frontier.svg"); plt.close(fig)

print("=== RS-3 PRODUCTION-ADJUSTMENT FRONTIER (real universe) ===")
print(f"n={out['n']}  base_rate={out['base_rate']}  gate(MACE)<= {GATE}")
print(f"{'surface':>24}  {'AUC':>6} {'MACE':>6} {'resol':>6} {'std':>6} {'VSI':>6}  gate")
for r in rows:
    a = f"{r['auc']:.3f}" if r['auc'] is not None else "  -  "
    print(f"{r['surface']:>24}  {a:>6} {r['mace']:.3f}  {r['resolution']:.4f} {r['std']:.3f} "
          f"{str(r['vsi']):>6}  {'PASS' if r['gate_pass'] else 'fail'}")
print("\n--- episode-cluster bootstrap MACE (2000 reps) ---")
for k, b in boot.items():
    print(f"{k:>24}  mean {b['mace_mean']}  90%CI [{b['mace_p05']}, {b['mace_p95']}]  "
          f"P(MACE<=gate)={b['p_under_gate']}")
print(f"\nsharpest gate-passing (pointwise): {out['sharpest_gate_passing_pointwise']}")
print(f"surfaces with p95<=gate (strict):  {out['surfaces_with_p95_under_gate']}")
print(f"\nwrote {HERE}/rs3_blend_frontier.json + .csv + .svg")
