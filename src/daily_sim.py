"""Phase 3: daily time-series simulation of an LV network."""
import sys
from pathlib import Path
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import opendssdirect as dss

ROOT = Path(__file__).resolve().parents[1]
FEEDER = sys.argv[1] if len(sys.argv) > 1 else "zimbabwe_rural"
STEP_MIN = int(sys.argv[2]) if len(sys.argv) > 2 else 15
MASTER = ROOT / "opendss" / FEEDER / "Master.dss"
OUT = ROOT / "results" / FEEDER
V_LOW, V_HIGH = 0.94, 1.10  # AS 60038 (230 V +10%/-6%); change for ZETDC limits


def hhmm(hour):
    h = hour % 24
    return f"{int(h):02d}:{int(round((h % 1) * 60)) % 60:02d}"


def tx_loading():
    dss.Transformers.First()
    kva = dss.Transformers.kVA()
    dss.Circuit.SetActiveElement(f"Transformer.{dss.Transformers.Name()}")
    n = dss.CktElement.NumConductors()
    pw = dss.CktElement.Powers()[: 2 * n]
    s = (sum(pw[0::2]) ** 2 + sum(pw[1::2]) ** 2) ** 0.5
    return s, 100 * s / kva


def main():
    dss.Text.Command(f'compile "{MASTER}"')
    dss.Text.Command("set mode=yearly")
    dss.Text.Command(f"set stepsize={STEP_MIN}m")
    dss.Text.Command("set number=1")
    dss.Solution.Hour(0)
    dss.Solution.Seconds(0)

    nodes = dss.Circuit.AllNodeNames()
    node_vmin = np.full(len(nodes), np.inf)
    rows = []
    for _ in range(24 * 60 // STEP_MIN):
        dss.Solution.Solve()
        if not dss.Solution.Converged():
            raise RuntimeError(f"Did not converge at hour {dss.Solution.DblHour()}")
        v = np.array(dss.Circuit.AllBusMagPu())
        v_valid = np.where(v > 0, v, np.inf)
        i_min = int(np.argmin(v_valid))
        p, q = dss.Circuit.TotalPower()
        s_tx, load_pct = tx_loading()
        rows.append({"hour": round(dss.Solution.DblHour(), 3),
                     "demand_kw": -p, "demand_kvar": -q,
                     "tx_kva": s_tx, "tx_loading_pct": load_pct,
                     "v_min_pu": v[i_min], "v_min_node": nodes[i_min],
                     "v_max_pu": v.max(),
                     "losses_kw": dss.Circuit.Losses()[0] / 1000,
                     "nodes_below_limit": int(((v > 0) & (v < V_LOW)).sum())})
        node_vmin = np.minimum(node_vmin, v_valid)

    df = pd.DataFrame(rows)
    OUT.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUT / "daily_timeseries.csv", index=False)
    pd.DataFrame({"node": nodes, "v_min_pu": node_vmin}).sort_values("v_min_pu") \
        .to_csv(OUT / "node_min_voltage.csv", index=False)

    h = STEP_MIN / 60
    pk = df.loc[df["demand_kw"].idxmax()]
    tx = df.loc[df["tx_loading_pct"].idxmax()]
    lo = df.loc[df["v_min_pu"].idxmin()]
    energy = df["demand_kw"].sum() * h
    losses = df["losses_kw"].sum() * h

    print(f"=== {FEEDER}: 24 h at {STEP_MIN}-minute steps ===")
    print(f"Peak demand: {pk['demand_kw']:.1f} kW at {hhmm(pk['hour'])}")
    print(f"Max transformer loading: {tx['tx_loading_pct']:.0f}% at {hhmm(tx['hour'])}")
    print(f"Hours transformer above 100%: {(df['tx_loading_pct'] > 100).sum() * h:.2f}")
    print(f"Lowest voltage: {lo['v_min_pu']:.4f} pu ({lo['v_min_pu'] * 230:.0f} V) "
          f"at {hhmm(lo['hour'])}, node {lo['v_min_node']}")
    print(f"Hours with any voltage below {V_LOW} pu: {(df['nodes_below_limit'] > 0).sum() * h:.2f}")
    print(f"Highest voltage: {df['v_max_pu'].max():.4f} pu")
    print(f"Daily energy: {energy:.0f} kWh | Losses: {losses:.1f} kWh ({100 * losses / energy:.1f}%)")

    fig, ax = plt.subplots(3, 1, figsize=(10, 8), sharex=True)
    ax[0].plot(df["hour"], df["demand_kw"])
    ax[0].set_ylabel("Demand (kW)")
    ax[1].plot(df["hour"], df["tx_loading_pct"])
    ax[1].axhline(100, color="red", linestyle="--", label="Rating")
    ax[1].set_ylabel("Transformer loading (%)")
    ax[1].legend()
    ax[2].plot(df["hour"], df["v_min_pu"])
    ax[2].axhline(V_LOW, color="red", linestyle="--", label=f"Limit {V_LOW} pu")
    ax[2].set_ylabel("Lowest voltage (pu)")
    ax[2].set_xlabel("Hour of day")
    ax[2].legend()
    fig.suptitle(f"{FEEDER}: daily simulation")
    fig.tight_layout()
    fig.savefig(OUT / "daily_summary.png", dpi=150)
    print(f"Saved: results/{FEEDER}/daily_timeseries.csv, node_min_voltage.csv, daily_summary.png")


if __name__ == "__main__":
    main()
