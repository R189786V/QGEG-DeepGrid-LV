"""Phase 8: interactive network maps (local coordinates).

Coordinates are each network's own local grid in metres. The Zimbabwean layouts are
synthetic and are NOT placed at real geographic locations.
"""
import sys
import random
from pathlib import Path
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parents[1]
FEEDER = sys.argv[1] if len(sys.argv) > 1 else "zimbabwe_rural"
ASSETS = ROOT / "data" / "assets" / FEEDER
RES = ROOT / "results" / FEEDER
OUT = RES / "maps"

BAND_COLOUR = {"Green": "#2e7d32", "Amber": "#f9a825", "Red": "#c62828"}
LOAD_BANDS = [(0, 50, "0-50%", "#90caf9"), (50, 80, "50-80%", "#1e88e5"),
              (80, 100, "80-100%", "#f9a825"), (100, 1e9, ">100%", "#c62828")]
FEEDER_COLOURS = ["#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd", "#8c564b"]
VIEWS = ["Feeders", "Voltage", "Line loading", "Asset health", "Solar (pv50 scenario)"]


def segments(df, xy):
    xs, ys, txt = [], [], []
    for r in df.itertuples(index=False):
        if r.bus1 in xy and r.bus2 in xy:
            (x1, y1), (x2, y2) = xy[r.bus1], xy[r.bus2]
            xs += [x1, x2, None]
            ys += [y1, y2, None]
            txt += [r.hover, r.hover, None]
    return xs, ys, txt


def main():
    s = str
    buses = pd.read_csv(ASSETS / "buses.csv", dtype={"bus": s, "feeder": s})
    lines = pd.read_csv(ASSETS / "lines.csv", dtype={"line": s, "bus1": s, "bus2": s})
    tx = pd.read_csv(ASSETS / "transformers.csv", dtype={"hv_bus": s, "lv_bus": s})
    cons = pd.read_csv(ASSETS / "consumers.csv", dtype={"bus": s, "load": s, "feeder": s})
    lh = pd.read_csv(RES / "asset_health" / "line_health.csv", dtype={"line": s, "feeder": s})
    ch = pd.read_csv(RES / "asset_health" / "customer_health.csv", dtype={"load": s, "feeder": s})

    hv_bus, lv_bus = tx.loc[0, "hv_bus"], tx.loc[0, "lv_bus"]
    xy = {b: (x, y) for b, x, y in zip(buses["bus"], buses["x"], buses["y"]) if b != hv_bus}

    lines = lines.merge(lh[["line", "feeder", "max_loading_pct", "band"]], on="line", how="left")
    lines["hover"] = [f"{l} | feeder {f} | {lc} | {ln:.0f} m | max loading {ld:.0f}% | {b}"
                      for l, f, lc, ln, ld, b in zip(lines["line"], lines["feeder"], lines["linecode"],
                                                     lines["length"], lines["max_loading_pct"].fillna(0),
                                                     lines["band"].fillna("n/a"))]
    c = ch.merge(cons[["consumer_id", "bus"]], on="consumer_id", how="left")
    c["x"] = [xy.get(b, (np.nan, np.nan))[0] for b in c["bus"]]
    c["y"] = [xy.get(b, (np.nan, np.nan))[1] for b in c["bus"]]

    ids = cons[cons["phase"].isin(["A", "B", "C"])]["consumer_id"].tolist()
    order = ids[:]
    random.Random(1).shuffle(order)          # same selection as der_study.py
    pv = set(order[:round(0.5 * len(order))])
    c["pv"] = c["consumer_id"].isin(pv)
    c["hover"] = [f"{cid} ({ld}) | feeder {f} | phase {p} | {d:.0f} m<br>"
                  f"min {vv:.0f} V ({vp:.3f} pu) | {hb} h below limit | {b}"
                  f"{' | PV in pv50' if has_pv else ''}"
                  for cid, ld, f, p, d, vv, vp, hb, b, has_pv in
                  zip(c["consumer_id"], c["load"], c["feeder"], c["phase"], c["distance_m"],
                      c["v_min_volts"], c["v_min_pu"], c["hours_below_limit"], c["band"], c["pv"])]

    traces, tags = [], []

    def add(trace, view):
        traces.append(trace)
        tags.append(view)

    gx, gy, gt = segments(lines, xy)

    def grey_lines():
        return go.Scatter(x=gx, y=gy, mode="lines", line=dict(color="#c0c0c0", width=1.5),
                          text=gt, hoverinfo="text", name="Lines")

    def cust_markers(color, name, df=c, symbol="circle", size=8):
        return go.Scatter(x=df["x"], y=df["y"], mode="markers", name=name, text=df["hover"],
                          hoverinfo="text", marker=dict(size=size, color=color, symbol=symbol,
                                                        line=dict(width=0.5, color="black")))

    # View 1: feeders
    for k, (f, g) in enumerate(lines.groupby("feeder")):
        x, y, t = segments(g, xy)
        add(go.Scatter(x=x, y=y, mode="lines", name=f"Feeder {f}", text=t, hoverinfo="text",
                       line=dict(color=FEEDER_COLOURS[k % len(FEEDER_COLOURS)], width=2.5)), "Feeders")
    add(cust_markers("#777777", "Customers", size=6), "Feeders")

    # View 2: voltage
    add(grey_lines(), "Voltage")
    add(go.Scatter(x=c["x"], y=c["y"], mode="markers", name="Customer min voltage", text=c["hover"],
                   hoverinfo="text",
                   marker=dict(size=10, color=c["v_min_pu"], colorscale="RdYlGn", cmin=0.90, cmax=1.05,
                               line=dict(width=0.5, color="black"),
                               colorbar=dict(title="Min voltage (pu)"))), "Voltage")

    # View 3: line loading
    for lo, hi, label, col in LOAD_BANDS:
        g = lines[(lines["max_loading_pct"] >= lo) & (lines["max_loading_pct"] < hi)]
        if len(g):
            x, y, t = segments(g, xy)
            add(go.Scatter(x=x, y=y, mode="lines", name=f"Loading {label}", text=t, hoverinfo="text",
                           line=dict(color=col, width=3)), "Line loading")
    add(cust_markers("#bbbbbb", "Customers", size=5), "Line loading")

    # View 4: asset health
    for b, col in BAND_COLOUR.items():
        g = lines[lines["band"] == b]
        if len(g):
            x, y, t = segments(g, xy)
            add(go.Scatter(x=x, y=y, mode="lines", name=f"Lines {b}", text=t, hoverinfo="text",
                           line=dict(color=col, width=2.5)), "Asset health")
    for b, col in BAND_COLOUR.items():
        g = c[c["band"] == b]
        if len(g):
            add(cust_markers(col, f"Customers {b}", df=g, size=9), "Asset health")

    # View 5: solar
    add(grey_lines(), "Solar (pv50 scenario)")
    add(cust_markers("#bbbbbb", "No PV", df=c[~c["pv"]], size=6), "Solar (pv50 scenario)")
    add(cust_markers("#ffb300", "PV (5 kW)", df=c[c["pv"]], symbol="star", size=12), "Solar (pv50 scenario)")

    lx, ly = xy[lv_bus]
    add(go.Scatter(x=[lx], y=[ly], mode="markers+text", name="Transformer", text=["Transformer"],
                   textposition="top center", hoverinfo="name",
                   marker=dict(size=18, symbol="square", color="black")), "all")

    def visible(view):
        return [t == view or t == "all" for t in tags]

    fig = go.Figure(traces)
    for tr, vis in zip(fig.data, visible("Feeders")):
        tr.visible = vis
    note = ("IEEE European LV Test Feeder, local coordinates" if FEEDER == "european_lv"
            else "SYNTHETIC layout on a local grid (metres). Not a real ZETDC network or location.")
    fig.update_layout(
        title=f"{FEEDER}: Feeders", template="plotly_white", height=780,
        xaxis=dict(title="x (m, local grid)", scaleanchor="y"), yaxis=dict(title="y (m)"),
        updatemenus=[dict(type="buttons", direction="right", x=0, xanchor="left", y=1.08,
                          buttons=[dict(label=v, method="update",
                                        args=[{"visible": visible(v)}, {"title": f"{FEEDER}: {v}"}])
                                   for v in VIEWS])],
        annotations=[dict(text=note, xref="paper", yref="paper", x=0, y=-0.08,
                          showarrow=False, font=dict(size=11, color="#555"))])
    OUT.mkdir(parents=True, exist_ok=True)
    fig.write_html(OUT / "network_map.html", include_plotlyjs="cdn")

    fig2, ax = plt.subplots(figsize=(9, 8))
    for r in lines.itertuples(index=False):
        if r.bus1 in xy and r.bus2 in xy:
            ax.plot([xy[r.bus1][0], xy[r.bus2][0]], [xy[r.bus1][1], xy[r.bus2][1]],
                    color="#bbbbbb", linewidth=0.8, zorder=1)
    sc = ax.scatter(c["x"], c["y"], c=c["v_min_pu"], cmap="RdYlGn", vmin=0.90, vmax=1.05,
                    s=28, zorder=2, edgecolors="black", linewidths=0.3)
    ax.scatter([lx], [ly], marker="s", s=140, color="black", zorder=3, label="Transformer")
    fig2.colorbar(sc, label="Lowest customer voltage over the day (pu)")
    ax.set_aspect("equal")
    ax.set_xlabel("x (m, local grid)")
    ax.set_ylabel("y (m)")
    ax.set_title(f"{FEEDER}: lowest voltage per customer\n{note}", fontsize=10)
    ax.legend(loc="best")
    fig2.tight_layout()
    fig2.savefig(OUT / "voltage_map.png", dpi=150)

    unmapped = int(c["x"].isna().sum())
    print(f"=== {FEEDER}: network map ===")
    print(f"Mapped {len(xy)} buses, {len(lines)} lines, {len(c) - unmapped} of {len(c)} customers")
    print(f"PV homes shown (pv50 scenario): {int(c['pv'].sum())}")
    print(f"Saved: results/{FEEDER}/maps/network_map.html and voltage_map.png")


if __name__ == "__main__":
    main()
