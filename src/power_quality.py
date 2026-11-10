"""Phase 7: power quality analytics (from simulation).

Covers steady-state voltage compliance, voltage unbalance, neutral current,
power factor, motor-starting voltage dips and fault-induced sags/swells.
Not covered: harmonics and flicker (need measured spectra or time-domain models).
Limits are indicative planning values; check against ZETDC / local standards.
"""
import sys
from pathlib import Path
import numpy as np
import pandas as pd
import opendssdirect as dss

ROOT = Path(__file__).resolve().parents[1]
FEEDER = sys.argv[1] if len(sys.argv) > 1 else "zimbabwe_rural"
STEP_MIN = 15
MASTER = ROOT / "opendss" / FEEDER / "Master.dss"
ASSETS = ROOT / "data" / "assets" / FEEDER
MON = ROOT / "results" / FEEDER / "monitoring"
OUT = ROOT / "results" / FEEDER / "power_quality"

V_LOW, V_HIGH = 0.94, 1.10
VUF_LIMIT = 2.0                    # % (EN 50160 / IEC planning level for LV)
DIP_FREQ, DIP_RARE = 4.0, 6.0      # % indicative motor-start dip limits
SAG, SWELL = 0.90, 1.10            # pu
START_MULT, START_PF = 6.0, 0.3    # direct-on-line motor start assumptions
FAULT_R = 0.01                     # ohm, phase-earth fault resistance
CLEAR_MULT = 5.0                   # fault current below this x rating = slow clearing risk
PHASE_NODE = {"A": [".1"], "B": [".2"], "C": [".3"], "3ph": [".1", ".2", ".3"]}
A = np.exp(2j * np.pi / 3)


def node_index():
    nodes = [n.lower() for n in dss.Circuit.AllNodeNames()]
    return nodes, {n: i for i, n in enumerate(nodes)}


def complex_volts():
    v = np.array(dss.Circuit.AllBusVolts())
    return v[0::2] + 1j * v[1::2]


def snapshot():
    dss.Text.Command(f'compile "{MASTER}"')
    dss.Text.Command("set mode=snapshot")


def main():
    s = str
    consumers = pd.read_csv(ASSETS / "consumers.csv", dtype={"bus": s, "load": s, "feeder": s})
    buses = pd.read_csv(ASSETS / "buses.csv", dtype={"bus": s, "feeder": s})
    lines = pd.read_csv(ASSETS / "lines.csv", dtype={"line": s, "bus1": s, "bus2": s})
    tx = pd.read_csv(ASSETS / "transformers.csv", dtype={"hv_bus": s, "lv_bus": s})
    lv_bus, hv_bus = tx.loc[0, "lv_bus"], tx.loc[0, "hv_bus"]
    feeder_of = {b: f for b, f in zip(buses["bus"], buses["feeder"]) if isinstance(f, str)}
    dist_of = dict(zip(buses["bus"], buses["distance_m"]))
    lines["feeder"] = [feeder_of.get(b2, feeder_of.get(b1)) for b1, b2 in zip(lines["bus1"], lines["bus2"])]
    heads = lines[(lines["bus1"] == lv_bus) | (lines["bus2"] == lv_bus)]

    # 1. Daily run: unbalance, neutral current, power factor
    dss.Text.Command(f'compile "{MASTER}"')
    dss.Text.Command("set mode=yearly")
    dss.Text.Command(f"set stepsize={STEP_MIN}m")
    dss.Text.Command("set number=1")
    dss.Solution.Hour(0)
    dss.Solution.Seconds(0)
    nodes, idx = node_index()
    tri = sorted({n.split(".")[0] for n in nodes
                  if all(f"{n.split('.')[0]}.{k}" in idx for k in (1, 2, 3))} - {hv_bus})
    i1 = np.array([idx[f"{b}.1"] for b in tri])
    i2 = np.array([idx[f"{b}.2"] for b in tri])
    i3 = np.array([idx[f"{b}.3"] for b in tri])

    n_steps = 24 * 60 // STEP_MIN
    vuf = np.zeros((n_steps, len(tri)))
    neutral_rows, pf_rows = [], []
    for t in range(n_steps):
        dss.Solution.Solve()
        hour = round(dss.Solution.DblHour(), 3)
        v = complex_volts()
        va, vb, vc = v[i1], v[i2], v[i3]
        v1 = (va + A * vb + A * A * vc) / 3
        v2 = (va + A * A * vb + A * vc) / 3
        vuf[t] = 100 * np.abs(v2) / np.abs(v1)
        for r in heads.itertuples(index=False):
            dss.Circuit.SetActiveElement(f"Line.{r.line}")
            nph = dss.CktElement.NumPhases()
            c = np.array(dss.CktElement.Currents()[:2 * nph])
            ph = c[0::2] + 1j * c[1::2]
            neutral_rows.append({"hour": hour, "feeder": r.feeder,
                                 "i_phase_max": np.abs(ph).max(), "i_neutral": abs(ph.sum())})
        dss.Transformers.First()
        dss.Circuit.SetActiveElement(f"Transformer.{dss.Transformers.Name()}")
        n = dss.CktElement.NumConductors()
        pw = dss.CktElement.Powers()[: 2 * n]
        p, q = sum(pw[0::2]), sum(pw[1::2])
        pf_rows.append({"hour": hour, "kw": p, "pf": p / (p * p + q * q) ** 0.5 if p else 1.0})

    bt = pd.DataFrame({"bus": tri, "vuf_max_pct": vuf.max(axis=0).round(3),
                       "vuf_p95_pct": np.percentile(vuf, 95, axis=0).round(3),
                       "pct_time_over_limit": (100 * (vuf > VUF_LIMIT).mean(axis=0)).round(1)})
    bt["feeder"] = bt["bus"].map(feeder_of)
    bt["distance_m"] = bt["bus"].map(dist_of)
    bt = bt.sort_values("vuf_max_pct", ascending=False)

    nd = pd.DataFrame(neutral_rows)
    ns = nd.groupby("feeder").agg(i_neutral_max=("i_neutral", "max"),
                                  i_phase_max=("i_phase_max", "max")).round(1).reset_index()
    ns["neutral_vs_phase_pct"] = (100 * ns["i_neutral_max"] / ns["i_phase_max"]).round(0)
    pfd = pd.DataFrame(pf_rows)

    # 2. Voltage compliance (from Phase 4 monitoring)
    cv = pd.read_csv(MON / "customer_voltage_pu.csv").drop(columns="hour")
    ok = (cv.values >= V_LOW) & (cv.values <= V_HIGH)
    within_all = 100 * ok.mean()
    per_cust = 100 * ok.mean(axis=0)
    worst_cust, worst_pct = cv.columns[per_cust.argmin()], per_cust.min()

    # 3. Motor-starting dips (snapshot, all other loads at peak = worst case)
    snapshot()
    nodes, idx = node_index()
    cust_nodes = [[idx[f"{b}{sfx}"] for sfx in PHASE_NODE[p]]
                  for b, p in zip(consumers["bus"], consumers["phase"])]

    def cust_v(fn):
        v = np.array(dss.Circuit.AllBusMagPu())
        return np.array([fn(v[ix]) for ix in cust_nodes])

    motors = consumers[(consumers["phase"] == "3ph") &
                       consumers["load"].str.contains("pump|mill", case=False)]
    mrows = []
    for m in motors.itertuples(index=False):
        snapshot()
        dss.Text.Command(f"edit Load.{m.load} enabled=no")
        dss.Solution.Solve()
        before = cust_v(np.min)
        s_start = START_MULT * m.kw / m.pf
        p_st, q_st = s_start * START_PF, s_start * np.sqrt(1 - START_PF ** 2)
        dss.Text.Command(f"edit Load.{m.load} enabled=yes model=2 kW={p_st:.2f} kvar={q_st:.2f}")
        dss.Solution.Solve()
        dip = 100 * (before - cust_v(np.min)) / before
        mask = consumers["load"].values != m.load
        j = int(np.argmax(np.where(mask, dip, -1)))
        mrows.append({"motor": m.load, "rated_kw": m.kw, "start_kva": round(s_start, 1),
                      "max_dip_pct": round(float(dip[mask].max()), 2),
                      "worst_customer": consumers["consumer_id"].iloc[j],
                      f"customers_over_{DIP_FREQ:.0f}pct": int((dip[mask] > DIP_FREQ).sum()),
                      f"customers_over_{DIP_RARE:.0f}pct": int((dip[mask] > DIP_RARE).sum())})
    mdf = pd.DataFrame(mrows)

    # 4. Fault-induced sags/swells: phase-earth fault at the far end of each feeder
    frows = []
    for f in sorted(consumers["feeder"].dropna().unique()):
        cand = [b for b in tri if feeder_of.get(b) == f]
        if not cand:
            continue
        fb = max(cand, key=lambda b: dist_of.get(b, 0))
        snapshot()
        dss.Text.Command(f"New Fault.f1 phases=1 bus1={fb}.1 r={FAULT_R}")
        dss.Solution.Solve()
        dss.Circuit.SetActiveElement("Fault.f1")
        i_f = dss.CktElement.CurrentsMagAng()[0]
        vmin, vmax = cust_v(np.min), cust_v(np.max)
        other = consumers["feeder"].values != f
        rating = heads.loc[heads["feeder"] == f, "norm_amps"].max()
        frows.append({"feeder": f, "fault_bus": fb, "distance_m": dist_of.get(fb),
                      "fault_current_a": round(i_f, 0),
                      "x_cable_rating": round(i_f / rating, 1) if rating else np.nan,
                      "slow_clearing_risk": bool(rating and i_f < CLEAR_MULT * rating),
                      "customers_sag": int((vmin < SAG).sum()),
                      "customers_sag_other_feeders": int((vmin[other] < SAG).sum()),
                      "min_v_other_feeders_pu": round(float(vmin[other].min()), 3) if other.any() else np.nan,
                      "customers_swell": int((vmax > SWELL).sum())})
    fdf = pd.DataFrame(frows)

    OUT.mkdir(parents=True, exist_ok=True)
    bt.to_csv(OUT / "bus_unbalance.csv", index=False)
    nd.to_csv(OUT / "feeder_neutral_timeseries.csv", index=False)
    ns.to_csv(OUT / "feeder_neutral_summary.csv", index=False)
    pfd.to_csv(OUT / "transformer_pf.csv", index=False)
    mdf.to_csv(OUT / "motor_start_dips.csv", index=False)
    fdf.to_csv(OUT / "fault_sags.csv", index=False)

    print(f"=== {FEEDER}: power quality (simulated) ===")
    print(f"Voltage compliance: {within_all:.1f}% of customer readings within {V_LOW}-{V_HIGH} pu; "
          f"worst customer {worst_cust} at {worst_pct:.1f}%")
    top = bt.iloc[0]
    print(f"Voltage unbalance: {(bt['vuf_max_pct'] > VUF_LIMIT).sum()} of {len(bt)} three-phase points "
          f"exceed {VUF_LIMIT}% at some time; worst {top['vuf_max_pct']:.2f}% at bus {top['bus']} "
          f"({top['feeder']}, {top['distance_m']} m)")
    print("Worst unbalance by feeder:")
    print(bt.groupby("feeder")[["vuf_max_pct", "vuf_p95_pct"]].max().round(2).to_string())
    print("\nNeutral current at feeder heads:")
    print(ns.to_string(index=False))
    print(f"\nTransformer power factor: min {pfd['pf'].min():.3f}, "
          f"at peak load {pfd.loc[pfd['kw'].idxmax(), 'pf']:.3f}")
    print("\nMotor-starting dips (direct-on-line, others at peak):")
    print(mdf.to_string(index=False) if len(mdf) else "  no pump or mill motors on this network")
    print("\nPhase-earth fault at far end of each feeder:")
    print(fdf.to_string(index=False))
    print("\nNot covered: harmonics and flicker (need measured spectra or time-domain models).")
    print(f"Saved to results/{FEEDER}/power_quality/")


if __name__ == "__main__":
    main()
