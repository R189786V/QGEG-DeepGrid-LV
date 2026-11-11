"""Data-quality experiment: how wrong phase records degrade the twin.
The true network is the model as built. The twin has 10/20/30% of single-phase customers recorded on
the wrong phase (10 random draws each). Both simulate the same day; the twin's predictions are compared
with the truth: voltage error per customer, missed violations, false alarms, and whether the most
loaded phase on each feeder is still identified. All else (cables, demand, DER) is identical.
Usage: python src/data_quality.py zimbabwe_rural [control]
"""
import sys
import random
from pathlib import Path
import numpy as np
import pandas as pd
import networkx as nx
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import opendssdirect as dss

sys.path.insert(0, str(Path(__file__).resolve().parent))
from customer_nodes import build_nodes, meter_profile, ROOT, STEPS, PHASE_NODE, PHASE_LIST, V_LOW, V_HIGH  # noqa: E402
from fault_locator import topology, base  # noqa: E402

RATES = [0.0, 0.1, 0.2, 0.3]
SEEDS = range(10)
PH = ["A", "B", "C"]
VNOM = 230.0


def run(feeder, nodes, cons, phase_of, busbar, heads):
    """Connect each customer on phase_of[cid]. A single-phase service bus has only one phase, so a wrong
    phase is modelled by reconnecting the upstream end of the service cable (at the pole or pillar)."""
    dss.Text.Command(f'compile "{ROOT / "opendss" / feeder / "Master.dss"}"')
    truth = dict(zip(cons["consumer_id"], cons["phase"]))
    label = {}
    for cid, bus, ld in zip(cons["consumer_id"], cons["bus"], cons["load"]):
        ph, ph0 = phase_of[cid], truth[cid]
        label[cid] = ph0
        if ph == ph0:
            continue
        bus = bus.lower()
        dss.Circuit.SetActiveBus(bus)
        if int(PHASE_NODE[ph][1:]) in dss.Bus.Nodes():
            dss.Text.Command(f"edit Load.{ld} bus1={bus}{PHASE_NODE[ph]}")
            label[cid] = ph
            continue
        moved = False
        i = dss.Lines.First()
        while i > 0:
            b1, b2 = base(dss.Lines.Bus1()), base(dss.Lines.Bus2())
            if bus in (b1, b2) and dss.Lines.Phases() == 1:
                k, up = (1, b1) if b2 == bus else (2, b2)
                name = dss.Lines.Name()
                dss.Text.Command(f"edit Line.{name} bus{k}={up}{PHASE_NODE[ph]}")
                moved = True
                break
            i = dss.Lines.Next()
        if not moved:
            raise RuntimeError(f"No single-phase service cable found for {cid} at bus {bus}")
    by_load = {nd.load: nd for nd in nodes}
    consumption, generation = {}, {}
    for ld, cid in zip(cons["load"], cons["consumer_id"]):
        nd = by_load.get(ld)
        if nd is None:
            consumption[ld] = meter_profile(ld)
            continue
        r = nd.net_meter()
        consumption[ld] = r["meter"] + r["ev"] + r["hp"] + r["charge"]
        g = r["pv"] + r["discharge"]
        if g.max() > 1e-9:
            generation[f"g_{cid}".lower()] = (g, nd.bus, label[cid])
    for ld in consumption:
        dss.Text.Command(f"edit Load.{ld} vminpu=0.5 vmaxpu=1.5")
    for gname, (g, bus, ph) in generation.items():
        dss.Text.Command(f"New Generator.{gname} phases=1 bus1={bus}{PHASE_NODE[ph]} kV=0.23 kW=0 pf=1 model=1 "
                         f"vminpu=0.5 vmaxpu=1.5")
    dss.Text.Command("set maxiterations=100")
    dss.Text.Command("set mode=snapshot")
    idx = {n.lower(): i for i, n in enumerate(dss.Circuit.AllNodeNames())}
    cnodes = [[idx[f"{b.lower()}{s}"] for s in PHASE_LIST[label[c]] if f"{b.lower()}{s}" in idx]
              for c, b in zip(cons["consumer_id"], cons["bus"])]
    missing = [c for c, ix in zip(cons["consumer_id"], cnodes) if not ix]
    if missing:
        raise RuntimeError(f"No voltage node found for customers: {missing[:5]}")
    cmin, cmax = np.full(len(cnodes), np.inf), np.full(len(cnodes), -np.inf)
    peaks = {(h, p): 0.0 for h in heads for p in (1, 2, 3)}
    for t in range(STEPS):
        for ld, c in consumption.items():
            dss.Loads.Name(ld)
            dss.Loads.kW(float(c[t]))
        for gname, (g, _, _) in generation.items():
            dss.Generators.Name(gname)
            dss.Generators.kW(float(g[t]))
        dss.Solution.Solve()
        v = np.array(dss.Circuit.AllBusMagPu())
        cmin = np.minimum(cmin, [v[ix].min() for ix in cnodes])
        cmax = np.maximum(cmax, [v[ix].max() for ix in cnodes])
        for h in heads:
            dss.Circuit.SetActiveElement(f"Line.{h}")
            n = dss.CktElement.NumConductors()
            tt = 0 if base(dss.CktElement.BusNames()[0]) == busbar else 1
            order = dss.CktElement.NodeOrder()[tt * n:(tt + 1) * n]
            mags = dss.CktElement.CurrentsMagAng()
            for p in (1, 2, 3):
                if p in order:
                    j = tt * n + order.index(p)
                    peaks[(h, p)] = max(peaks[(h, p)], mags[2 * j])
    return cmin, cmax, peaks



def main():
    feeder = sys.argv[1] if len(sys.argv) > 1 else "zimbabwe_rural"
    control = sys.argv[2] if len(sys.argv) > 2 else "network"
    cons = pd.read_csv(ROOT / "data" / "assets" / feeder / "consumers.csv", dtype={"bus": str, "load": str})
    nodes = build_nodes(feeder, {"pv": 0.5, "ev": 0.3, "hp": 0.4, "battery": 0.5}, cons, control)
    G, lv, busbar = topology()
    toward = nx.shortest_path(G, busbar, lv)[1] if busbar != lv else None
    heads = [G[busbar][n]["name"] for n in G[busbar] if n != toward]
    truth_phase = dict(zip(cons["consumer_id"], cons["phase"]))
    one = [c for c in cons["consumer_id"] if truth_phase[c] in PH]
    print(f"=== {feeder}: wrong phase records | {len(one)} single-phase customers | feeders {len(heads)} | "
          f"day: 50% PV, 30% EV, 40% heat pumps, controller '{control}' ===")
    t_min, t_max, t_peaks = run(feeder, nodes, cons, truth_phase, busbar, heads)
    t_out = (t_min < V_LOW) | (t_max > V_HIGH)
    print(f"True network: {int(t_out.sum())} customers out of limits")
    t_worst = {h: max((1, 2, 3), key=lambda p: t_peaks[(h, p)]) for h in heads}

    rows = []
    for rate in RATES:
        for seed in (SEEDS if rate > 0 else [0]):
            rng = random.Random(1000 * int(rate * 100) + seed)
            wrong = rng.sample(one, round(rate * len(one)))
            rec = dict(truth_phase)
            for c in wrong:
                rec[c] = rng.choice([q for q in PH if q != truth_phase[c]])
            w_min, w_max, w_peaks = run(feeder, nodes, cons, rec, busbar, heads)
            err = np.maximum(np.abs(w_min - t_min), np.abs(w_max - t_max)) * VNOM
            w_out = (w_min < V_LOW) | (w_max > V_HIGH)
            w_worst = {h: max((1, 2, 3), key=lambda p: w_peaks[(h, p)]) for h in heads}
            ph_err = [abs(w_peaks[k] - t_peaks[k]) / t_peaks[k] * 100 for k in t_peaks if t_peaks[k] > 1]
            rows.append({"wrong_pct": int(rate * 100), "seed": seed, "customers_wrong": len(wrong),
                         "mean_voltage_error_v": round(float(err.mean()), 2),
                         "max_voltage_error_v": round(float(err.max()), 2),
                         "missed_violations": int((t_out & ~w_out).sum()),
                         "false_alarms": int((~t_out & w_out).sum()),
                         "worst_phase_correct_pct": round(100 * np.mean([w_worst[h] == t_worst[h] for h in heads]), 1),
                         "phase_peak_current_error_pct": round(float(np.mean(ph_err)), 1)})
            print(f"  {int(rate * 100):>2}% wrong, draw {seed}: mean error {rows[-1]['mean_voltage_error_v']} V, "
                  f"missed {rows[-1]['missed_violations']}, false alarms {rows[-1]['false_alarms']}")
    df = pd.DataFrame(rows)
    summ = df.groupby("wrong_pct").agg(
        draws=("seed", "count"), customers_wrong=("customers_wrong", "first"),
        mean_voltage_error_v=("mean_voltage_error_v", "mean"), max_voltage_error_v=("max_voltage_error_v", "max"),
        missed_violations_avg=("missed_violations", "mean"), missed_violations_max=("missed_violations", "max"),
        false_alarms_avg=("false_alarms", "mean"), false_alarms_max=("false_alarms", "max"),
        worst_phase_correct_pct=("worst_phase_correct_pct", "mean"),
        phase_peak_current_error_pct=("phase_peak_current_error_pct", "mean")).round(2)
    out = ROOT / "results" / feeder / "data_quality"
    out.mkdir(parents=True, exist_ok=True)
    df.to_csv(out / "runs.csv", index=False)
    summ.to_csv(out / "summary.csv")
    print(f"\nTrue network: {int(t_out.sum())} customers out of limits (the twin should find exactly these)")
    print(summ.to_string())

    fig, ax = plt.subplots(1, 3, figsize=(15, 4.5))
    jit = np.random.default_rng(0)
    for col, a, title in [("mean_voltage_error_v", ax[0], "Mean voltage error per customer (V)"),
                          ("missed_violations", ax[1], "Customers out of limits the twin misses"),
                          ("worst_phase_correct_pct", ax[2], "Most loaded phase identified correctly (%)")]:
        a.scatter(df["wrong_pct"] + jit.uniform(-1, 1, len(df)), df[col], s=18, color="#1c1f3e", alpha=0.7)
        a.plot(summ.index, df.groupby("wrong_pct")[col].mean(), color="#fab724", linewidth=2.5)
        a.set_xlabel("Customers on the wrong phase in the records (%)")
        a.set_title(title)
        a.set_xticks(RATES and [0, 10, 20, 30])
    ax[1].scatter(df["wrong_pct"] + jit.uniform(-1, 1, len(df)), df["false_alarms"], s=18, color="#c62828",
                  alpha=0.6, label="false alarms")
    ax[1].legend()
    fig.tight_layout()
    fig.savefig(out / "data_quality.png", dpi=150)
    print(f"\nSaved results/{feeder}/data_quality/ (runs.csv, summary.csv, data_quality.png)")


if __name__ == "__main__":
    main()
