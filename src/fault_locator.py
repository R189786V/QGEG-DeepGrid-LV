"""Distance-to-fault in simulation, tested against ground truth.
The locator sees only busbar voltage and feeder-head currents (like a substation monitor).
The twin's fault map gives the impedance seen from the busbar for a solid fault at every point.
Random test faults (unknown fault resistance, load level and measurement error) are located with
two methods: full impedance and reactance only. Error = cable distance between estimate and truth.
Usage: python src/fault_locator.py zimbabwe_rural [n_faults]
"""
import sys
import random
import cmath
import math
from pathlib import Path
import numpy as np
import pandas as pd
import networkx as nx
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import opendssdirect as dss

ROOT = Path(__file__).resolve().parents[1]
UNIT_M = {0: 1.0, 1: 1609.34, 2: 304.8, 3: 1000.0, 4: 1.0, 5: 0.3048, 6: 0.0254, 7: 0.01, 8: 0.001}
NOISE_MAG, NOISE_DEG = 0.01, 0.5
MAP_LOADMULT = 0.6


def base(b):
    return b.split(".")[0].lower()


def compile_net(feeder):
    dss.Text.Command(f'compile "{ROOT / "opendss" / feeder / "Master.dss"}"')
    dss.Text.Command("set mode=snapshot")
    dss.Text.Command("set maxiterations=100")
    dss.Text.Command("New Fault.F1 phases=1 bus1=sourcebus.1 r=1 enabled=no")


def topology():
    dss.Transformers.First()
    lv = base(dss.CktElement.BusNames()[1])
    G = nx.Graph()
    i = dss.Lines.First()
    while i > 0:
        G.add_edge(base(dss.Lines.Bus1()), base(dss.Lines.Bus2()),
                   length=dss.Lines.Length() * UNIT_M.get(dss.Lines.Units(), 1.0), name=dss.Lines.Name())
        i = dss.Lines.Next()
    busbar, prev = lv, None
    while True:
        nxt = [n for n in G[busbar] if n != prev]
        if len(nxt) != 1:
            break
        prev, busbar = busbar, nxt[0]
    return G, lv, busbar


def measure(busbar, head, phase):
    dss.Circuit.SetActiveBus(busbar)
    nodes, v = dss.Bus.Nodes(), dss.Bus.Voltages()
    k = nodes.index(phase)
    V = complex(v[2 * k], v[2 * k + 1])
    dss.Circuit.SetActiveElement(f"Line.{head}")
    ncond = dss.CktElement.NumConductors()
    t = 0 if base(dss.CktElement.BusNames()[0]) == busbar else 1
    order = dss.CktElement.NodeOrder()[t * ncond:(t + 1) * ncond]
    cur = dss.CktElement.Currents()
    j = t * ncond + order.index(phase)
    return V, complex(cur[2 * j], cur[2 * j + 1])


def apply_fault(bus, phase, rf):
    dss.Text.Command(f"edit Fault.F1 bus1={bus}.{phase} r={max(rf, 1e-4)} enabled=yes")
    dss.Solution.Solve()


def clear_fault():
    dss.Text.Command("edit Fault.F1 enabled=no")


def noisy(z, rng):
    return z * (1 + rng.gauss(0, NOISE_MAG)) * cmath.exp(1j * math.radians(rng.gauss(0, NOISE_DEG)))


def main():
    feeder = sys.argv[1] if len(sys.argv) > 1 else "zimbabwe_rural"
    n_faults = int(sys.argv[2]) if len(sys.argv) > 2 else 200
    compile_net(feeder)
    G, lv, busbar = topology()
    dist = nx.single_source_dijkstra_path_length(G, lv, weight="length")
    paths = nx.single_source_shortest_path(G, lv)
    heads = {n: G[busbar][n]["name"] for n in G[busbar] if n in paths and busbar in paths[n] and n != busbar
             and paths[n].index(busbar) < paths[n].index(n)}
    points = []
    for b in G.nodes:
        p = paths.get(b, [])
        if busbar not in p or b == busbar:
            continue
        i = p.index(busbar)
        if i + 1 >= len(p) or p[i + 1] not in heads:
            continue
        dss.Circuit.SetActiveBus(b)
        for ph in [n for n in dss.Bus.Nodes() if n in (1, 2, 3)]:
            points.append((b, ph, heads[p[i + 1]]))
    total_km = sum(d["length"] for _, _, d in G.edges(data=True)) / 1000
    print(f"=== {feeder}: fault location test ===")
    print(f"Cable length {total_km:.2f} km | busbar {busbar} | feeders {len(heads)} | fault points {len(points)}")

    dss.Solution.LoadMult(MAP_LOADMULT)
    fmap = []
    for b, ph, head in points:
        apply_fault(b, ph, 1e-4)
        V, I = measure(busbar, head, ph)
        fmap.append({"bus": b, "phase": ph, "feeder_head": head, "distance_m": dist[b], "z": V / I})
        clear_fault()
    fmap = pd.DataFrame(fmap)
    fmap["r_ohm"], fmap["x_ohm"] = fmap["z"].apply(lambda z: z.real), fmap["z"].apply(lambda z: z.imag)

    rng = random.Random(11)
    rows = []
    for _ in range(n_faults):
        b, ph, head = points[rng.randrange(len(points))]
        rf = 10 ** rng.uniform(-3, math.log10(0.5))
        lm = rng.uniform(0.3, 1.0)
        dss.Solution.LoadMult(lm)
        apply_fault(b, ph, rf)
        currents = {h: abs(measure(busbar, name, ph)[1]) for h, name in heads.items()}
        est_head = heads[max(currents, key=currents.get)]
        V, I = measure(busbar, est_head, ph)
        clear_fault()
        z = noisy(V, rng) / noisy(I, rng)
        cand = fmap[(fmap["feeder_head"] == est_head) & (fmap["phase"] == ph)]
        row = {"true_bus": b, "phase": ph, "true_distance_m": round(dist[b], 1), "fault_r_ohm": round(rf, 4),
               "load_level": round(lm, 2), "fault_current_a": round(abs(I), 1),
               "feeder_identified": est_head == head, "z_meas_r": round(z.real, 5), "z_meas_x": round(z.imag, 5)}
        for name, score in [("impedance", (cand["z"] - z).abs()), ("reactance", (cand["x_ohm"] - z.imag).abs())]:
            best = cand.loc[score.idxmin()]
            row[f"{name}_bus"] = best["bus"]
            row[f"{name}_distance_m"] = round(best["distance_m"], 1)
            row[f"{name}_error_m"] = round(nx.shortest_path_length(G, best["bus"], b, weight="length"), 1)
        tol = 0.1 * abs(z.imag) + 1e-4
        row["reactance_candidates"] = int(((cand["x_ohm"] - z.imag).abs() <= tol).sum())
        rows.append(row)
    df = pd.DataFrame(rows)

    out = ROOT / "results" / feeder / "fault_location"
    out.mkdir(parents=True, exist_ok=True)
    df.to_csv(out / "test_faults.csv", index=False)
    fmap.drop(columns="z").to_csv(out / "fault_map.csv", index=False)
    summ = []
    for name in ["impedance", "reactance"]:
        e = df[f"{name}_error_m"]
        summ.append({"method": name, "median_error_m": round(e.median(), 1), "p90_error_m": round(e.quantile(0.9), 1),
                     "within_25m_pct": round(100 * (e <= 25).mean(), 1), "within_100m_pct": round(100 * (e <= 100).mean(), 1),
                     "exact_point_pct": round(100 * (e == 0).mean(), 1)})
    summ = pd.DataFrame(summ)
    summ.to_csv(out / "summary.csv", index=False)
    print(f"\nTest faults: {len(df)} | feeder identified correctly: {100 * df['feeder_identified'].mean():.1f}% | "
          f"fault current {df['fault_current_a'].min():.0f} to {df['fault_current_a'].max():.0f} A")
    print(summ.to_string(index=False))
    df["rf_band"] = pd.cut(df["fault_r_ohm"], [0, 0.01, 0.1, 0.5], labels=["<=0.01 ohm", "0.01-0.1 ohm", "0.1-0.5 ohm"])
    print("\nMedian error (m) by fault resistance:")
    print(df.groupby("rf_band", observed=True)[["impedance_error_m", "reactance_error_m"]].median().round(1).to_string())
    print(f"\nPoints matching the reactance within 10% (crew search set): median {df['reactance_candidates'].median():.0f}, "
          f"max {df['reactance_candidates'].max()}")

    fig, ax = plt.subplots(1, 2, figsize=(12, 5))
    for name, col in [("impedance", "#1c1f3e"), ("reactance", "#fab724")]:
        ax[0].scatter(df["true_distance_m"], df[f"{name}_distance_m"], s=14, alpha=0.7, color=col, label=name)
        e = np.sort(df[f"{name}_error_m"].values)
        ax[1].plot(e, np.arange(1, len(e) + 1) / len(e) * 100, color=col, linewidth=2, label=name)
    lim = df["true_distance_m"].max() * 1.05
    ax[0].plot([0, lim], [0, lim], color="grey", linewidth=0.8, linestyle="--")
    ax[0].set_xlabel("True distance from transformer (m)")
    ax[0].set_ylabel("Estimated distance (m)")
    ax[0].set_title("Estimated vs true fault location")
    ax[0].legend()
    ax[1].set_xlabel("Location error: cable distance to the true fault (m)")
    ax[1].set_ylabel("Faults located within this error (%)")
    ax[1].set_title("Error distribution")
    ax[1].legend()
    fig.tight_layout()
    fig.savefig(out / "fault_location.png", dpi=150)
    print(f"\nSaved results/{feeder}/fault_location/ (test_faults.csv, fault_map.csv, summary.csv, fault_location.png)")


if __name__ == "__main__":
    main()
