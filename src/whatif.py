"""What-if simulator: re-simulate a full day with chosen settings and animate it.

Settings: PV share, EV share and charging mode, smart batteries, volt-watt, phase
rebalancing, irrigation pump to daytime (rural), transformer rating, supply voltage.
Simulated digital model, not live data. Used by the dashboard; also runnable:
python src/whatif.py zimbabwe_rural pv=0.5 ev=0.3 mode=slow battery=1 voltwatt=1 rebalance=1 pump_day=1
"""
import sys
import random
from pathlib import Path
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import opendssdirect as dss

sys.path.insert(0, str(Path(__file__).resolve().parent))
from agents import (house_profile, customer_nodes, VOLTWATT, SHAPES, PV_KW, BAT_KW,  # noqa: E402
                    BAT_KWH, EV_KW, BAT_CHARGE_FRAC, PHASE_NODE, V_LOW, V_HIGH)
from twin_animation import BANDS, VSCALE, hhmm  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
PUMP_DAY = [0] * 9 + [1] * 7 + [0] * 8
DEFAULTS = dict(pv=0.0, ev=0.0, mode="uncontrolled", battery=False, voltwatt=False,
                rebalance=False, pump_day=False, tx_kva=None, source_pu=None)


def run(feeder, params=None):
    p = {**DEFAULTS, **(params or {})}
    step = 30 if feeder == "european_lv" else 15
    H = step / 60
    s = str
    assets = ROOT / "data" / "assets" / feeder
    cons = pd.read_csv(assets / "consumers.csv", dtype={"bus": s, "load": s, "feeder": s})
    lines = pd.read_csv(assets / "lines.csv", dtype={"line": s, "bus1": s, "bus2": s})
    buses = pd.read_csv(assets / "buses.csv", dtype={"bus": s})
    tx = pd.read_csv(assets / "transformers.csv", dtype={"hv_bus": s, "lv_bus": s})
    hv, lv = tx.loc[0, "hv_bus"], tx.loc[0, "lv_bus"]
    xy = {b: (x, y) for b, x, y in zip(buses["bus"], buses["x"], buses["y"]) if b != hv}

    one = cons[cons["phase"].isin(["A", "B", "C"])].reset_index(drop=True)
    ids = one["consumer_id"].tolist()
    pv_order, ev_order = ids[:], ids[:]
    random.Random(1).shuffle(pv_order)
    random.Random(2).shuffle(ev_order)
    pv = set(pv_order[:round(float(p["pv"]) * len(ids))])
    ev = set(ev_order[:round(float(p["ev"]) * len(ids))])
    notes, moved, light, reb = [], set(), None, []

    if p["rebalance"]:
        ok = False
        crep_path = ROOT / "results" / feeder / "monitoring" / "customer_report.csv"
        if crep_path.exists():
            crep = pd.read_csv(crep_path, dtype={"load": s, "feeder": s})
            worst = crep.sort_values("v_min_pu").iloc[0]
            fc = one[one["feeder"] == worst["feeder"]]
            counts = fc["phase"].value_counts().reindex(["A", "B", "C"], fill_value=0)
            heavy, light = counts.idxmax(), counts.idxmin()
            n_move = int((counts[heavy] - counts[light]) // 2)
            movers = fc[fc["phase"] == heavy].sort_values("distance_m", ascending=False).head(n_move)
            svc = lines[lines["phases"] == 1].set_index("bus2")
            if n_move > 0 and movers["bus"].isin(svc.index).all():
                for r in movers.itertuples(index=False):
                    ln, new = svc.loc[r.bus], PHASE_NODE[light]
                    reb += [f"edit Line.{ln['line']} bus1={ln['bus1']}{new} bus2={r.bus}{new}",
                            f"edit Load.{r.load} bus1={r.bus}{new}"]
                moved, ok = set(movers["consumer_id"]), True
                notes.append(f"Rebalancing: moved {n_move} homes on feeder {worst['feeder']} "
                             f"from phase {heavy} to {light}")
        if not ok:
            notes.append("Rebalancing: not applicable on this network")

    dss.Text.Command(f'compile "{ROOT / "opendss" / feeder / "Master.dss"}"')
    for name, vals in SHAPES.items():
        dss.Text.Command(f"New Loadshape.{name} npts=96 interval=0.25 mult=({' '.join(map(str, vals))})")
    for c in reb:
        dss.Text.Command(c)
    for r in one.itertuples(index=False):
        cid = r.consumer_id
        node = f"{r.bus}{PHASE_NODE[light if cid in moved else r.phase]}"
        if cid in pv:
            dss.Text.Command(f"New PVSystem.pv_{cid} phases=1 bus1={node} kV=0.23 kVA={PV_KW} Pmpp={PV_KW} "
                             f"irradiance=1 %cutin=0.05 %cutout=0.05 pf=1 daily=der_solar yearly=der_solar")
            if p["battery"]:
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
                dss.Text.Command(f"New Loadshape.bat_{cid} npts=96 interval=0.25 mult=({' '.join(map(str, mult))})")
                dss.Text.Command(f"New Storage.bat_{cid} phases=1 bus1={node} kV=0.23 kWrated={BAT_KW} "
                                 f"kWhrated={BAT_KWH} %stored=20 %reserve=10 dispmode=follow "
                                 f"daily=bat_{cid} yearly=bat_{cid}")
        if cid in ev:
            dss.Text.Command(f"New Load.ev_{cid} phases=1 bus1={node} kV=0.23 kW={EV_KW} pf=1 model=1 "
                             f"daily=ev_{p['mode']} yearly=ev_{p['mode']}")
    if p["voltwatt"] and pv:
        for c in VOLTWATT:
            dss.Text.Command(c)
    if p["pump_day"]:
        if (cons["load"] == "irrigation_pump").any():
            dss.Text.Command(f"New Loadshape.irrigation_day npts=24 interval=1 mult=({' '.join(map(str, PUMP_DAY))})")
            dss.Text.Command("edit Load.irrigation_pump daily=irrigation_day yearly=irrigation_day")
            notes.append("Irrigation pump moved to 09:00-16:00 (same energy)")
        else:
            notes.append("Pump option: no irrigation pump on this network")
    dss.Transformers.First()
    tname, kva0 = dss.Transformers.Name(), dss.Transformers.kVA()
    if p["tx_kva"] and float(p["tx_kva"]) != kva0:
        k = float(p["tx_kva"])
        dss.Text.Command(f"edit Transformer.{tname} kVAs=[{k} {k}]")
        notes.append(f"Transformer changed from {kva0:.0f} to {k:.0f} kVA")
    if p["source_pu"]:
        dss.Text.Command(f"edit Vsource.source pu={float(p['source_pu'])}")

    dss.Text.Command("set maxcontroliter=200 maxiterations=100")
    dss.Text.Command("set mode=yearly")
    dss.Text.Command(f"set stepsize={step}m")
    dss.Text.Command("set number=1")
    dss.Solution.Hour(0)
    dss.Solution.Seconds(0)

    nodes = [n.lower() for n in dss.Circuit.AllNodeNames()]
    bus_names = [b for b in xy if any(n.split(".")[0] == b for n in nodes)]
    bus_ix = {b: [i for i, n in enumerate(nodes) if n.split(".")[0] == b] for b in bus_names}
    cn = customer_nodes()
    ends, norm = [], []
    i = dss.Lines.First()
    while i > 0:
        ends.append((dss.Lines.Bus1().split(".")[0].lower(), dss.Lines.Bus2().split(".")[0].lower()))
        norm.append(dss.Lines.NormAmps())
        i = dss.Lines.Next()
    drawable = [k for k, (a, b) in enumerate(ends) if a in xy and b in xy]

    fv, fl, info, rows = [], [], [], []
    cmin = {k: np.inf for k in cn}
    cmax = {k: -np.inf for k in cn}
    for _ in range(24 * 60 // step):
        dss.Solution.Solve()
        conv = dss.Solution.Converged()
        v = np.array(dss.Circuit.AllBusMagPu())
        fv.append([float(v[bus_ix[b]].min()) for b in bus_names])
        lo_t, hi_t = np.inf, -np.inf
        for k, ix in cn.items():
            if ix:
                a, b = v[ix].min(), v[ix].max()
                cmin[k], cmax[k] = min(cmin[k], a), max(cmax[k], b)
                lo_t, hi_t = min(lo_t, a), max(hi_t, b)
        load, k = [], 0
        i = dss.Lines.First()
        while i > 0:
            nph = dss.CktElement.NumPhases()
            amps = max(dss.CktElement.CurrentsMagAng()[0:2 * nph:2])
            load.append(100 * amps / norm[k] if norm[k] > 0 else 0.0)
            k += 1
            i = dss.Lines.Next()
        fl.append(load)
        dss.Transformers.First()
        kva = dss.Transformers.kVA()
        dss.Circuit.SetActiveElement(f"Transformer.{dss.Transformers.Name()}")
        n = dss.CktElement.NumConductors()
        pw = dss.CktElement.Powers()[: 2 * n]
        pp, qq = sum(pw[0::2]), sum(pw[1::2])
        pct = 100 * (pp * pp + qq * qq) ** 0.5 / kva
        pvk = 0.0
        i = dss.PVsystems.First()
        while i > 0:
            pvk += dss.PVsystems.kW()
            i = dss.PVsystems.Next()
        h = dss.Solution.DblHour()
        info.append((h, pp, pct))
        rows.append({"conv": conv, "v_min": lo_t, "v_max": hi_t, "tx_pct": pct, "tx_kw": pp,
                     "line_pct": max(load) if load else 0.0, "pv_kw": pvk,
                     "loss_kw": dss.Circuit.Losses()[0] / 1000})

    df = pd.DataFrame(rows)
    sev = H * (((V_LOW - df["v_min"]).clip(lower=0) * 100).sum() + ((df["v_max"] - V_HIGH).clip(lower=0) * 100).sum()
               + (df["tx_pct"] - 100).clip(lower=0).sum() + (df["line_pct"] - 100).clip(lower=0).sum())
    peak = int(df["tx_pct"].idxmax())
    summary = {
        "severity": round(float(sev), 2),
        "violation_hours": round(float(((df["v_min"] < V_LOW).sum() + (df["v_max"] > V_HIGH).sum()
                                        + (df["tx_pct"] > 100).sum() + (df["line_pct"] > 100).sum()) * H), 2),
        "customers_out_of_limits": int(sum(1 for k in cn if cmin[k] < V_LOW or cmax[k] > V_HIGH)),
        "v_min_pu": round(float(df["v_min"].min()), 4), "v_max_pu": round(float(df["v_max"].max()), 4),
        "tx_peak_pct": round(float(df["tx_pct"].max()), 1), "tx_peak_time": hhmm(info[peak][0]),
        "max_line_pct": round(float(df["line_pct"].max()), 1),
        "max_export_kw": round(float(max(0.0, -df["tx_kw"].min())), 1),
        "pv_kwh": round(float(df["pv_kw"].sum() * H), 1),
        "losses_kwh": round(float(df["loss_kw"].sum() * H), 1),
        "nonconverged_steps": int((~df["conv"].astype(bool)).sum()),
        "notes": notes}

    bx, by = [xy[b][0] for b in bus_names], [xy[b][1] for b in bus_names]

    def seg(idx):
        xs, ys = [], []
        for k in idx:
            (x1, y1), (x2, y2) = xy[ends[k][0]], xy[ends[k][1]]
            xs += [x1, x2, None]
            ys += [y1, y2, None]
        return (xs or [None]), (ys or [None])

    def dynamic(t):
        out = []
        for lo, hi, col, name in BANDS:
            xs, ys = seg([k for k in drawable if lo <= fl[t][k] < hi])
            out.append(go.Scatter(x=xs, y=ys, mode="lines", line=dict(color=col, width=3),
                                  name=f"Line loading {name}", hoverinfo="skip"))
        vb = fv[t]
        out.append(go.Scatter(x=bx, y=by, mode="markers", name="Bus voltage (pu)",
                              text=[f"{b}: {val:.3f} pu ({val * 230:.0f} V)" for b, val in zip(bus_names, vb)],
                              hoverinfo="text",
                              marker=dict(size=7, color=vb, cmin=0.88, cmax=1.12, colorscale=VSCALE,
                                          line=dict(width=0.4, color="#333"),
                                          colorbar=dict(title="Voltage (pu)", x=1.02,
                                                        tickvals=[0.90, 0.94, 1.00, 1.06, 1.10],
                                                        ticktext=["0.90", "0.94 (limit)", "1.00", "1.06",
                                                                  "1.10 (limit)"]))))
        return out

    def title(t):
        h, pp, pct = info[t]
        flow = f"{pp:.0f} kW import" if pp >= 0 else f"{-pp:.0f} kW export"
        return f"{hhmm(h)} | transformer {pct:.0f}% ({flow}) | voltage {min(fv[t]):.3f} to {max(fv[t]):.3f} pu"

    gx, gy = seg(drawable)
    fig = go.Figure(
        data=[go.Scatter(x=gx, y=gy, mode="lines", line=dict(color="#d0d0d0", width=1), name="Network",
                         hoverinfo="skip")] + dynamic(peak) +
             [go.Scatter(x=[xy[lv][0]], y=[xy[lv][1]], mode="markers+text", text=["Transformer"],
                         textposition="top center", marker=dict(size=16, symbol="square", color="black"),
                         name="Transformer", hoverinfo="name")],
        frames=[go.Frame(data=dynamic(t), traces=[1, 2, 3, 4, 5], name=str(t),
                         layout=go.Layout(title_text=title(t))) for t in range(len(info))])
    fig.update_layout(
        title_text=title(peak), template="plotly_white", height=860,
        xaxis=dict(title="x (m, local grid)", scaleanchor="y"), yaxis=dict(title="y (m)"),
        legend=dict(orientation="h", x=0, y=1.0, yanchor="bottom", font=dict(size=11)), margin=dict(t=150, b=160),
        updatemenus=[dict(type="buttons", direction="left", x=0, xanchor="left", y=-0.14, yanchor="top",
                          buttons=[dict(label="Play", method="animate",
                                        args=[None, {"frame": {"duration": 250, "redraw": True}, "fromcurrent": True,
                                                     "transition": {"duration": 0}}]),
                                   dict(label="Pause", method="animate",
                                        args=[[None], {"frame": {"duration": 0, "redraw": False},
                                                       "mode": "immediate"}])])],
        sliders=[dict(active=peak, x=0.16, len=0.84, y=-0.14, yanchor="top", ticklen=0, tickcolor="rgba(0,0,0,0)", font=dict(size=1, color="rgba(0,0,0,0)"), currentvalue=dict(prefix="Time: ", font=dict(size=14, color="#333")),
                      steps=[dict(method="animate", label=hhmm(info[t][0]),
                                  args=[[str(t)], {"mode": "immediate", "frame": {"duration": 0, "redraw": True},
                                                   "transition": {"duration": 0}}]) for t in range(len(info))])])
    return fig.to_json(), summary


if __name__ == "__main__":
    feeder = sys.argv[1] if len(sys.argv) > 1 else "zimbabwe_rural"
    params = {}
    for a in sys.argv[2:]:
        k, v = a.split("=", 1)
        params[k] = (v.lower() in ("1", "true", "yes")) if k in ("battery", "voltwatt", "rebalance", "pump_day") \
            else (v if k == "mode" else float(v))
    fig_json, summary = run(feeder, params)
    import plotly.io as pio
    out = ROOT / "results" / feeder / "animation"
    out.mkdir(parents=True, exist_ok=True)
    pio.from_json(fig_json).write_html(out / "whatif.html", include_plotlyjs="cdn")
    for k, v in summary.items():
        print(f"{k}: {v}")
    print(f"Saved results/{feeder}/animation/whatif.html (opens at the peak-loading moment)")

