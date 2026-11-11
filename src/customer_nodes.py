"""Customer node layer: each customer is a node with a meter profile, DER components,
a node controller and a net meter. The net meter profile drives the OpenDSS network model.

Customer node   = one customer connection
Meter profile   = the customer's own demand (from the network model's load profile)
DER components  = rooftop PV, home battery, EV charger, heat-pump water heater
Node controller = "none" (devices uncontrolled), "smart" (home-optimised) or
                  "network" (home + network-aware: battery keeps capacity for the midday export peak,
                  EV charging finishes before the morning peak)
Net meter       = demand + EV + heat pump - PV + battery charge - battery discharge (kW, 15 min)
A cross-layer balance check compares the transformer import with the sum of net meters + losses.
All DER is hypothetical and simulated.
Usage: python src/customer_nodes.py zimbabwe_urban pv=0.5 ev=0.3 hp=0.4 battery=0.5 control=all
"""
import sys
import random
from dataclasses import dataclass, field
from pathlib import Path
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import opendssdirect as dss

sys.path.insert(0, str(Path(__file__).resolve().parent))
from agents import SHAPES, V_LOW, V_HIGH, PHASE_NODE  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
STEPS, DT = 96, 0.25
HOURS = np.arange(STEPS) * DT
SOLAR = np.array(SHAPES["der_solar"], dtype=float)
PHASE_LIST = {"A": [".1"], "B": [".2"], "C": [".3"], "3ph": [".1", ".2", ".3"]}
EXPORT_THRESHOLD_KW = 1.5   # network mode: battery charges only from export above this


def window(start, end):
    if start <= end:
        return ((HOURS >= start) & (HOURS < end)).astype(float)
    return ((HOURS >= start) | (HOURS < end)).astype(float)


@dataclass
class PV:
    kw: float = 5.0

    def output(self):
        return self.kw * SOLAR


@dataclass
class EVCharger:
    kw: float = 7.0
    kwh: float = 14.0

    def profile(self, mode):
        win = {"uncontrolled": window(18, 20), "managed": window(23, 5), "slow": window(21, 7)}[mode]
        return win * self.kwh / (win.sum() * DT)


@dataclass
class HeatPump:
    kw: float = 1.2
    hours: float = 3.0

    def profile(self, start):
        return window(start, (start + self.hours) % 24) * self.kw


@dataclass
class Battery:
    kw: float = 5.0
    kwh: float = 10.0
    eff: float = 0.92
    reserve: float = 0.10
    soc0: float = 0.10

    def dispatch(self, surplus, threshold=0.0):
        soc = self.soc0 * self.kwh
        ch, dis, socs = np.zeros(STEPS), np.zeros(STEPS), np.zeros(STEPS)
        for t, s in enumerate(surplus):
            if s > threshold:
                p = min(s - threshold, self.kw, max(0.0, self.kwh - soc) / (self.eff * DT))
                ch[t], soc = p, soc + p * self.eff * DT
            elif s < 0:
                p = min(-s, self.kw, max(0.0, soc - self.reserve * self.kwh) * self.eff / DT)
                dis[t], soc = p, soc - p / self.eff * DT
            socs[t] = soc / self.kwh
        return ch, dis, socs


@dataclass
class NodeController:
    mode: str = "none"

    def ev_mode(self):
        return {"none": "uncontrolled", "smart": "slow", "network": "managed"}[self.mode]

    def hp_start(self, has_pv):
        return 17 if self.mode == "none" else (10 if has_pv else 23)

    def charge_threshold(self):
        return EXPORT_THRESHOLD_KW if self.mode == "network" else 0.0


@dataclass
class CustomerNode:
    cid: str
    load: str
    bus: str
    phase: str
    meter: np.ndarray
    pv: PV | None = None
    battery: Battery | None = None
    ev: EVCharger | None = None
    hp: HeatPump | None = None
    controller: NodeController = field(default_factory=NodeController)

    def net_meter(self):
        z = np.zeros(STEPS)
        ev = self.ev.profile(self.controller.ev_mode()) if self.ev else z
        hp = self.hp.profile(self.controller.hp_start(self.pv is not None)) if self.hp else z
        pv = self.pv.output() if self.pv else z
        demand = self.meter + ev + hp
        ch, dis, soc = (self.battery.dispatch(pv - demand, self.controller.charge_threshold())
                        if self.battery else (z, z, z))
        return {"meter": self.meter, "ev": ev, "hp": hp, "pv": pv, "charge": ch, "discharge": dis,
                "soc": soc, "net": demand - pv + ch - dis}


def meter_profile(load):
    dss.Loads.Name(load)
    kw, shp = dss.Loads.kW(), dss.Loads.Daily() or dss.Loads.Yearly()
    if not shp:
        return np.full(STEPS, kw)
    dss.LoadShape.Name(shp)
    mult, interval = list(dss.LoadShape.PMult()), dss.LoadShape.HrInterval()
    return np.array([kw * mult[int(t * DT / interval) % len(mult)] for t in range(STEPS)])


def build_nodes(feeder, p, cons, control):
    dss.Text.Command(f'compile "{ROOT / "opendss" / feeder / "Master.dss"}"')
    one = cons[cons["phase"].isin(["A", "B", "C"])].reset_index(drop=True)
    ids = one["consumer_id"].tolist()
    orders = {}
    for name, seed in [("pv", 1), ("ev", 2), ("hp", 3), ("bat", 4)]:
        o = ids[:]
        random.Random(seed).shuffle(o)
        orders[name] = o
    pv = set(orders["pv"][:round(p["pv"] * len(ids))])
    ev = set(orders["ev"][:round(p["ev"] * len(ids))])
    hp = set(orders["hp"][:round(p["hp"] * len(ids))])
    pv_homes = [c for c in orders["bat"] if c in pv]
    bat = set(pv_homes[:round(p["battery"] * len(pv_homes))])
    ctrl = NodeController(control)
    return [CustomerNode(r.consumer_id, r.load, r.bus, r.phase, meter_profile(r.load),
                         PV() if r.consumer_id in pv else None, Battery() if r.consumer_id in bat else None,
                         EVCharger() if r.consumer_id in ev else None, HeatPump() if r.consumer_id in hp else None,
                         ctrl) for r in one.itertuples(index=False)]


def simulate(feeder, nodes, cons, with_der=True):
    """Python is the single timekeeper: every load and generator is set at each 15-minute step,
    then OpenDSS solves that moment (snapshot). Loads are held at constant power."""
    dss.Text.Command(f'compile "{ROOT / "opendss" / feeder / "Master.dss"}"')
    by_load = {nd.load: nd for nd in nodes}
    consumption, generation = {}, {}
    for ld in cons["load"]:
        nd = by_load.get(ld)
        if nd is None:
            consumption[ld] = meter_profile(ld)
            continue
        r = nd.net_meter()
        if with_der:
            consumption[ld] = r["meter"] + r["ev"] + r["hp"] + r["charge"]
            g = r["pv"] + r["discharge"]
            if g.max() > 1e-9:
                generation[f"g_{nd.cid}".lower()] = (g, nd)
        else:
            consumption[ld] = r["meter"]
    for ld in consumption:
        dss.Text.Command(f"edit Load.{ld} vminpu=0.5 vmaxpu=1.5")
    for gname, (g, nd) in generation.items():
        dss.Text.Command(f"New Generator.{gname} phases=1 bus1={nd.bus}{PHASE_NODE[nd.phase]} kV=0.23 kW=0 "
                         f"pf=1 model=1 vminpu=0.5 vmaxpu=1.5")
    dss.Text.Command("set maxiterations=100")
    dss.Text.Command("set mode=snapshot")
    idx = {n.lower(): i for i, n in enumerate(dss.Circuit.AllNodeNames())}
    cnodes = [[idx[f"{b}{s}"] for s in PHASE_LIST[ph] if f"{b}{s}" in idx]
              for b, ph in zip(cons["bus"], cons["phase"])]
    rows, conv = [], True
    cmin, cmax = np.full(len(cnodes), np.inf), np.full(len(cnodes), -np.inf)
    for t in range(STEPS):
        for ld, c in consumption.items():
            dss.Loads.Name(ld)
            dss.Loads.kW(float(c[t]))
        for gname, (g, nd) in generation.items():
            dss.Generators.Name(gname)
            dss.Generators.kW(float(g[t]))
        dss.Solution.Solve()
        conv = conv and dss.Solution.Converged()
        v = np.array(dss.Circuit.AllBusMagPu())
        lo = np.array([v[ix].min() for ix in cnodes])
        hi = np.array([v[ix].max() for ix in cnodes])
        cmin, cmax = np.minimum(cmin, lo), np.maximum(cmax, hi)
        dss.Transformers.First()
        kva = dss.Transformers.kVA()
        dss.Circuit.SetActiveElement(f"Transformer.{dss.Transformers.Name()}")
        n = dss.CktElement.NumConductors()
        pw = dss.CktElement.Powers()[: 2 * n]
        pp, qq = sum(pw[0::2]), sum(pw[1::2])
        mx = 0.0
        i = dss.Lines.First()
        while i > 0:
            na = dss.Lines.NormAmps()
            if na > 0:
                nph = dss.CktElement.NumPhases()
                mx = max(mx, 100 * max(dss.CktElement.CurrentsMagAng()[0:2 * nph:2]) / na)
            i = dss.Lines.Next()
        rows.append({"v_min": lo.min(), "v_max": hi.max(), "tx_pct": 100 * (pp * pp + qq * qq) ** 0.5 / kva,
                     "tx_kw": pp, "line_pct": mx, "loss_kw": dss.Circuit.Losses()[0] / 1000})
    df = pd.DataFrame(rows)
    sev = DT * (((V_LOW - df["v_min"]).clip(lower=0) * 100).sum() + ((df["v_max"] - V_HIGH).clip(lower=0) * 100).sum()
                + (df["tx_pct"] - 100).clip(lower=0).sum() + (df["line_pct"] - 100).clip(lower=0).sum())
    expected = sum(consumption.values()) - sum((g for g, _ in generation.values()), np.zeros(STEPS))
    df["expected_kw"] = expected
    m = {"converged": conv, "severity": round(float(sev), 2),
         "customers_out_of_limits": int(((cmin < V_LOW) | (cmax > V_HIGH)).sum()),
         "customers_undervoltage": int((cmin < V_LOW).sum()), "customers_overvoltage": int((cmax > V_HIGH).sum()),
         "v_min_pu": round(float(df["v_min"].min()), 4), "v_max_pu": round(float(df["v_max"].max()), 4),
         "tx_peak_pct": round(float(df["tx_pct"].max()), 1),
         "max_import_kw": round(float(df["tx_kw"].max()), 1),
         "max_export_kw": round(float(max(0.0, -df["tx_kw"].min())), 1),
         "max_line_pct": round(float(df["line_pct"].max()), 1),
         "losses_kwh": round(float(df["loss_kw"].sum() * DT), 1)}
    return m, df


def layer_check(nodes, cons, ts):
    """Transformer import minus losses must equal the sum of all loads minus all generation, every step."""
    d = np.abs(ts["tx_kw"].values - ts["loss_kw"].values - ts["expected_kw"].values)
    return round(float(d.max()), 3), "same step", round(float(d.mean()), 3)



def main():
    feeder = sys.argv[1] if len(sys.argv) > 1 else "zimbabwe_urban"
    p = {"pv": 0.5, "ev": 0.3, "hp": 0.4, "battery": 0.5, "control": "all"}
    for a in sys.argv[2:]:
        k, v = a.split("=", 1)
        p[k] = v if k == "control" else float(v)
    controls = ["none", "smart", "network"] if p["control"] == "all" else [p["control"]]
    cons = pd.read_csv(ROOT / "data" / "assets" / feeder / "consumers.csv", dtype={"bus": str, "load": str})
    out = ROOT / "results" / feeder / "customer_nodes"
    out.mkdir(parents=True, exist_ok=True)

    base_nodes = build_nodes(feeder, p, cons, "none")
    base_m, _ = simulate(feeder, base_nodes, cons, with_der=False)
    cols, totals, example = {"today (no new DER)": base_m}, {}, None
    print(f"=== {feeder}: customer node layer | PV {p['pv']:.0%}, EV {p['ev']:.0%}, heat pump {p['hp']:.0%}, "
          f"batteries on {p['battery']:.0%} of PV homes ===")
    for ctrl in controls:
        nodes = build_nodes(feeder, p, cons, ctrl)
        m, ts = simulate(feeder, nodes, cons, with_der=True)
        mismatch, alignment, mismatch_same = layer_check(nodes, cons, ts)
        rows = []
        for nd in nodes:
            r = nd.net_meter()
            pv_kwh = r["pv"].sum() * DT
            exp = np.clip(-r["net"], 0, None).sum() * DT
            rows.append({"customer": nd.cid, "pv": nd.pv is not None, "battery": nd.battery is not None,
                         "ev": nd.ev is not None, "heat_pump": nd.hp is not None,
                         "import_kwh": round(float(np.clip(r["net"], 0, None).sum() * DT), 2),
                         "export_kwh": round(float(exp), 2),
                         "peak_import_kw": round(float(r["net"].max()), 2),
                         "peak_export_kw": round(float(max(0, -r["net"].min())), 2),
                         "solar_used_on_site_pct": round(float(100 * (pv_kwh - exp) / pv_kwh), 1) if pv_kwh > 0 else np.nan})
        table = pd.DataFrame(rows)
        table.to_csv(out / f"node_summary_{ctrl}.csv", index=False)
        m["solar_used_on_site_pct"] = round(float(table["solar_used_on_site_pct"].mean()), 1)
        m["layer_check_max_kw"] = mismatch
        m["layer_alignment"] = alignment
        m["layer_check_mean_kw"] = mismatch_same
        cols[f"controller: {ctrl}"] = m
        totals[ctrl] = sum(nd.net_meter()["net"] for nd in nodes)
        if example is None:
            score = table[["pv", "battery", "ev", "heat_pump"]].sum(axis=1)
            example = table.loc[score.idxmax(), "customer"]
        if ctrl == controls[-1]:
            ex_node = next(nd for nd in nodes if nd.cid == example)
            ex_r, ex_ctrl = ex_node.net_meter(), ctrl
        if ctrl == "none":
            print(f"Nodes: {len(nodes)} | PV {int(table['pv'].sum())} | batteries {int(table['battery'].sum())} | "
                  f"EVs {int(table['ev'].sum())} | heat pumps {int(table['heat_pump'].sum())}")

    comp = pd.DataFrame(cols)
    comp.to_csv(out / "network_comparison.csv")
    print("\nNetwork result (OpenDSS, driven by each node's net meter):")
    print(comp.to_string())

    r = ex_r
    fig, ax = plt.subplots(2, 1, figsize=(11, 8), sharex=True)
    ax[0].stackplot(HOURS, r["meter"], r["ev"], r["hp"], r["charge"],
                    labels=["Meter profile (demand)", "EV charging", "Heat pump", "Battery charging"],
                    colors=["#9aa5b1", "#1c1f3e", "#fab724", "#2e7d32"], alpha=0.85)
    ax[0].stackplot(HOURS, -r["pv"], -r["discharge"], labels=["Rooftop PV", "Battery discharging"],
                    colors=["#f7d774", "#81c784"], alpha=0.85)
    ax[0].plot(HOURS, r["net"], color="black", linewidth=2, label="Net meter")
    ax[0].axhline(0, color="grey", linewidth=0.8)
    ax[0].set_ylabel("kW (positive = from grid)")
    ax[0].set_title(f"Customer node {example}: components and net meter (controller '{ex_ctrl}')")
    ax[0].legend(fontsize=8, ncol=3, loc="upper left")
    ax[1].plot(HOURS, sum(nd.meter for nd in base_nodes), label="Demand only (today)", color="#9aa5b1", linewidth=2)
    for ctrl, col in zip(controls, ["#c62828", "#fab724", "#1c1f3e"]):
        ax[1].plot(HOURS, totals[ctrl], label=f"Net meters, controller '{ctrl}'", color=col, linewidth=2)
    ax[1].axhline(0, color="grey", linewidth=0.8)
    ax[1].set_ylabel("All customers (kW)")
    ax[1].set_xlabel("Hour of day")
    ax[1].legend(fontsize=9)
    fig.tight_layout()
    fig.savefig(out / "example_node.png", dpi=150)
    print(f"\nExample node: {example}. Saved results/{feeder}/customer_nodes/")


if __name__ == "__main__":
    main()
