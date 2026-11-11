"""Phase 13: validation and verification.

Model verification: hand-calculation benchmark, energy balance, physical scaling.
Model integrity: topology checks, reproducibility of the synthetic networks.
Forecast validation: rolling-origin cross-validation on real Ausgrid data.
There are no field measurements of voltage, so the network models are VERIFIED
(they solve the intended physics correctly) but not VALIDATED against the field.
Writes results/validation/validation_report.md
"""
import sys
import hashlib
import subprocess
from pathlib import Path
import numpy as np
import pandas as pd
import networkx as nx
import opendssdirect as dss
import xgboost as xgb

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
OUT = ROOT / "results" / "validation"
NETWORKS = ["zimbabwe_rural", "zimbabwe_urban", "european_lv"]
BUILDERS = {"zimbabwe_rural": "build_zim_rural.py", "zimbabwe_urban": "build_zim_urban.py"}
STEP_MIN = 15
checks = []


def record(name, network, passed, detail):
    checks.append({"check": name, "network": network, "result": "PASS" if passed else "FAIL", "detail": detail})
    print(f"  [{'PASS' if passed else 'FAIL'}] {name} ({network}): {detail}")


# 1. Hand-calculation benchmark --------------------------------------------
def analytical_benchmark():
    v_ll, r, x, p_kw, pf = 400.0, 0.1, 0.05, 40.0, 0.9
    vph = v_ll / np.sqrt(3)
    q_kvar = p_kw * np.tan(np.arccos(pf))
    s_ph = (p_kw + 1j * q_kvar) * 1000 / 3
    z = r + 1j * x
    vr = vph + 0j
    for _ in range(200):
        i = np.conj(s_ph / vr)
        vr = vph - i * z
    v_hand = abs(vr) / vph
    loss_hand = 3 * abs(i) ** 2 * r / 1000

    dss.Text.Command("clear")
    dss.Text.Command("new circuit.check basekv=0.4 pu=1.0 phases=3 bus1=src MVAsc3=1e8 MVAsc1=1e8")
    dss.Text.Command(f"new line.l1 bus1=src bus2=rcv phases=3 r1={r} x1={x} r0={r} x0={x} c1=0 c0=0 length=1")
    dss.Text.Command(f"new load.ld bus1=rcv phases=3 kv=0.4 kw={p_kw} pf={pf} model=1 vminpu=0.7")
    dss.Text.Command("set voltagebases=[0.4]")
    dss.Text.Command("calcvoltagebases")
    dss.Text.Command("solve")
    dss.Circuit.SetActiveBus("rcv")
    v_dss = float(np.mean(dss.Bus.puVmagAngle()[0::2]))
    dss.Circuit.SetActiveElement("Line.l1")
    loss_dss = dss.CktElement.Losses()[0] / 1000
    dv, dl = abs(v_dss - v_hand), 100 * abs(loss_dss - loss_hand) / loss_hand
    record("Hand-calculation benchmark", "test circuit", dv < 0.001 and dl < 1.0,
           f"voltage hand {v_hand:.5f} vs OpenDSS {v_dss:.5f} pu (diff {dv:.6f}); "
           f"losses hand {loss_hand:.4f} vs OpenDSS {loss_dss:.4f} kW (diff {dl:.3f}%)")


# 2. Energy balance -----------------------------------------------------------
def energy_balance(net):
    dss.Text.Command(f'compile "{ROOT / "opendss" / net / "Master.dss"}"')
    dss.Text.Command("set mode=yearly")
    dss.Text.Command(f"set stepsize={STEP_MIN}m")
    dss.Text.Command("set number=1")
    dss.Solution.Hour(0)
    dss.Solution.Seconds(0)
    worst, conv = 0.0, True
    for _ in range(24 * 60 // STEP_MIN):
        dss.Solution.Solve()
        conv = conv and dss.Solution.Converged()
        supply = -dss.Circuit.TotalPower()[0]
        loads = 0.0
        i = dss.Loads.First()
        while i > 0:
            loads += sum(dss.CktElement.Powers()[0::2])
            i = dss.Loads.Next()
        losses = dss.Circuit.Losses()[0] / 1000
        worst = max(worst, 100 * abs(supply - loads - losses) / supply)
    record("Energy balance (supply = loads + losses), every 15 min", net, conv and worst < 0.1,
           f"worst mismatch {worst:.5f}% of supply; all steps converged: {conv}")


# 3. Physical scaling -----------------------------------------------------------
def scaling(net):
    res = {}
    for k in (0.5, 1.0):
        dss.Text.Command(f'compile "{ROOT / "opendss" / net / "Master.dss"}"')
        dss.Text.Command("set mode=snapshot")
        dss.Text.Command(f"set loadmult={k}")
        dss.Solution.Solve()
        v = np.array(dss.Circuit.AllBusMagPu())
        res[k] = (float(v[v > 0].min()), dss.Circuit.Losses()[0] / 1000)
    ratio = res[0.5][1] / res[1.0][1]
    ok = res[0.5][0] > res[1.0][0] and 0.20 <= ratio <= 0.32
    record("Physical scaling (half load: higher voltage, about 1/4 losses)", net, ok,
           f"min voltage {res[1.0][0]:.4f} -> {res[0.5][0]:.4f} pu; loss ratio {ratio:.3f} (theory about 0.25)")


# 4. Topology and data integrity -------------------------------------------------
def integrity(net):
    cons = pd.read_csv(ROOT / "data" / "assets" / net / "consumers.csv", dtype={"bus": str})
    lines = pd.read_csv(ROOT / "data" / "assets" / net / "lines.csv")
    tx = pd.read_csv(ROOT / "data" / "assets" / net / "transformers.csv", dtype={"lv_bus": str})
    G = nx.read_graphml(ROOT / "models" / net / "network.graphml")
    missing = [b for b in cons["bus"] if b not in G]
    reach = nx.node_connected_component(G, tx.loc[0, "lv_bus"])
    unreachable = [b for b in cons["bus"] if b in G and b not in reach]
    dss.Text.Command(f'compile "{ROOT / "opendss" / net / "Master.dss"}"')
    n_dss = dss.Loads.Count()
    ok = nx.is_connected(G) and nx.is_tree(G) and not missing and not unreachable and n_dss == len(cons)
    record("Topology and data integrity", net, ok,
           f"connected {nx.is_connected(G)}, radial {nx.is_tree(G)}, customers {len(cons)} "
           f"(OpenDSS {n_dss}), missing {len(missing)}, unreachable {len(unreachable)}")
    default_rated = int((lines["norm_amps"] == 400).sum())
    if default_rated:
        print(f"  [NOTE] {net}: {default_rated} of {len(lines)} lines use the OpenDSS default 400 A rating; "
              "line loading % is indicative only.")
    return default_rated, len(lines)


# 5. Reproducibility -------------------------------------------------------------
def folder_hash(folder):
    h = hashlib.sha256()
    for f in sorted(folder.glob("*")):
        if f.is_file():
            h.update(f.name.encode())
            h.update(f.read_bytes())
    return h.hexdigest()


def reproducibility(net):
    folder = ROOT / "opendss" / net
    before = folder_hash(folder)
    subprocess.run([sys.executable, str(SRC / BUILDERS[net])], check=True, capture_output=True)
    after = folder_hash(folder)
    record("Reproducibility (rebuild gives identical files)", net, before == after,
           f"hash before {before[:12]}..., after {after[:12]}...")


# 6. Forecast rolling-origin cross-validation -----------------------------------
def forecast_cv():
    sys.path.insert(0, str(SRC))
    import forecast_zone as fz
    w = pd.read_parquet(fz.PROC / "ausgrid_zone_15min.parquet", columns=list(fz.SITES))
    folds = [("2025-11-01 00:15", "2026-01-01 00:15"), ("2026-01-01 00:15", "2026-03-01 00:15"),
             ("2026-03-01 00:15", "2026-05-01 00:15")]
    rows = []
    for name, key in fz.SITES.items():
        wx = pd.read_parquet(fz.PROC / "weather" / f"{key}.parquet")
        df = fz.features(w[name], wx)
        for a, b in folds:
            a, b = pd.Timestamp(a), pd.Timestamp(b)
            train = df[(df.index < a) & df["y"].notna()]
            test = df[(df.index >= a) & (df.index < b) & df["y"].notna() & df["lag_1d"].notna()
                      & df["lag_7d"].notna() & df["prev_day_mean"].notna()]
            preds = {"naive_7d": test["lag_7d"].values}
            for m in ["xgb_weather", "xgb_weather_level"]:
                cols, rel = fz.MODELS[m]
                model = xgb.XGBRegressor(**fz.PARAMS)
                target = train["y"] - train["prev_day_mean"] if rel else train["y"]
                ok = target.notna()
                model.fit(train.loc[ok, cols], target[ok])
                p = model.predict(test[cols])
                preds[m] = p + test["prev_day_mean"].values if rel else p
            act = test["y"].values
            base = np.mean(np.abs(preds["naive_7d"] - act))
            use_mape = act.min() > 0.1 * act.mean()
            for m, p in preds.items():
                e = p - act
                mae = float(np.mean(np.abs(e)))
                rows.append({"substation": key, "fold": f"{a:%b %Y} to {(b - pd.Timedelta(days=1)):%b %Y}",
                             "train_intervals": len(train), "model": m, "mae_mw": round(mae, 3),
                             "mape_pct": round(float(100 * np.mean(np.abs(e) / np.abs(act))), 2) if use_mape else np.nan,
                             "bias_mw": round(float(e.mean()), 3),
                             "skill_vs_naive_7d_pct": round(100 * (1 - mae / base), 1)})
        print(f"  cross-validated {key}")
    cv = pd.DataFrame(rows)
    for key in ["city_north", "branxton"]:
        s = cv[(cv["substation"] == key) & (cv["model"] == "xgb_weather")]["skill_vs_naive_7d_pct"]
        record("Forecast beats the last-week baseline in every fold", key, bool((s > 0).all()),
               f"skill by fold: {', '.join(f'{v}%' for v in s)}")
    s = cv[(cv["substation"] == "kogarah") & (cv["model"] == "xgb_weather_level")]["skill_vs_naive_7d_pct"]
    record("Forecast beats the last-week baseline in every fold (level-adjusted)", "kogarah",
           bool((s > 0).all()), f"skill by fold: {', '.join(f'{v}%' for v in s)}")
    return cv


LIMITATIONS = [
    "No field measurements of voltage or current: the network models are verified (they solve the "
    "intended physics correctly) but not validated against a real network. Field validation needs a utility partner.",
    "The two Zimbabwean networks are synthetic. Equipment values are approximate typical figures, not ZETDC data, "
    "and load profiles are synthetic hourly shapes.",
    "OpenDSS loads switch from constant power to constant impedance below 0.95 pu (its default). Customers at very "
    "low voltage therefore draw slightly less than nominal power, which softens the worst undervoltage results.",
    "European feeder lines without a published rating use OpenDSS's default 400 A, so its line loading % is indicative.",
    "DER, EV and flexibility studies use one simulated clear-sky day and hypothetical sizes and locations.",
    "Forecasts use actual (reanalysis) weather, i.e. a perfect weather forecast, so with-weather accuracy is optimistic.",
    "Reliability indices use assumed fault rates and cover LV faults only (no MV faults or load shedding).",
    "Asset health is stress-based (loading and voltage), not based on asset age, inspections or condition data.",
    "Harmonics, flicker and transient events are not modelled (they need measured spectra or time-domain models).",
    "Agents are deterministic rules tested on one day; they are decision support and require operator approval.",
]


def main():
    print("1. Hand-calculation benchmark")
    analytical_benchmark()
    print("2. Energy balance")
    for n in NETWORKS:
        energy_balance(n)
    print("3. Physical scaling")
    for n in NETWORKS:
        scaling(n)
    print("4. Topology and data integrity")
    notes = {n: integrity(n) for n in NETWORKS}
    print("5. Reproducibility")
    for n in BUILDERS:
        reproducibility(n)
    print("6. Forecast cross-validation (real Ausgrid data, this takes a minute or two)")
    cv = forecast_cv()

    OUT.mkdir(parents=True, exist_ok=True)
    t = pd.DataFrame(checks)
    t.to_csv(OUT / "validation_checks.csv", index=False)
    cv.to_csv(OUT / "forecast_cross_validation.csv", index=False)
    n_pass = int((t["result"] == "PASS").sum())
    md = ["# Validation report: QGEG LV Digital Twin", "",
          f"**{n_pass} of {len(t)} checks passed.**", "",
          "The network models are *verified* (they correctly solve the intended physics and are built as "
          "intended). They are not *validated* against field measurements, which were not available.", "",
          "## Checks", "", t.to_markdown(index=False), "",
          "## Forecast cross-validation (rolling origin, real Ausgrid data)", "",
          "Each fold trains only on data before it and tests on the following two months.", "",
          cv.to_markdown(index=False), ""]
    for n, (d, tot) in notes.items():
        if d:
            md.append(f"Note: {n} has {d} of {tot} lines at the OpenDSS default 400 A rating.")
    md += ["", "## Limitations", ""] + [f"- {x}" for x in LIMITATIONS]
    (OUT / "validation_report.md").write_text("\n".join(md), encoding="utf-8")

    print(f"\n=== {n_pass} of {len(t)} checks passed ===")
    print("\nForecast cross-validation (main models):")
    show = cv[cv["model"].isin(["naive_7d", "xgb_weather", "xgb_weather_level"])]
    print(show.pivot_table(index=["substation", "fold"], columns="model", values="mae_mw").round(3).to_string())
    print("\nSaved results/validation/validation_report.md")


if __name__ == "__main__":
    main()
