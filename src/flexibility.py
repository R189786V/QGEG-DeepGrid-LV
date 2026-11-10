"""Phase 10: flexibility and network relief actions, each tested by re-simulation.

Every effect reported comes from re-running the OpenDSS model with the action
applied; nothing is estimated by hand. DER and EV scenarios are the hypothetical
ones from Phase 6 (same homes, same sizes). Actions are simple rule-based
controls, not optimised schedules.
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
FEEDER = sys.argv[1] if len(sys.argv) > 1 else "zimbabwe_rural"
MASTER = ROOT / "opendss" / FEEDER / "Master.dss"
ASSETS = ROOT / "data" / "assets" / FEEDER
OUT = ROOT / "results" / FEEDER / "flexibility"

STEP_MIN = 15
V_LOW, V_HIGH = 0.94, 1.10
PV_KW, BAT_KW, BAT_KWH, EV_KW = 5.0, 5.0, 10.0, 7.0
PENETRATION, EV_SHARE = 0.5, 0.3
PV_CAP_PCT = 60
BAT_CHARGE_FRAC = 0.35          # smart battery absorbs this share of PV output, 09:00-15:00
MOTOR_OPTIONS = {"direct-on-line": (6.0, 0.30), "soft starter": (3.0, 0.30), "VSD": (1.2, 0.95)}
PHASE_NODE = {"A": ".1", "B": ".2", "C": ".3"}
PHASE_LIST = {"A": [".1"], "B": [".2"], "C": [".3"], "3ph": [".1", ".2", ".3"]}
VOLTWATT = ["New XYcurve.vw_curve npts=4 Yarray=[1 1 0.2 0.2] Xarray=[0 1.10 1.13 2]",
            "New InvControl.vw mode=VOLTWATT voltwatt_curve=vw_curve VoltwattYAxis=PMPPPU"]


def shape96(fn):
    return [round(fn(t * 0.25), 4) for t in range(96)]


SHAPES = {
    "der_solar": shape96(lambda h: max(0.0, math.sin(math.pi * (h - 6) / 12)) if 6 < h < 18 else 0.0),
    "der_bat": shape96(lambda h: -0.5 if 10 <= h < 14 else (0.67 if 18 <= h < 21 else 0.0)),
    "der_ev": shape96(lambda h: 1.0 if 18 <= h < 20 else 0.0),
    "der_ev_managed": shape96(lambda h: round(1 / 3, 4) if (h >= 23 or h < 5) else 0.0),
}


def house_profile(load):
    dss.Loads.Name(load)
    kw, shp = dss.Loads.kW(), dss.Loads.Daily()
    if not shp:
        return [kw] * 96
    dss.LoadShape.Name(shp)
    mult, interval = list(dss.LoadShape.PMult()), dss.LoadShape.HrInterval()
    return [kw * mult[int(t * 0.25 / interval) % len(mult)] for t in range(96)]


def simulate(builder, watch_bus):
    dss.Text.Command(f'compile "{MASTER}"')
    for name, vals in SHAPES.items():
        dss.Text.Command(f"New Loadshape.{name} npts=96 interval=0.25 mult=({' '.join(str(v) for v in vals)})")
    for cmd in builder():
        dss.Text.Command(cmd)
    dss.Text.Command("set maxcontroliter=200 maxiterations=100")
    dss.Text.Command("set mode=yearly")
    dss.Text.Command(f"set stepsize={STEP_MIN}m")
    dss.Text.Command("set number=1")
    dss.Solution.Hour(0)
    dss.Solution.Seconds(0)
    nodes = [n.lower() for n in dss.Circuit.AllNodeNames()]
    widx = [i for i, n in enumerate(nodes) if n.split(".")[0] == watch_bus]
    rows = []
    for _ in range(24 * 60 // STEP_MIN):
        dss.Solution.Solve()
        if not dss.Solution.Converged():
            print(f"  warning: not converged at hour {dss.Solution.DblHour():.2f}")
        v_all = np.array(dss.Circuit.AllBusMagPu())
        v = v_all[v_all > 0]
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
        pv = 0.0
        i = dss.PVsystems.First()
        while i > 0:
            pv += dss.PVsystems.kW()
            i = dss.PVsystems.Next()
        rows.append({"hour": round(dss.Solution.DblHour(), 3), "converged": dss.Solution.Converged(), "tx_kw": p,
                     "tx_loading_pct": 100 * (p * p + q * q) ** 0.5 / kva,
                     "v_min": v.min(), "v_max": v.max(), "max_line_pct": mx, "pv_kw": pv,
                     "watch_v": v_all[widx].min() if widx else np.nan,
                     "losses_kw": dss.Circuit.Losses()[0] / 1000})
    return pd.DataFrame(rows)


def summarise(df):
    h = STEP_MIN / 60
    return {"nonconverged_steps": int((~df["converged"].astype(bool)).sum()), "max_tx_loading_pct": round(df["tx_loading_pct"].max(), 1),
            "peak_import_kw": round(df["tx_kw"].max(), 1),
            "max_export_kw": round(max(0.0, -df["tx_kw"].min()), 1),
            "v_max_pu": round(df["v_max"].max(), 4), "h_over_v": (df["v_max"] > V_HIGH).sum() * h,
            "v_min_pu": round(df["v_min"].min(), 4), "h_under_v": (df["v_min"] < V_LOW).sum() * h,
            "watch_v_min": round(df["watch_v"].min(), 4),
            "watch_h_below": (df["watch_v"] < V_LOW).sum() * h,
            "max_line_pct": round(df["max_line_pct"].max(), 1),
            "pv_kwh": round(df["pv_kw"].sum() * h, 1),
            "losses_kwh": round(df["losses_kw"].sum() * h, 1)}


def motor_options(consumers):
    def snapshot():
        dss.Text.Command(f'compile "{MASTER}"')
        dss.Text.Command("set mode=snapshot")
    snapshot()
    idx = {n.lower(): i for i, n in enumerate(dss.Circuit.AllNodeNames())}
    cnodes = [[idx[f"{b}{s}"] for s in PHASE_LIST[p]] for b, p in zip(consumers["bus"], consumers["phase"])]

    def cust_v():
        v = np.array(dss.Circuit.AllBusMagPu())
        return np.array([v[ix].min() for ix in cnodes])

    motors = consumers[(consumers["phase"] == "3ph") & consumers["load"].str.contains("pump|mill", case=False)]
    rows = []
    for m in motors.itertuples(index=False):
        mask = consumers["load"].values != m.load
        for opt, (mult, pf) in MOTOR_OPTIONS.items():
            snapshot()
            dss.Text.Command(f"edit Load.{m.load} enabled=no")
            dss.Solution.Solve()
            before = cust_v()
            s_start = mult * m.kw / m.pf
            p_st, q_st = s_start * pf, s_start * math.sqrt(1 - pf ** 2)
            dss.Text.Command(f"edit Load.{m.load} enabled=yes model=2 kW={p_st:.2f} kvar={q_st:.2f}")
            dss.Solution.Solve()
            dip = 100 * (before - cust_v()) / before
            rows.append({"motor": m.load, "starting": opt, "start_kva": round(s_start, 1),
                         "max_dip_pct": round(float(dip[mask].max()), 2),
                         "customers_over_4pct": int((dip[mask] > 4).sum()),
                         "customers_over_6pct": int((dip[mask] > 6).sum())})
    return pd.DataFrame(rows)


def main():
    s = str
    consumers = pd.read_csv(ASSETS / "consumers.csv", dtype={"bus": s, "load": s, "feeder": s})
    lines = pd.read_csv(ASSETS / "lines.csv", dtype={"line": s, "bus1": s, "bus2": s})
    crep = pd.read_csv(ROOT / "results" / FEEDER / "monitoring" / "customer_report.csv",
                       dtype={"load": s, "feeder": s})
    one = consumers[consumers["phase"].isin(["A", "B", "C"])].reset_index(drop=True)
    ids = one["consumer_id"].tolist()
    pv_order, ev_order = ids[:], ids[:]
    random.Random(1).shuffle(pv_order)
    random.Random(2).shuffle(ev_order)
    pv = set(pv_order[:round(PENETRATION * len(ids))])
    ev = set(ev_order[:round(EV_SHARE * len(ids))])

    worst = crep.sort_values("v_min_pu").iloc[0]
    watch_bus = consumers.set_index("consumer_id").loc[worst["consumer_id"], "bus"]

    def der(pv_set=(), ev_set=(), ev_shape="der_ev", bat_set=(), bat_mode=None, pv_cap=None, voltwatt=False):
        def build():
            cmds = []
            for r in one.itertuples(index=False):
                node, cid = f"{r.bus}{PHASE_NODE[r.phase]}", r.consumer_id
                if cid in pv_set:
                    cap = f" %Pmpp={pv_cap}" if pv_cap else ""
                    cmds.append(f"New PVSystem.pv_{cid} phases=1 bus1={node} kV=0.23 kVA={PV_KW} "
                                f"Pmpp={PV_KW} irradiance=1 %cutin=0.05 %cutout=0.05 pf=1 "
                                f"daily=der_solar yearly=der_solar{cap}")
                if cid in bat_set:
                    shp = "der_bat"
                    if bat_mode == "smart":
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
                        shp = f"bat_{cid}"
                        cmds.append(f"New Loadshape.{shp} npts=96 interval=0.25 mult=({' '.join(map(str, mult))})")
                    cmds.append(f"New Storage.bat_{cid} phases=1 bus1={node} kV=0.23 kWrated={BAT_KW} "
                                f"kWhrated={BAT_KWH} %stored=20 %reserve=10 dispmode=follow "
                                f"daily={shp} yearly={shp}")
                if cid in ev_set:
                    cmds.append(f"New Load.ev_{cid} phases=1 bus1={node} kV=0.23 kW={EV_KW} pf=1 "
                                f"model=1 daily={ev_shape} yearly={ev_shape}")
            if voltwatt:
                cmds += VOLTWATT
            return cmds
        return build

    scenarios = [("base (no DER)", der())]

    fc = one[one["feeder"] == worst["feeder"]]
    counts = fc["phase"].value_counts().reindex(["A", "B", "C"], fill_value=0)
    heavy, light = counts.idxmax(), counts.idxmin()
    n_move = int((counts[heavy] - counts[light]) // 2)
    movers = fc[fc["phase"] == heavy].sort_values("distance_m", ascending=False).head(n_move)
    svc = lines[lines["phases"] == 1].set_index("bus2")
    rebalance_note = "not needed"
    if worst["hours_below_limit"] > 0 and n_move > 0 and movers["bus"].isin(svc.index).all():
        def rebalance():
            cmds = []
            for r in movers.itertuples(index=False):
                ln, new = svc.loc[r.bus], PHASE_NODE[light]
                cmds += [f"edit Line.{ln['line']} bus1={ln['bus1']}{new} bus2={r.bus}{new}",
                         f"edit Load.{r.load} bus1={r.bus}{new}"]
            return cmds
        rebalance_note = (f"moved {n_move} homes on feeder {worst['feeder']} from phase {heavy} "
                          f"to {light}: {', '.join(movers['consumer_id'])}")
        scenarios.append(("rebalance phases", rebalance))

    scenarios += [
        ("ev30 uncontrolled", der(ev_set=ev)),
        ("ev30 managed", der(ev_set=ev, ev_shape="der_ev_managed")),
        ("pv50", der(pv_set=pv)),
        ("pv50 + fixed battery", der(pv_set=pv, bat_set=pv, bat_mode="fixed")),
        ("pv50 + smart battery", der(pv_set=pv, bat_set=pv, bat_mode="smart")),
        ("pv50 + volt-watt", der(pv_set=pv, voltwatt=True)),
        (f"pv50 + {PV_CAP_PCT}% cap", der(pv_set=pv, pv_cap=PV_CAP_PCT)),
        ("all: pv50 + smart battery + volt-watt + ev30 managed",
         der(pv_set=pv, bat_set=pv, bat_mode="smart", voltwatt=True, ev_set=ev, ev_shape="der_ev_managed")),
    ]

    OUT.mkdir(parents=True, exist_ok=True)
    results, series = [], []
    for name, builder in scenarios:
        print(f"Running {name} ...")
        df = simulate(builder, watch_bus)
        series.append(df.assign(scenario=name))
        results.append({"scenario": name, **summarise(df)})
    table = pd.DataFrame(results)
    pv_ref = table.loc[table["scenario"] == "pv50", "pv_kwh"].iloc[0]
    table["curtail_pct"] = [round(100 * (1 - r.pv_kwh / pv_ref), 1) if r.scenario.startswith(("pv50", "all")) else np.nan
                            for r in table.itertuples()]
    table.to_csv(OUT / "scenario_results.csv", index=False)
    pd.concat(series).to_csv(OUT / "scenario_timeseries.csv", index=False)
    motors = motor_options(consumers)
    motors.to_csv(OUT / "motor_start_options.csv", index=False)

    show = ["pv50", "pv50 + smart battery", "pv50 + volt-watt", "ev30 uncontrolled", "ev30 managed",
            "all: pv50 + smart battery + volt-watt + ev30 managed"]
    fig, ax = plt.subplots(3, 1, figsize=(11, 9), sharex=True)
    for df in series:
        nm = df["scenario"].iloc[0]
        if nm in show:
            ax[0].plot(df["hour"], df["tx_kw"], label=nm)
            ax[1].plot(df["hour"], df["v_max"], label=nm)
            ax[2].plot(df["hour"], df["v_min"], label=nm)
    ax[0].axhline(0, color="black", linewidth=0.8)
    ax[0].set_ylabel("Transformer kW\n(negative = export)")
    ax[1].axhline(V_HIGH, color="red", linestyle="--")
    ax[1].set_ylabel("Highest voltage (pu)")
    ax[2].axhline(V_LOW, color="red", linestyle="--")
    ax[2].set_ylabel("Lowest voltage (pu)")
    ax[2].set_xlabel("Hour of day")
    ax[0].legend(fontsize=7)
    fig.suptitle(f"{FEEDER}: flexibility actions (simulated)")
    fig.tight_layout()
    fig.savefig(OUT / "flexibility_actions.png", dpi=150)

    cols = ["scenario", "nonconverged_steps", "max_tx_loading_pct", "peak_import_kw", "max_export_kw", "v_max_pu", "h_over_v",
            "v_min_pu", "h_under_v", "watch_v_min", "watch_h_below", "max_line_pct", "curtail_pct"]
    print(f"\n=== {FEEDER}: flexibility actions (simulated) ===")
    print(f"Watched customer: {worst['consumer_id']} (worst voltage in Phase 4)")
    print(f"Phase rebalancing: {rebalance_note}")
    print(table[cols].to_string(index=False))
    print("\nMotor starting options:")
    print(motors.to_string(index=False) if len(motors) else "  no pump or mill motors on this network")
    print(f"\nSaved to results/{FEEDER}/flexibility/")


if __name__ == "__main__":
    main()


