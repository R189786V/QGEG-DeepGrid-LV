"""Phase 6: DER intelligence - rooftop PV, home batteries, EV charging, hosting capacity.

All DER is SIMULATED and hypothetical. Sizes, locations and profiles are assumptions.
The solar profile is a synthetic clear-sky day (worst case for voltage rise).
PV is assumed grid-tied and exporting.
"""
import sys
import math
import random
from pathlib import Path
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import opendssdirect as dss

ROOT = Path(__file__).resolve().parents[1]
FEEDER = sys.argv[1] if len(sys.argv) > 1 else "zimbabwe_urban"
MASTER = ROOT / "opendss" / FEEDER / "Master.dss"
ASSETS = ROOT / "data" / "assets" / FEEDER
OUT = ROOT / "results" / FEEDER / "der"

STEP_MIN = 15
V_LOW, V_HIGH = 0.94, 1.10
PV_KW = 5.0                 # per participating home (inverter AC rating)
BAT_KW, BAT_KWH = 5.0, 10.0
EV_KW = 7.0
PENETRATION = 0.5           # share of homes with PV in the scenario runs
EV_SHARE = 0.3              # share of homes with an EV
PHASE_NODE = {"A": ".1", "B": ".2", "C": ".3"}


def shape96(fn):
    return [round(fn(t * 0.25), 4) for t in range(96)]


SOLAR = shape96(lambda h: max(0.0, math.sin(math.pi * (h - 6) / 12)) if 6 < h < 18 else 0.0)
BAT = shape96(lambda h: -0.5 if 10 <= h < 14 else (0.67 if 18 <= h < 21 else 0.0))
EV = shape96(lambda h: 1.0 if 18 <= h < 20 else 0.0)


def run_day(cons, pv_ids, bat_ids, ev_ids):
    dss.Text.Command(f'compile "{MASTER}"')
    for name, vals in [("der_solar", SOLAR), ("der_bat", BAT), ("der_ev", EV)]:
        dss.Text.Command(f"New Loadshape.{name} npts=96 interval=0.25 "
                         f"mult=({' '.join(str(v) for v in vals)})")
    for r in cons.itertuples(index=False):
        node = f"{r.bus}{PHASE_NODE[r.phase]}"
        cid = r.consumer_id
        if cid in pv_ids:
            dss.Text.Command(f"New PVSystem.pv_{cid} phases=1 bus1={node} kV=0.23 kVA={PV_KW} "
                             f"Pmpp={PV_KW} irradiance=1 %cutin=0.05 %cutout=0.05 pf=1 "
                             f"daily=der_solar yearly=der_solar")
        if cid in bat_ids:
            dss.Text.Command(f"New Storage.bat_{cid} phases=1 bus1={node} kV=0.23 kWrated={BAT_KW} "
                             f"kWhrated={BAT_KWH} %stored=20 %reserve=10 dispmode=follow "
                             f"daily=der_bat yearly=der_bat")
        if cid in ev_ids:
            dss.Text.Command(f"New Load.ev_{cid} phases=1 bus1={node} kV=0.23 kW={EV_KW} pf=1 "
                             f"model=1 daily=der_ev yearly=der_ev")

    dss.Text.Command("set mode=yearly")
    dss.Text.Command(f"set stepsize={STEP_MIN}m")
    dss.Text.Command("set number=1")
    dss.Solution.Hour(0)
    dss.Solution.Seconds(0)

    rows = []
    for _ in range(24 * 60 // STEP_MIN):
        dss.Solution.Solve()
        if not dss.Solution.Converged():
            raise RuntimeError(f"Did not converge at hour {dss.Solution.DblHour()}")
        v = np.array(dss.Circuit.AllBusMagPu())
        v = v[v > 0]
        dss.Transformers.First()
        kva = dss.Transformers.kVA()
        dss.Circuit.SetActiveElement(f"Transformer.{dss.Transformers.Name()}")
        n = dss.CktElement.NumConductors()
        pw = dss.CktElement.Powers()[: 2 * n]
        p, q = sum(pw[0::2]), sum(pw[1::2])
        mx = 0.0
        i = dss.Lines.First()
        while i > 0:
            na = dss.Lines.NormAmps()
            if na > 0:
                nph = dss.CktElement.NumPhases()
                mx = max(mx, 100 * max(dss.CktElement.CurrentsMagAng()[0:2 * nph:2]) / na)
            i = dss.Lines.Next()
        rows.append({"hour": round(dss.Solution.DblHour(), 3), "tx_kw": p,
                     "tx_loading_pct": 100 * (p * p + q * q) ** 0.5 / kva,
                     "v_min": v.min(), "v_max": v.max(), "max_line_pct": mx,
                     "losses_kw": dss.Circuit.Losses()[0] / 1000})
    return pd.DataFrame(rows)


def summarise(df):
    h = STEP_MIN / 60
    return {"peak_import_kw": df["tx_kw"].max(),
            "max_export_kw": max(0.0, -df["tx_kw"].min()),
            "reverse_flow_h": (df["tx_kw"] < 0).sum() * h,
            "max_tx_loading_pct": df["tx_loading_pct"].max(),
            "v_max_pu": df["v_max"].max(), "h_over_v": (df["v_max"] > V_HIGH).sum() * h,
            "v_min_pu": df["v_min"].min(), "h_under_v": (df["v_min"] < V_LOW).sum() * h,
            "max_line_pct": df["max_line_pct"].max(),
            "losses_kwh": df["losses_kw"].sum() * h}


def main():
    cons = pd.read_csv(ASSETS / "consumers.csv", dtype={"bus": str, "load": str})
    cons = cons[cons["phase"].isin(["A", "B", "C"])].reset_index(drop=True)
    ids = cons["consumer_id"].tolist()
    pv_order, ev_order = ids[:], ids[:]
    random.Random(1).shuffle(pv_order)
    random.Random(2).shuffle(ev_order)

    def take(order, share):
        return set(order[:round(share * len(order))])

    pv, ev = take(pv_order, PENETRATION), take(ev_order, EV_SHARE)
    p, e = int(PENETRATION * 100), int(EV_SHARE * 100)
    scenarios = {
        "base": (set(), set(), set()),
        f"pv{p}": (pv, set(), set()),
        f"pv{p}+battery": (pv, pv, set()),
        f"ev{e}": (set(), set(), ev),
        f"pv{p}+battery+ev{e}": (pv, pv, ev),
    }

    OUT.mkdir(parents=True, exist_ok=True)
    results, series = [], {}
    for name, (a, b, c) in scenarios.items():
        print(f"Running scenario {name} ...")
        df = run_day(cons, a, b, c)
        series[name] = df
        df.to_csv(OUT / f"timeseries_{name.replace('+', '_')}.csv", index=False)
        results.append({"scenario": name, "pv_kw": len(a) * PV_KW, "batteries": len(b),
                        "evs": len(c), **summarise(df)})
    table = pd.DataFrame(results).round(3)
    table.to_csv(OUT / "scenario_comparison.csv", index=False)

    print("Running hosting capacity sweep ...")
    hc = []
    for share in [k / 10 for k in range(11)]:
        sel = take(pv_order, share)
        s = summarise(run_day(cons, sel, set(), set()))
        viol = [n for n, bad in [("overvoltage", s["v_max_pu"] > V_HIGH),
                                 ("line_overload", s["max_line_pct"] > 100),
                                 ("transformer_overload", s["max_tx_loading_pct"] > 100)] if bad]
        hc.append({"pv_share_pct": int(share * 100), "pv_kw": len(sel) * PV_KW,
                   "v_max_pu": round(s["v_max_pu"], 4), "max_line_pct": round(s["max_line_pct"], 1),
                   "max_tx_loading_pct": round(s["max_tx_loading_pct"], 1),
                   "max_export_kw": round(s["max_export_kw"], 1),
                   "violations": ", ".join(viol) or "none"})
    hc = pd.DataFrame(hc)
    hc.to_csv(OUT / "hosting_capacity.csv", index=False)

    fig, ax = plt.subplots(3, 1, figsize=(10, 9), sharex=True)
    for name, df in series.items():
        ax[0].plot(df["hour"], df["tx_kw"], label=name)
        ax[1].plot(df["hour"], df["v_max"], label=name)
        ax[2].plot(df["hour"], df["v_min"], label=name)
    ax[0].axhline(0, color="black", linewidth=0.8)
    ax[0].set_ylabel("Transformer kW\n(negative = export)")
    ax[1].axhline(V_HIGH, color="red", linestyle="--")
    ax[1].set_ylabel("Highest voltage (pu)")
    ax[2].axhline(V_LOW, color="red", linestyle="--")
    ax[2].set_ylabel("Lowest voltage (pu)")
    ax[2].set_xlabel("Hour of day")
    ax[0].legend(fontsize=8)
    fig.suptitle(f"{FEEDER}: DER scenarios (simulated)")
    fig.tight_layout()
    fig.savefig(OUT / "der_scenarios.png", dpi=150)

    print(f"\n=== {FEEDER}: DER scenarios (simulated, {len(ids)} single-phase customers) ===")
    print(table[["scenario", "pv_kw", "evs", "peak_import_kw", "max_export_kw", "reverse_flow_h",
                 "max_tx_loading_pct", "v_max_pu", "v_min_pu", "max_line_pct"]].to_string(index=False))
    print("\nHosting capacity sweep (PV only):")
    print(hc.to_string(index=False))
    bad = hc[hc["violations"] != "none"]
    if bad.empty:
        print(f"\nHosting capacity: at least 100% of homes ({hc['pv_kw'].max():.0f} kW) with no violations")
    else:
        first = bad.iloc[0]
        ok = hc[hc["pv_share_pct"] < first["pv_share_pct"]].iloc[-1]
        print(f"\nHosting capacity: about {ok['pv_share_pct']}% of homes ({ok['pv_kw']:.0f} kW). "
              f"First violation at {first['pv_share_pct']}%: {first['violations']}")
    print(f"Saved to results/{FEEDER}/der/")


if __name__ == "__main__":
    main()
