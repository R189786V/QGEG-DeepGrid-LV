"""Connection Check: should a new rooftop PV system or EV charger be connected?

Re-simulates the network for one day with the requested connection and returns
APPROVE, APPROVE WITH CONDITIONS (volt-watt inverter control, managed or slow EV
charging, or a smaller PV size) or REFUSE, with the numbers behind the decision.
A request passes if it does not make the network worse than today (severity score,
as in agents.py), keeps the transformer and lines within rating, and keeps the
customer's own voltage within limits (or no worse than today, if already outside).
Base case = the network as modelled today. Simulated clear-sky day; decision support only.
Usage: python src/connection_check.py zimbabwe_rural C037 --pv 6 --ev 7
       python src/connection_check.py zimbabwe_rural --list
"""
import sys
import math
import argparse
import itertools
from pathlib import Path
import numpy as np
import pandas as pd
import opendssdirect as dss

sys.path.insert(0, str(Path(__file__).resolve().parent))
from agents import customer_nodes, VOLTWATT, V_LOW, V_HIGH, STEP_MIN, H  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
EV_KWH = 14.0          # daily energy per EV
SEV_TOL = 0.05         # allowed severity increase over today
PHASE_NODE = {"A": ".1", "B": ".2", "C": ".3"}


def shape96(fn):
    return [round(fn(t * 0.25), 4) for t in range(96)]


SOLAR = shape96(lambda h: max(0.0, math.sin(math.pi * (h - 6) / 12)) if 6 < h < 18 else 0.0)


def ev_shape(kw, mode, kwh=EV_KWH):
    if mode == "uncontrolled":
        n = math.ceil(kwh / kw / 0.25)
        m = round(kwh / (n * 0.25 * kw), 4)
        return shape96(lambda h: m if 18 <= h < 18 + n * 0.25 else 0.0)
    if mode == "managed":
        return shape96(lambda h: round(kwh / 6 / kw, 4) if (h >= 23 or h < 5) else 0.0)
    return shape96(lambda h: round(kwh / 10 / kw, 4) if (h >= 21 or h < 7) else 0.0)


def request_cmds(node, pv_kw, voltwatt, ev_kw, ev_mode):
    cmds = []
    if pv_kw > 0:
        cmds.append(f"New Loadshape.der_solar npts=96 interval=0.25 mult=({' '.join(map(str, SOLAR))})")
        cmds.append(f"New PVSystem.pv_req phases=1 bus1={node} kV=0.23 kVA={pv_kw} Pmpp={pv_kw} "
                    f"irradiance=1 %cutin=0.05 %cutout=0.05 pf=1 daily=der_solar yearly=der_solar")
        if voltwatt:
            cmds += VOLTWATT
    if ev_kw > 0 and ev_mode:
        cmds.append(f"New Loadshape.ev_req npts=96 interval=0.25 "
                    f"mult=({' '.join(map(str, ev_shape(ev_kw, ev_mode)))})")
        cmds.append(f"New Load.ev_req phases=1 bus1={node} kV=0.23 kW={ev_kw} pf=1 model=1 "
                    f"daily=ev_req yearly=ev_req")
    return cmds


def simulate(master, cmds, req_bus):
    dss.Text.Command(f'compile "{master}"')
    for c in cmds:
        dss.Text.Command(c)
    dss.Text.Command("set maxcontroliter=200 maxiterations=100")
    dss.Text.Command("set mode=yearly")
    dss.Text.Command(f"set stepsize={STEP_MIN}m")
    dss.Text.Command("set number=1")
    dss.Solution.Hour(0)
    dss.Solution.Seconds(0)
    cn = customer_nodes()
    idx = {n.lower(): i for i, n in enumerate(dss.Circuit.AllNodeNames())}
    req_ix = [i for n, i in idx.items() if n.split(".")[0] == req_bus]
    rows, conv = [], True
    for _ in range(24 * 60 // STEP_MIN):
        dss.Solution.Solve()
        conv = conv and dss.Solution.Converged()
        v = np.array(dss.Circuit.AllBusMagPu())
        lo = min(v[ix].min() for ix in cn.values() if ix)
        hi = max(v[ix].max() for ix in cn.values() if ix)
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
        rows.append({"v_min": lo, "v_max": hi, "req_min": v[req_ix].min(), "req_max": v[req_ix].max(),
                     "tx_pct": 100 * (p * p + q * q) ** 0.5 / kva, "line_pct": mx, "pv_kw": pvk})
    df = pd.DataFrame(rows)
    sev = H * (((V_LOW - df["v_min"]).clip(lower=0) * 100).sum() + ((df["v_max"] - V_HIGH).clip(lower=0) * 100).sum()
               + (df["tx_pct"] - 100).clip(lower=0).sum() + (df["line_pct"] - 100).clip(lower=0).sum())
    return {"valid": bool(conv and df["v_max"].max() < 1.5 and df["v_min"].min() > 0.5),
            "severity": round(float(sev), 2),
            "req_v_min": round(float(df["req_min"].min()), 4), "req_v_max": round(float(df["req_max"].max()), 4),
            "max_tx_loading_pct": round(float(df["tx_pct"].max()), 1),
            "max_line_pct": round(float(df["line_pct"].max()), 1),
            "pv_kwh": round(float(df["pv_kw"].sum() * H), 2)}


def check(feeder, cid, pv_kw=0.0, ev_kw=0.0):
    master = ROOT / "opendss" / feeder / "Master.dss"
    cons = pd.read_csv(ROOT / "data" / "assets" / feeder / "consumers.csv", dtype={"bus": str, "load": str})
    row = cons[cons["consumer_id"] == cid]
    if row.empty:
        raise ValueError(f"Unknown customer {cid} on {feeder}")
    r = row.iloc[0]
    if r["phase"] not in PHASE_NODE:
        raise ValueError("Connection Check covers single-phase customers only")
    node = f"{r['bus']}{PHASE_NODE[r['phase']]}"
    base = simulate(master, [], r["bus"])

    def passes(m):
        own_ok = (m["req_v_min"] >= min(V_LOW, base["req_v_min"]) - 1e-4
                  and m["req_v_max"] <= max(V_HIGH, base["req_v_max"]) + 1e-4)
        return bool(m["valid"] and m["severity"] <= base["severity"] + SEV_TOL
                    and m["max_tx_loading_pct"] <= max(100.0, base["max_tx_loading_pct"])
                    and m["max_line_pct"] <= max(100.0, base["max_line_pct"]) and own_ok)

    pv_opts = ([("PV as requested", pv_kw, False), ("PV with volt-watt", pv_kw, True)]
               if pv_kw > 0 else [("no PV", 0.0, False)])
    ev_opts = ([("EV uncontrolled", "uncontrolled"), ("EV managed 23:00-05:00", "managed"),
                ("EV slow 21:00-07:00", "slow")] if ev_kw > 0 else [("no EV", None)])
    combos = sorted(itertools.product(range(len(pv_opts)), range(len(ev_opts))), key=lambda ij: (sum(ij), ij))
    tested, decision, chosen, ref_pv = [], "REFUSE", None, None
    for i, j in combos:
        (pl, pk, vw), (el, em) = pv_opts[i], ev_opts[j]
        m = simulate(master, request_cmds(node, pk, vw, ev_kw, em), r["bus"])
        if i == 0 and j == 0:
            ref_pv = m["pv_kwh"]
        m.update({"option": f"{pl} + {el}", "conditions": i + j, "pass": passes(m)})
        tested.append(m)
        if m["pass"]:
            decision = "APPROVE" if i + j == 0 else "APPROVE WITH CONDITIONS"
            chosen = m
            break
    if chosen is None and pv_kw > 0:
        em = "managed" if ev_kw > 0 else None
        lo, hi = 1, int(round(pv_kw * 2)) - 1
        while lo <= hi:
            mid = (lo + hi) // 2
            size = mid * 0.5
            m = simulate(master, request_cmds(node, size, False, ev_kw, em), r["bus"])
            m.update({"option": f"PV reduced to {size} kW" + (" + EV managed 23:00-05:00" if ev_kw > 0 else ""),
                      "conditions": 3, "pass": passes(m)})
            tested.append(m)
            if m["pass"]:
                chosen, lo = m, mid + 1
            else:
                hi = mid - 1
        if chosen is not None:
            decision = "APPROVE WITH CONDITIONS"
    t = pd.DataFrame(tested)
    if ref_pv:
        t["curtail_pct"] = [round(100 * (1 - pk / ref_pv), 1) if "volt-watt" in o else np.nan
                            for pk, o in zip(t["pv_kwh"], t["option"])]
    return {"feeder": feeder, "customer": cid, "node": node, "distance_m": r["distance_m"],
            "pv_kw": pv_kw, "ev_kw": ev_kw, "decision": decision,
            "condition": chosen["option"] if chosen is not None and decision != "APPROVE" else "",
            "base": base, "tested": t}


def report(res):
    b = res["base"]
    lines = [f"# Connection Check: {res['customer']} on {res['feeder']}", "",
             f"**Request:** {res['pv_kw']} kW rooftop PV, {res['ev_kw']} kW EV charger "
             f"({EV_KWH:.0f} kWh/day). Customer at {res['node']}, {res['distance_m']} m from the transformer.", "",
             f"## Decision: {res['decision']}" + (f" ({res['condition']})" if res["condition"] else ""), "",
             f"**Today (base case):** severity {b['severity']}, customer voltage {b['req_v_min']} to "
             f"{b['req_v_max']} pu, transformer peak {b['max_tx_loading_pct']}%, line peak {b['max_line_pct']}%.", "",
             "## Options tested (simulated)", "",
             res["tested"].drop(columns=["valid"]).to_markdown(index=False), "",
             "Pass = no worse than today (severity +0.05 allowed), transformer and lines within rating, and the "
             f"customer's own voltage within {V_LOW}-{V_HIGH} pu (or no worse than today). Simulated clear-sky day. "
             "Decision support only: requires approval by the network planner."]
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description="Connection Check for rooftop PV and EV chargers")
    ap.add_argument("feeder")
    ap.add_argument("customer", nargs="?")
    ap.add_argument("--pv", type=float, default=0.0, help="rooftop PV size, kW")
    ap.add_argument("--ev", type=float, default=0.0, help="EV charger size, kW")
    ap.add_argument("--list", action="store_true", help="list single-phase customers")
    a = ap.parse_args()
    if a.list or not a.customer:
        cons = pd.read_csv(ROOT / "data" / "assets" / a.feeder / "consumers.csv")
        print(cons[cons["phase"].isin(list(PHASE_NODE))][["consumer_id", "load", "feeder", "phase", "distance_m"]]
              .to_string(index=False))
        return
    if a.pv <= 0 and a.ev <= 0:
        sys.exit("Give --pv and/or --ev")
    if a.ev > 7.4:
        print("Note: single-phase home chargers are normally up to about 7 kW.")
    res = check(a.feeder, a.customer, a.pv, a.ev)
    md = report(res)
    out = ROOT / "results" / a.feeder / "connection_checks"
    out.mkdir(parents=True, exist_ok=True)
    stem = f"{a.customer}_pv{a.pv:g}_ev{a.ev:g}"
    (out / f"{stem}.md").write_text(md, encoding="utf-8")
    res["tested"].to_csv(out / f"{stem}.csv", index=False)
    print(f"=== Connection Check: {a.customer} on {a.feeder} | PV {a.pv:g} kW, EV {a.ev:g} kW ===")
    print(f"DECISION: {res['decision']}" + (f" -> {res['condition']}" if res["condition"] else ""))
    b = res["base"]
    print(f"Today: severity {b['severity']}, customer voltage {b['req_v_min']}-{b['req_v_max']} pu, "
          f"transformer {b['max_tx_loading_pct']}%")
    cols = [c for c in ["option", "pass", "severity", "req_v_min", "req_v_max", "max_tx_loading_pct",
                        "max_line_pct", "curtail_pct"] if c in res["tested"]]
    print(res["tested"][cols].to_string(index=False))
    print(f"Saved results/{a.feeder}/connection_checks/{stem}.md")


if __name__ == "__main__":
    main()
