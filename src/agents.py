"""Phase 11: rule-based agents. Every number comes from the twin simulation or real data.

- Forecast agent: monitors day-ahead forecast bias on real Ausgrid data and switches to
  the level-adjusted model after a sustained bias (e.g. a load transfer). Uses only
  information available before each forecast day.
- Visibility agent: runs the twin for a situation and lists every limit violation.
- Coordination agent: generates every combination of flexibility actions, re-simulates
  each, rejects any that breaks a limit, and ranks the rest. All options deliver the
  same EV and pump energy.
- Decision support agent: writes a plain report of the verified result.
Agents are deterministic rules, not machine-learned or language-model agents.
Usage: python src/agents.py forecast | zimbabwe_rural | zimbabwe_urban
"""
import sys
import math
import random
import itertools
from pathlib import Path
import numpy as np
import pandas as pd
import opendssdirect as dss

ROOT = Path(__file__).resolve().parents[1]
MODE = sys.argv[1] if len(sys.argv) > 1 else "zimbabwe_rural"
STEP_MIN = 15
H = STEP_MIN / 60
V_LOW, V_HIGH = 0.94, 1.10
PV_KW, BAT_KW, BAT_KWH, EV_KW = 5.0, 5.0, 10.0, 7.0
PENETRATION, EV_SHARE = 0.5, 0.3
BAT_CHARGE_FRAC = 0.35
BIAS_TH, BIAS_DAYS = 0.10, 2
PHASE_NODE = {"A": ".1", "B": ".2", "C": ".3"}
VOLTWATT = ["New XYcurve.vw_curve npts=4 Yarray=[1 1 0.2 0.2] Xarray=[0 1.10 1.13 2]",
            "New InvControl.vw mode=VOLTWATT voltwatt_curve=vw_curve VoltwattYAxis=PMPPPU"]
PUMP_DAY = [0] * 9 + [1] * 7 + [0] * 8      # 09:00-16:00, same 7 h-equivalent as night schedule


def shape96(fn):
    return [round(fn(t * 0.25), 4) for t in range(96)]


SHAPES = {
    "der_solar": shape96(lambda h: max(0.0, math.sin(math.pi * (h - 6) / 12)) if 6 < h < 18 else 0.0),
    "ev_uncontrolled": shape96(lambda h: 1.0 if 18 <= h < 20 else 0.0),
    "ev_managed": shape96(lambda h: 0.3333 if (h >= 23 or h < 5) else 0.0),
    "ev_slow": shape96(lambda h: 0.2 if (h >= 21 or h < 7) else 0.0),
}


# ---------------------------------------------------------------- forecast agent
def forecast_agent():
    rows = []
    for d in sorted((ROOT / "results" / "forecasting").iterdir()):
        f = d / "test_predictions.csv"
        if not f.exists():
            continue
        p = pd.read_csv(f, index_col=0, parse_dates=True)
        day = (p.index - pd.Timedelta(minutes=15)).normalize()
        g = p.groupby(day)
        bias = (g["xgb_weather"].mean() - g["actual"].mean()) / g["actual"].apply(lambda s: s.abs().mean())
        over, sign = bias.abs() > BIAS_TH, np.sign(bias)
        flag = pd.Series(True, index=bias.index)
        for k in range(1, BIAS_DAYS + 1):
            flag &= over.shift(k, fill_value=False) & (sign.shift(k) == sign.shift(1))
        use_level = pd.Series(day, index=p.index).map(flag).fillna(False).astype(bool).values
        adaptive = np.where(use_level, p["xgb_weather_level"], p["xgb_weather"])

        def mae(x):
            return round(float(np.mean(np.abs(np.asarray(x) - p["actual"].values))), 3)
        episodes = int((flag & ~flag.shift(1, fill_value=False)).sum())
        first = flag[flag].index.min()
        rows.append({"substation": d.name, "test_days": len(bias), "days_switched": int(flag.sum()),
                     "switch_episodes": episodes,
                     "first_switch": first.date() if pd.notna(first) else "never",
                     "mae_weather_mw": mae(p["xgb_weather"]),
                     "mae_level_mw": mae(p["xgb_weather_level"]),
                     "mae_adaptive_mw": mae(adaptive)})
    t = pd.DataFrame(rows)
    out = ROOT / "results" / "agents"
    out.mkdir(parents=True, exist_ok=True)
    t.to_csv(out / "forecast_agent.csv", index=False)
    print(f"=== Forecast agent (real Ausgrid data; switch after {BIAS_DAYS} days of "
          f"same-sign bias above {BIAS_TH:.0%}) ===")
    print(t.to_string(index=False))
    print("Saved results/agents/forecast_agent.csv")


# ---------------------------------------------------------------- twin agents
def house_profile(load):
    dss.Loads.Name(load)
    kw, shp = dss.Loads.kW(), dss.Loads.Daily()
    if not shp:
        return [kw] * 96
    dss.LoadShape.Name(shp)
    mult, interval = list(dss.LoadShape.PMult()), dss.LoadShape.HrInterval()
    return [kw * mult[int(t * 0.25 / interval) % len(mult)] for t in range(96)]


def customer_nodes():
    idx = {n.lower(): i for i, n in enumerate(dss.Circuit.AllNodeNames())}
    out = {}
    i = dss.Loads.First()
    while i > 0:
        name = dss.Loads.Name()
        if not name.startswith("ev_"):
            parts = dss.CktElement.BusNames()[0].lower().split(".")
            phs = [p for p in parts[1:] if p != "0"] or ["1", "2", "3"]
            out[name] = [idx[f"{parts[0]}.{p}"] for p in phs if f"{parts[0]}.{p}" in idx]
        i = dss.Loads.Next()
    return out


def describe(c):
    parts = [f"EV {c['ev']}"]
    for k, label in [("battery", "smart battery"), ("voltwatt", "volt-watt"),
                     ("rebalance", "rebalance phases"), ("pump_day", "pump to daytime")]:
        if c.get(k):
            parts.append(label)
    return " + ".join(parts)


def twin_agents(feeder):
    master = ROOT / "opendss" / feeder / "Master.dss"
    assets = ROOT / "data" / "assets" / feeder
    out = ROOT / "results" / feeder / "agents"
    s = str
    consumers = pd.read_csv(assets / "consumers.csv", dtype={"bus": s, "load": s, "feeder": s})
    lines = pd.read_csv(assets / "lines.csv", dtype={"line": s, "bus1": s, "bus2": s})
    crep = pd.read_csv(ROOT / "results" / feeder / "monitoring" / "customer_report.csv",
                       dtype={"load": s, "feeder": s})
    one = consumers[consumers["phase"].isin(["A", "B", "C"])].reset_index(drop=True)
    ids = one["consumer_id"].tolist()
    pv_order, ev_order = ids[:], ids[:]
    random.Random(1).shuffle(pv_order)
    random.Random(2).shuffle(ev_order)
    pv = set(pv_order[:round(PENETRATION * len(ids))])
    ev = set(ev_order[:round(EV_SHARE * len(ids))])
    load_of = dict(zip(consumers["consumer_id"], consumers["load"]))

    worst = crep.sort_values("v_min_pu").iloc[0]
    fc = one[one["feeder"] == worst["feeder"]]
    counts = fc["phase"].value_counts().reindex(["A", "B", "C"], fill_value=0)
    heavy, light = counts.idxmax(), counts.idxmin()
    n_move = int((counts[heavy] - counts[light]) // 2)
    movers = fc[fc["phase"] == heavy].sort_values("distance_m", ascending=False).head(n_move)
    svc = lines[lines["phases"] == 1].set_index("bus2")
    can_rebalance = (worst["hours_below_limit"] > 0 and n_move > 0
                     and movers["bus"].isin(svc.index).all())
    moved = set(movers["consumer_id"]) if can_rebalance else set()
    has_pump = bool((consumers["load"] == "irrigation_pump").any())

    def build(c):
        cmds = []
        if c.get("rebalance"):
            for r in movers.itertuples(index=False):
                ln, new = svc.loc[r.bus], PHASE_NODE[light]
                cmds += [f"edit Line.{ln['line']} bus1={ln['bus1']}{new} bus2={r.bus}{new}",
                         f"edit Load.{r.load} bus1={r.bus}{new}"]
        for r in one.itertuples(index=False):
            cid = r.consumer_id
            ph = light if (c.get("rebalance") and cid in moved) else r.phase
            node = f"{r.bus}{PHASE_NODE[ph]}"
            if cid in pv:
                cmds.append(f"New PVSystem.pv_{cid} phases=1 bus1={node} kV=0.23 kVA={PV_KW} "
                            f"Pmpp={PV_KW} irradiance=1 %cutin=0.05 %cutout=0.05 pf=1 "
                            f"daily=der_solar yearly=der_solar")
                if c.get("battery"):
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
                cmds.append(f"New Load.ev_{cid} phases=1 bus1={node} kV=0.23 kW={EV_KW} pf=1 "
                            f"model=1 daily=ev_{c['ev']} yearly=ev_{c['ev']}")
        if c.get("voltwatt"):
            cmds += VOLTWATT
        if c.get("pump_day"):
            cmds += [f"New Loadshape.irrigation_day npts=24 interval=1 mult=({' '.join(map(str, PUMP_DAY))})",
                     "edit Load.irrigation_pump daily=irrigation_day yearly=irrigation_day"]
        return cmds

    def simulate(c):
        dss.Text.Command(f'compile "{master}"')
        for name, vals in SHAPES.items():
            dss.Text.Command(f"New Loadshape.{name} npts=96 interval=0.25 mult=({' '.join(map(str, vals))})")
        for cmd in build(c):
            dss.Text.Command(cmd)
        dss.Text.Command("set maxcontroliter=200 maxiterations=100")
        dss.Text.Command("set mode=yearly")
        dss.Text.Command(f"set stepsize={STEP_MIN}m")
        dss.Text.Command("set number=1")
        dss.Solution.Hour(0)
        dss.Solution.Seconds(0)
        cn = customer_nodes()
        cmin = {k: np.inf for k in cn}
        cmax = {k: -np.inf for k in cn}
        steps = []
        for _ in range(24 * 60 // STEP_MIN):
            dss.Solution.Solve()
            conv = dss.Solution.Converged()
            v_all = np.array(dss.Circuit.AllBusMagPu())
            vmin_t, vmax_t = np.inf, -np.inf
            for k, ix in cn.items():
                if ix:
                    lo, hi = v_all[ix].min(), v_all[ix].max()
                    cmin[k], cmax[k] = min(cmin[k], lo), max(cmax[k], hi)
                    vmin_t, vmax_t = min(vmin_t, lo), max(vmax_t, hi)
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
            pvk = 0.0
            i = dss.PVsystems.First()
            while i > 0:
                pvk += dss.PVsystems.kW()
                i = dss.PVsystems.Next()
            steps.append({"conv": conv, "tx_pct": 100 * (p * p + q * q) ** 0.5 / kva, "tx_kw": p,
                          "line_pct": mx, "v_min": vmin_t, "v_max": vmax_t, "pv_kw": pvk,
                          "loss_kw": dss.Circuit.Losses()[0] / 1000})
        df = pd.DataFrame(steps)
        out_cust = sorted(k for k in cn if cmin[k] < V_LOW or cmax[k] > V_HIGH)
        r = {"hours_undervoltage": (df["v_min"] < V_LOW).sum() * H,
             "hours_overvoltage": (df["v_max"] > V_HIGH).sum() * H,
             "hours_tx_overload": (df["tx_pct"] > 100).sum() * H,
             "hours_line_overload": (df["line_pct"] > 100).sum() * H,
             "customers_out_of_limits": len(out_cust),
             "v_min_pu": round(df["v_min"].min(), 4), "v_max_pu": round(df["v_max"].max(), 4),
             "max_tx_loading_pct": round(df["tx_pct"].max(), 1),
             "max_line_pct": round(df["line_pct"].max(), 1),
             "max_export_kw": round(max(0.0, -df["tx_kw"].min()), 1),
             "pv_kwh": round(df["pv_kw"].sum() * H, 1), "losses_kwh": round(df["loss_kw"].sum() * H, 1),
             "nonconverged_steps": int((~df["conv"].astype(bool)).sum())}
        r["violation_hours"] = round(r["hours_undervoltage"] + r["hours_overvoltage"]
                                     + r["hours_tx_overload"] + r["hours_line_overload"], 2)
        r["severity"] = round(H * (((V_LOW - df["v_min"]).clip(lower=0) * 100).sum() + ((df["v_max"] - V_HIGH).clip(lower=0) * 100).sum() + (df["tx_pct"] - 100).clip(lower=0).sum() + (df["line_pct"] - 100).clip(lower=0).sum()), 2)
        return r, out_cust

    # energy checks: every EV option delivers the same energy; the pump keeps its hours
    ev_kwh = {k: round(sum(SHAPES[f"ev_{k}"]) * 0.25 * EV_KW, 2) for k in ["uncontrolled", "managed", "slow"]}
    pump_note = ""
    if has_pump:
        dss.Text.Command(f'compile "{master}"')
        dss.Loads.Name("irrigation_pump")
        dss.LoadShape.Name(dss.Loads.Daily())
        night_h = sum(dss.LoadShape.PMult())
        pump_note = f"pump equivalent full-load hours: night {night_h:.1f}, daytime option {sum(PUMP_DAY):.1f}"

    opts = {"ev": ["uncontrolled", "managed", "slow"], "battery": [False, True], "voltwatt": [False, True]}
    if can_rebalance:
        opts["rebalance"] = [False, True]
    if has_pump:
        opts["pump_day"] = [False, True]
    cands = [dict(zip(opts.keys(), vals)) for vals in itertools.product(*opts.values())]

    print(f"=== {feeder}: situation = {PENETRATION:.0%} rooftop PV + {EV_SHARE:.0%} EVs (simulated) ===")
    print(f"Coordination agent: testing {len(cands)} action combinations by re-simulation ...")
    rows, do_nothing_cust = [], []
    for k, c in enumerate(cands, start=1):
        r, cust = simulate(c)
        if not any(c.get(x) for x in ["battery", "voltwatt", "rebalance", "pump_day"]) and c["ev"] == "uncontrolled":
            do_nothing_cust = cust
        rows.append({"combination": describe(c), **c, **r})
        print(f"  {k:2d}/{len(cands)} {describe(c)}: {r['violation_hours']} violation-hours")
    t = pd.DataFrame(rows)
    base = t[t["combination"] == "EV uncontrolled"].iloc[0]
    t["n_actions"] = sum(t.get(k, pd.Series(False, index=t.index)).astype(int) for k in ["battery", "voltwatt", "rebalance", "pump_day"]) + (t["ev"] != "uncontrolled").astype(int)
    t["curtail_pct"] = (100 * (1 - t["pv_kwh"] / base["pv_kwh"])).round(1)
    t["valid"] = (t["nonconverged_steps"] == 0) & (t["v_max_pu"] < 1.5) & (t["v_min_pu"] > 0.5)
    t["feasible"] = t["valid"] & (t["violation_hours"] == 0)
    n_invalid = int((~t["valid"]).sum())
    t = t.sort_values(["valid", "feasible", "severity", "customers_out_of_limits", "curtail_pct", "n_actions", "max_tx_loading_pct", "losses_kwh"], ascending=[False, False, True, True, True, True, True, True]).reset_index(drop=True)
    out.mkdir(parents=True, exist_ok=True)
    t.to_csv(out / "coordination_results.csv", index=False)
    best = t.iloc[0]
    alts = []
    no_pump = ~t.get("pump_day", pd.Series(False, index=t.index)).astype(bool)
    no_field = no_pump & ~t.get("rebalance", pd.Series(False, index=t.index)).astype(bool)
    for label, mask in [("without moving the pump", no_pump),
                        ("without field work (no pump move, no rebalancing)", no_field)]:
        sub = t[t["valid"] & mask]
        if len(sub) and sub.iloc[0]["combination"] != best["combination"]:
            alts.append((label, sub.iloc[0]))

    show = ["combination", "valid", "feasible", "severity", "violation_hours", "customers_out_of_limits", "v_min_pu", "v_max_pu",
            "max_tx_loading_pct", "max_line_pct", "curtail_pct", "losses_kwh"]
    print(f"\nVisibility agent (do nothing): {base['violation_hours']} violation-hours | "
          f"undervoltage {base['hours_undervoltage']} h, overvoltage {base['hours_overvoltage']} h, "
          f"transformer overload {base['hours_tx_overload']} h, line overload {base['hours_line_overload']} h | "
          f"{base['customers_out_of_limits']} customers out of limits")
    print(f"Energy checks: EV kWh per car {ev_kwh}" + (f"; {pump_note}" if pump_note else ""))
    print("Invalid results (solver did not converge or implausible):", n_invalid)
    print(f"Feasible combinations (no violation anywhere): {int(t['feasible'].sum())} of {len(t)}")
    print("\nTop 8:")
    print(t.head(8)[show].to_string(index=False))

    verdict = ("RECOMMENDED (no limit violations)" if best["feasible"]
               else "BEST AVAILABLE (no combination removes every violation)")
    lines_md = [
        f"# Decision support report: {feeder}",
        "",
        f"**Situation (simulated):** {PENETRATION:.0%} of homes with 5 kW rooftop PV and {EV_SHARE:.0%} with a 7 kW EV.",
        "All figures come from re-running the OpenDSS network model. Networks are synthetic.",
        "",
        "## Problem (do nothing)",
        f"- Violation-hours: {base['violation_hours']} (undervoltage {base['hours_undervoltage']} h, "
        f"overvoltage {base['hours_overvoltage']} h, transformer overload {base['hours_tx_overload']} h, "
        f"line overload {base['hours_line_overload']} h)",
        f"- Customers outside {V_LOW}-{V_HIGH} pu: {base['customers_out_of_limits']}"
        + (f" ({', '.join(do_nothing_cust[:12])}{' ...' if len(do_nothing_cust) > 12 else ''})" if do_nothing_cust else ""),
        f"- Voltage range {base['v_min_pu']} to {base['v_max_pu']} pu; transformer peak {base['max_tx_loading_pct']}%; "
        f"line peak {base['max_line_pct']}%",
        "",
        f"## {verdict}",
        f"**{best['combination']}**",
        "**Status:** verified in simulation only; not applied. Requires operator approval before any field action.",
        f"- Severity score (1 = 1% beyond a limit for 1 hour): {base['severity']} -> {best['severity']}",
        f"- Violation-hours: {base['violation_hours']} -> {best['violation_hours']}",
        f"- Customers out of limits: {base['customers_out_of_limits']} -> {best['customers_out_of_limits']}",
        f"- Voltage range: {best['v_min_pu']} to {best['v_max_pu']} pu",
        f"- Transformer peak: {base['max_tx_loading_pct']}% -> {best['max_tx_loading_pct']}%",
        f"- Solar curtailed: {best['curtail_pct']}%; losses {best['losses_kwh']} kWh/day",
        "",
        f"## Tested: {len(t)} combinations, {int(t['feasible'].sum())} feasible",
        "",
        t.head(10)[show].to_markdown(index=False) if hasattr(t, "to_markdown") else "",
        "",
        "## Alternatives for the operator",
        *[f"- Best {lab}: **{a['combination']}** (severity {a['severity']}, violation-hours {a['violation_hours']}, "
          f"customers out of limits {a['customers_out_of_limits']}, transformer {a['max_tx_loading_pct']}%)"
          for lab, a in alts],
        "- None needed: the recommended combination requires no field work." if not alts else "",
        "",
        "## Caveats",
        "- Rule-based controls on a synthetic network with hypothetical DER and EVs; one clear-sky day.",
        f"- Every EV option delivers the same energy per car ({ev_kwh['uncontrolled']} kWh)."
        + (f" {pump_note}." if pump_note else ""),
        "- Moving irrigation to daytime increases evaporation losses and needs the farmer's agreement.",
        "- Phase rebalancing requires a field crew to reconnect service cables.",
        "- Volt-watt settles voltage just above 1.10 pu by design (control band 253-260 V).",
    ]
    (out / "decision_report.md").write_text("\n".join(lines_md), encoding="utf-8")
    print(f"\nDecision support agent: {verdict}")
    print(f"  {best['combination']}")
    print(f"  violation-hours {base['violation_hours']} -> {best['violation_hours']}, customers out of limits "
          f"{base['customers_out_of_limits']} -> {best['customers_out_of_limits']}, transformer "
          f"{base['max_tx_loading_pct']}% -> {best['max_tx_loading_pct']}%, curtailment {best['curtail_pct']}%")
    for lab, a in alts:
        print(f"  Alternative {lab}: {a['combination']} (severity {a['severity']}, "
              f"violation-hours {a['violation_hours']}, customers out {a['customers_out_of_limits']})")
    print("  Status: verified in simulation only; requires operator approval before any field action.")
    print(f"Saved results/{feeder}/agents/ (coordination_results.csv, decision_report.md)")


if __name__ == "__main__":
    if MODE == "forecast":
        forecast_agent()
    else:
        twin_agents(MODE)



