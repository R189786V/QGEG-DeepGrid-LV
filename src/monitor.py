"""Phase 4: LV monitoring and visibility (simulated measurements).

Readings come from the OpenDSS daily simulation, not field telemetry.
The limits, alerts and reports are what would run on real LV monitor data.
"""
import sys
from pathlib import Path
import numpy as np
import pandas as pd
import opendssdirect as dss

ROOT = Path(__file__).resolve().parents[1]
FEEDER = sys.argv[1] if len(sys.argv) > 1 else "zimbabwe_rural"
STEP_MIN = int(sys.argv[2]) if len(sys.argv) > 2 else 15
MASTER = ROOT / "opendss" / FEEDER / "Master.dss"
ASSETS = ROOT / "data" / "assets" / FEEDER
OUT = ROOT / "results" / FEEDER / "monitoring"

V_LOW, V_CRIT, V_HIGH = 0.94, 0.90, 1.10   # pu of 230 V
LOAD_WARN, LOAD_CRIT = 80.0, 100.0          # % of rating
IMBAL_WARN = 30.0                           # % current imbalance at feeder head
IMBAL_MIN_LOAD = 30.0                       # and busiest phase above this % of rating
PHASE_NODE = {"A": [".1"], "B": [".2"], "C": [".3"], "3ph": [".1", ".2", ".3"]}


def hhmm(hour):
    h = hour % 24
    return f"{int(h):02d}:{int(round((h % 1) * 60)) % 60:02d}"


def read_assets():
    s = str
    consumers = pd.read_csv(ASSETS / "consumers.csv", dtype={"bus": s, "load": s})
    lines = pd.read_csv(ASSETS / "lines.csv", dtype={"line": s, "bus1": s, "bus2": s})
    buses = pd.read_csv(ASSETS / "buses.csv", dtype={"bus": s, "feeder": s})
    tx = pd.read_csv(ASSETS / "transformers.csv", dtype={"hv_bus": s, "lv_bus": s})
    return consumers, lines, buses, tx


def setup():
    dss.Text.Command(f'compile "{MASTER}"')
    dss.Text.Command("set mode=yearly")
    dss.Text.Command(f"set stepsize={STEP_MIN}m")
    dss.Text.Command("set number=1")
    dss.Solution.Hour(0)
    dss.Solution.Seconds(0)


def merge_events(raw, hours):
    cols = ["type", "asset", "severity", "start", "end", "duration_min", "worst_value"]
    if not raw:
        return pd.DataFrame(columns=cols)
    df = pd.DataFrame(raw, columns=["step", "type", "asset", "severity", "value"])
    out = []
    for (typ, asset), g in df.groupby(["type", "asset"]):
        g = g.sort_values("step")
        for _, e in g.groupby((g["step"].diff() != 1).cumsum()):
            worst = e["value"].min() if typ == "undervoltage" else e["value"].max()
            out.append({"type": typ, "asset": asset,
                        "severity": "critical" if (e["severity"] == "critical").any() else "warning",
                        "start": hhmm(hours[e["step"].iloc[0]]),
                        "end": hhmm(hours[e["step"].iloc[-1]]),
                        "duration_min": len(e) * STEP_MIN,
                        "worst_value": round(worst, 4)})
    return pd.DataFrame(out, columns=cols).sort_values(["severity", "start"])


def main():
    consumers, lines, buses, tx = read_assets()
    feeder_of = {b: f for b, f in zip(buses["bus"], buses["feeder"]) if isinstance(f, str)}
    lv_bus = tx.loc[0, "lv_bus"]
    lines["feeder"] = [feeder_of.get(b2, feeder_of.get(b1)) for b1, b2 in zip(lines["bus1"], lines["bus2"])]
    heads = lines[(lines["bus1"] == lv_bus) | (lines["bus2"] == lv_bus)]

    setup()
    nodes = [n.lower() for n in dss.Circuit.AllNodeNames()]
    idx = {n: i for i, n in enumerate(nodes)}
    cust_nodes = [[idx[f"{b}{sfx}"] for sfx in PHASE_NODE[p]]
                  for b, p in zip(consumers["bus"], consumers["phase"])]

    line_names, line_norm = [], []
    i = dss.Lines.First()
    while i > 0:
        line_names.append(dss.Lines.Name())
        line_norm.append(dss.Lines.NormAmps())
        i = dss.Lines.Next()
    line_norm = np.array(line_norm, dtype=float)

    n_steps = 24 * 60 // STEP_MIN
    hours = []
    cust_v = np.zeros((n_steps, len(consumers)))
    line_i = np.zeros((n_steps, len(line_names)))
    feeder_rows, tx_rows = [], []

    for t in range(n_steps):
        dss.Solution.Solve()
        hour = round(dss.Solution.DblHour(), 3)
        hours.append(hour)
        v = np.array(dss.Circuit.AllBusMagPu())
        cust_v[t] = [v[ix].min() for ix in cust_nodes]

        k = 0
        i = dss.Lines.First()
        while i > 0:
            nph = dss.CktElement.NumPhases()
            line_i[t, k] = max(dss.CktElement.CurrentsMagAng()[0:2 * nph:2])
            k += 1
            i = dss.Lines.Next()

        for r in heads.itertuples(index=False):
            dss.Circuit.SetActiveElement(f"Line.{r.line}")
            nph = dss.CktElement.NumPhases()
            pw = dss.CktElement.Powers()[:2 * nph]
            ia = dss.CktElement.CurrentsMagAng()[0:2 * nph:2]
            sign = 1 if r.bus1 == lv_bus else -1
            mean = float(np.mean(ia))
            row = {"hour": hour, "feeder": r.feeder, "head_line": r.line,
                   "kw": sign * sum(pw[0::2]), "kvar": sign * sum(pw[1::2]),
                   "imbalance_pct": 100 * (max(ia) - mean) / mean if mean > 0 else 0.0,
                   "i_max_pct": 100 * max(ia) / r.norm_amps}
            row.update({f"i_{ph}": a for ph, a in zip("abc", ia)})
            feeder_rows.append(row)

        dss.Transformers.First()
        kva_rating = dss.Transformers.kVA()
        dss.Circuit.SetActiveElement(f"Transformer.{dss.Transformers.Name()}")
        n = dss.CktElement.NumConductors()
        pw = dss.CktElement.Powers()[:2 * n]
        p, q = sum(pw[0::2]), sum(pw[1::2])
        s = (p * p + q * q) ** 0.5
        tx_rows.append({"hour": hour, "kw": p, "kvar": q, "kva": s,
                        "loading_pct": 100 * s / kva_rating, "pf": p / s if s > 0 else 1.0})

    line_pct = np.where(line_norm > 0, 100 * line_i / np.where(line_norm > 0, line_norm, 1), np.nan)
    fdf, tdf = pd.DataFrame(feeder_rows), pd.DataFrame(tx_rows)

    raw = []
    ids = consumers["consumer_id"].tolist()
    for t in range(n_steps):
        for j, cid in enumerate(ids):
            vv = cust_v[t, j]
            if vv < V_CRIT:
                raw.append((t, "undervoltage", cid, "critical", vv))
            elif vv < V_LOW:
                raw.append((t, "undervoltage", cid, "warning", vv))
            elif vv > V_HIGH:
                raw.append((t, "overvoltage", cid, "warning", vv))
        for k in np.where(line_pct[t] > LOAD_WARN)[0]:
            raw.append((t, "line_overload" if line_pct[t, k] > LOAD_CRIT else "line_high_loading", line_names[k],
                        "critical" if line_pct[t, k] > LOAD_CRIT else "warning", line_pct[t, k]))
        lp = tdf.loc[t, "loading_pct"]
        if lp > LOAD_WARN:
            raw.append((t, "transformer_overload" if lp > LOAD_CRIT else "transformer_high_loading", tx.loc[0, "transformer"],
                        "critical" if lp > LOAD_CRIT else "warning", lp))
    for r in fdf.itertuples(index=False):
        if r.imbalance_pct > IMBAL_WARN and r.i_max_pct > IMBAL_MIN_LOAD:
            raw.append((hours.index(r.hour), "phase_imbalance", r.feeder, "warning", r.imbalance_pct))
    alerts = merge_events(raw, hours)

    h = STEP_MIN / 60
    cust = consumers[["consumer_id", "load", "feeder", "phase", "distance_m"]].copy()
    cust["v_min_pu"] = cust_v.min(axis=0).round(4)
    cust["v_max_pu"] = cust_v.max(axis=0).round(4)
    cust["v_min_volts"] = (cust["v_min_pu"] * 230).round(1)
    cust["hours_below_limit"] = ((cust_v < V_LOW).sum(axis=0) * h).round(2)
    cust = cust.sort_values("v_min_pu")

    lrep = pd.DataFrame({"line": line_names, "norm_amps": line_norm,
                         "max_amps": line_i.max(axis=0).round(1),
                         "max_loading_pct": np.nanmax(line_pct, axis=0).round(1),
                         "avg_loading_pct": np.nanmean(line_pct, axis=0).round(1)})
    lrep = lrep.merge(lines[["line", "feeder", "linecode", "length"]], on="line", how="left")
    lrep = lrep.sort_values("max_loading_pct", ascending=False)

    fsum = []
    for f, g in fdf.groupby("feeder"):
        pk = g.loc[g["kw"].idxmax()]
        fsum.append({"feeder": f, "peak_kw": round(pk["kw"], 1), "peak_time": hhmm(pk["hour"]),
                     "energy_kwh": round(g["kw"].sum() * h, 1),
                     "max_imbalance_pct": round(g["imbalance_pct"].max(), 1),
                     "customers": int((consumers["feeder"] == f).sum())})
    fsum = pd.DataFrame(fsum)

    OUT.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(cust_v, columns=ids).assign(hour=hours).to_csv(OUT / "customer_voltage_pu.csv", index=False)
    fdf.to_csv(OUT / "feeder_timeseries.csv", index=False)
    tdf.to_csv(OUT / "transformer_timeseries.csv", index=False)
    cust.to_csv(OUT / "customer_report.csv", index=False)
    lrep.to_csv(OUT / "line_report.csv", index=False)
    fsum.to_csv(OUT / "feeder_summary.csv", index=False)
    alerts.to_csv(OUT / "alerts.csv", index=False)

    tp = tdf.loc[tdf["loading_pct"].idxmax()]
    print(f"=== {FEEDER}: monitoring (simulated measurements, {STEP_MIN}-min) ===")
    print(f"Transformer peak: {tp['loading_pct']:.0f}% at {hhmm(tp['hour'])}, pf {tp['pf']:.2f}")
    print("\nFeeders:")
    print(fsum.to_string(index=False))
    print("\nMost loaded lines:")
    print(lrep.head(5)[["line", "feeder", "max_amps", "norm_amps", "max_loading_pct"]].to_string(index=False))
    print("\nLowest-voltage customers:")
    print(cust.head(5)[["consumer_id", "load", "feeder", "phase", "distance_m",
                        "v_min_volts", "hours_below_limit"]].to_string(index=False))
    print(f"\nCustomers below {V_LOW} pu at any time: {(cust['hours_below_limit'] > 0).sum()} of {len(cust)}")
    print(f"\nAlert events: {len(alerts)}")
    if len(alerts):
        print(alerts.groupby(["type", "severity"]).size().to_string())
        print("\nFirst alerts:")
        print(alerts.head(8).to_string(index=False))
    print(f"\nSaved to results/{FEEDER}/monitoring/")


if __name__ == "__main__":
    main()


