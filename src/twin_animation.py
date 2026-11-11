"""Twin view: an animated replay of one simulated day on the network map.

Each frame is one time step of an OpenDSS quasi-static time-series run: buses are
coloured by voltage, lines by loading, and the title shows transformer loading.
This is a replay of a SIMULATED day (a digital model), not live data.
Usage: python src/twin_animation.py zimbabwe_rural [base|pv50|ev30|pv50+ev30|coordinated]
"""
import sys
import random
from pathlib import Path
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import opendssdirect as dss

sys.path.insert(0, str(Path(__file__).resolve().parent))
from agents import (house_profile, VOLTWATT, SHAPES, PV_KW, BAT_KW, BAT_KWH, EV_KW,  # noqa: E402
                    BAT_CHARGE_FRAC, PHASE_NODE, PENETRATION, EV_SHARE)

ROOT = Path(__file__).resolve().parents[1]
FEEDER = sys.argv[1] if len(sys.argv) > 1 else "zimbabwe_rural"
SCEN = sys.argv[2] if len(sys.argv) > 2 else "base"
STEP_MIN = 30 if FEEDER == "european_lv" else 15
SCENARIOS = {"base": {}, "pv50": {"pv": True}, "ev30": {"ev": "uncontrolled"},
             "pv50+ev30": {"pv": True, "ev": "uncontrolled"},
             "coordinated": {"pv": True, "ev": "managed", "battery": True, "voltwatt": True}}
BANDS = [(0, 50, "#90caf9", "0-50%"), (50, 80, "#1e88e5", "50-80%"),
         (80, 100, "#f9a825", "80-100%"), (100, 1e9, "#c62828", "over 100%")]
VSCALE = [[0.0, "#b71c1c"], [0.25, "#ef6c00"], [0.375, "#2e7d32"], [0.7917, "#2e7d32"], [0.9167, "#8e24aa"], [1.0, "#4a148c"]]


def hhmm(h):
    h = h % 24
    return f"{int(h):02d}:{int(round((h % 1) * 60)) % 60:02d}"


def build(cons, opts):
    one = cons[cons["phase"].isin(["A", "B", "C"])].reset_index(drop=True)
    ids = one["consumer_id"].tolist()
    pv_order, ev_order = ids[:], ids[:]
    random.Random(1).shuffle(pv_order)
    random.Random(2).shuffle(ev_order)
    pv = set(pv_order[:round(PENETRATION * len(ids))]) if opts.get("pv") else set()
    ev = set(ev_order[:round(EV_SHARE * len(ids))]) if opts.get("ev") else set()
    cmds = [f"New Loadshape.{n} npts=96 interval=0.25 mult=({' '.join(map(str, v))})" for n, v in SHAPES.items()]
    for r in one.itertuples(index=False):
        node, cid = f"{r.bus}{PHASE_NODE[r.phase]}", r.consumer_id
        if cid in pv:
            cmds.append(f"New PVSystem.pv_{cid} phases=1 bus1={node} kV=0.23 kVA={PV_KW} Pmpp={PV_KW} "
                        f"irradiance=1 %cutin=0.05 %cutout=0.05 pf=1 daily=der_solar yearly=der_solar")
            if opts.get("battery"):
                prof = house_profile(r.load)
                mult = []
                for t in range(96):
                    hh = t * 0.25
                    if 9 <= hh < 15:
                        mult.append(round(-BAT_CHARGE_FRAC * PV_KW * SHAPES["der_solar"][t] / BAT_KW, 4))
                    elif 17 <= hh < 22:
                        mult.append(round(min(1.0, prof[t] / BAT_KW), 4))
                    else:
                        mult.append(0.0)
                cmds.append(f"New Loadshape.bat_{cid} npts=96 interval=0.25 mult=({' '.join(map(str, mult))})")
                cmds.append(f"New Storage.bat_{cid} phases=1 bus1={node} kV=0.23 kWrated={BAT_KW} "
                            f"kWhrated={BAT_KWH} %stored=20 %reserve=10 dispmode=follow "
                            f"daily=bat_{cid} yearly=bat_{cid}")
        if cid in ev:
            cmds.append(f"New Load.ev_{cid} phases=1 bus1={node} kV=0.23 kW={EV_KW} pf=1 model=1 "
                        f"daily=ev_{opts['ev']} yearly=ev_{opts['ev']}")
    if opts.get("voltwatt"):
        cmds += VOLTWATT
    return cmds


def main():
    if SCEN not in SCENARIOS:
        sys.exit(f"Scenario must be one of: {', '.join(SCENARIOS)}")
    assets = ROOT / "data" / "assets" / FEEDER
    cons = pd.read_csv(assets / "consumers.csv", dtype={"bus": str, "load": str})
    buses = pd.read_csv(assets / "buses.csv", dtype={"bus": str})
    tx = pd.read_csv(assets / "transformers.csv", dtype={"hv_bus": str, "lv_bus": str})
    hv, lv = tx.loc[0, "hv_bus"], tx.loc[0, "lv_bus"]
    xy = {b: (x, y) for b, x, y in zip(buses["bus"], buses["x"], buses["y"]) if b != hv}

    dss.Text.Command(f'compile "{ROOT / "opendss" / FEEDER / "Master.dss"}"')
    for c in build(cons, SCENARIOS[SCEN]):
        dss.Text.Command(c)
    dss.Text.Command("set maxcontroliter=200 maxiterations=100")
    dss.Text.Command("set mode=yearly")
    dss.Text.Command(f"set stepsize={STEP_MIN}m")
    dss.Text.Command("set number=1")
    dss.Solution.Hour(0)
    dss.Solution.Seconds(0)

    nodes = [n.lower() for n in dss.Circuit.AllNodeNames()]
    bus_names = [b for b in xy if any(n.split(".")[0] == b for n in nodes)]
    bus_ix = {b: [i for i, n in enumerate(nodes) if n.split(".")[0] == b] for b in bus_names}
    line_ends, norm = [], []
    i = dss.Lines.First()
    while i > 0:
        line_ends.append((dss.Lines.Bus1().split(".")[0].lower(), dss.Lines.Bus2().split(".")[0].lower()))
        norm.append(dss.Lines.NormAmps())
        i = dss.Lines.Next()
    drawable = [k for k, (a, b) in enumerate(line_ends) if a in xy and b in xy]

    frames_v, frames_l, info = [], [], []
    for _ in range(24 * 60 // STEP_MIN):
        dss.Solution.Solve()
        v = np.array(dss.Circuit.AllBusMagPu())
        frames_v.append([float(v[bus_ix[b]].min()) for b in bus_names])
        load, k = [], 0
        i = dss.Lines.First()
        while i > 0:
            nph = dss.CktElement.NumPhases()
            amps = max(dss.CktElement.CurrentsMagAng()[0:2 * nph:2])
            load.append(100 * amps / norm[k] if norm[k] > 0 else 0.0)
            k += 1
            i = dss.Lines.Next()
        frames_l.append(load)
        dss.Transformers.First()
        kva = dss.Transformers.kVA()
        dss.Circuit.SetActiveElement(f"Transformer.{dss.Transformers.Name()}")
        n = dss.CktElement.NumConductors()
        pw = dss.CktElement.Powers()[: 2 * n]
        p, q = sum(pw[0::2]), sum(pw[1::2])
        info.append((dss.Solution.DblHour(), p, 100 * (p * p + q * q) ** 0.5 / kva))

    bx = [xy[b][0] for b in bus_names]
    by = [xy[b][1] for b in bus_names]

    def seg(idx):
        xs, ys = [], []
        for k in idx:
            (x1, y1), (x2, y2) = xy[line_ends[k][0]], xy[line_ends[k][1]]
            xs += [x1, x2, None]
            ys += [y1, y2, None]
        return (xs or [None]), (ys or [None])

    def dynamic(t):
        out = []
        for lo, hi, col, name in BANDS:
            xs, ys = seg([k for k in drawable if lo <= frames_l[t][k] < hi])
            out.append(go.Scatter(x=xs, y=ys, mode="lines", line=dict(color=col, width=3),
                                  name=f"Line loading {name}", hoverinfo="skip"))
        vb = frames_v[t]
        out.append(go.Scatter(x=bx, y=by, mode="markers", name="Bus voltage (pu)",
                              text=[f"{b}: {val:.3f} pu ({val * 230:.0f} V)" for b, val in zip(bus_names, vb)],
                              hoverinfo="text",
                              marker=dict(size=7, color=vb, cmin=0.88, cmax=1.12, colorscale=VSCALE,
                                          line=dict(width=0.4, color="#333"),
                                          colorbar=dict(title="Voltage (pu)", x=1.02, tickvals=[0.90, 0.94, 1.00, 1.06, 1.10], ticktext=["0.90", "0.94 (limit)", "1.00", "1.06", "1.10 (limit)"]))))
        return out

    def title(t):
        h, p, pct = info[t]
        vb = frames_v[t]
        flow = f"{p:.0f} kW import" if p >= 0 else f"{-p:.0f} kW export"
        return (f"{FEEDER} | scenario: {SCEN} | {hhmm(h)} | transformer {pct:.0f}% ({flow}) | "
                f"voltage {min(vb):.3f} to {max(vb):.3f} pu")

    gx, gy = seg(drawable)
    base_lines = go.Scatter(x=gx, y=gy, mode="lines", line=dict(color="#d0d0d0", width=1),
                            name="Network", hoverinfo="skip")
    txm = go.Scatter(x=[xy[lv][0]], y=[xy[lv][1]], mode="markers+text", text=["Transformer"],
                     textposition="top center", marker=dict(size=16, symbol="square", color="black"),
                     name="Transformer", hoverinfo="name")
    fig = go.Figure(data=[base_lines] + dynamic(0) + [txm],
                    frames=[go.Frame(data=dynamic(t), traces=[1, 2, 3, 4, 5], name=str(t),
                                     layout=go.Layout(title_text=title(t))) for t in range(len(info))])
    steps = [dict(method="animate", label=hhmm(info[t][0]),
                  args=[[str(t)], {"mode": "immediate", "frame": {"duration": 0, "redraw": True},
                                   "transition": {"duration": 0}}]) for t in range(len(info))]
    fig.update_layout(
        title_text=title(0), template="plotly_white", height=900,
        xaxis=dict(title="x (m, local grid)", scaleanchor="y"), yaxis=dict(title="y (m)"),
        legend=dict(orientation="h", x=0, y=1.0, yanchor="bottom", font=dict(size=11)), margin=dict(t=150, b=200),
        updatemenus=[dict(type="buttons", direction="left", x=0, xanchor="left", y=-0.14, yanchor="top", buttons=[
            dict(label="Play", method="animate",
                 args=[None, {"frame": {"duration": 250, "redraw": True}, "fromcurrent": True,
                              "transition": {"duration": 0}}]),
            dict(label="Pause", method="animate",
                 args=[[None], {"frame": {"duration": 0, "redraw": False}, "mode": "immediate"}])])],
        sliders=[dict(active=0, steps=steps, x=0.16, len=0.84, y=-0.14, yanchor="top", ticklen=0, tickcolor="rgba(0,0,0,0)", font=dict(size=1, color="rgba(0,0,0,0)"),
                      currentvalue=dict(prefix="Time: ", font=dict(size=14, color="#333")))],
        annotations=[dict(text="Replay of a SIMULATED day (digital model), not live data. "
                               + ("Synthetic network." if FEEDER != "european_lv" else "IEEE European LV Test Feeder."),
                          xref="paper", yref="paper", x=0, y=-0.3, showarrow=False,
                          font=dict(size=11, color="#555"))])

    out = ROOT / "results" / FEEDER / "animation"
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{SCEN.replace('+', '_')}.html"
    fig.write_html(path, include_plotlyjs="cdn", auto_play=False)
    peak = max(range(len(info)), key=lambda t: info[t][2])
    lo_t = min(range(len(info)), key=lambda t: min(frames_v[t]))
    hi_t = max(range(len(info)), key=lambda t: max(frames_v[t]))
    print(f"=== {FEEDER} | {SCEN} | {len(info)} frames of {STEP_MIN} min ===")
    print(f"Transformer peak {info[peak][2]:.0f}% at {hhmm(info[peak][0])}")
    print(f"Lowest voltage {min(frames_v[lo_t]):.3f} pu at {hhmm(info[lo_t][0])}; "
          f"highest {max(frames_v[hi_t]):.3f} pu at {hhmm(info[hi_t][0])}")
    print(f"Saved {path.relative_to(ROOT)} ({path.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()


