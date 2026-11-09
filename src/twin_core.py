"""Phase 2: digital twin core - asset database and network topology."""
import sys
from pathlib import Path
import opendssdirect as dss
import networkx as nx
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
FEEDER = sys.argv[1] if len(sys.argv) > 1 else "european_lv"
MASTER = ROOT / "opendss" / FEEDER / "Master.dss"
ASSETS = ROOT / "data" / "assets" / FEEDER
MODELS = ROOT / "models" / FEEDER


def bus_name(full):
    return full.split(".")[0].lower()


def load_feeder():
    dss.Text.Command(f'compile "{MASTER}"')
    dss.Text.Command("set mode=snapshot")
    dss.Text.Command("solve")
    if not dss.Solution.Converged():
        raise RuntimeError("Power flow did not converge")


def get_buses():
    rows = []
    for name in dss.Circuit.AllBusNames():
        dss.Circuit.SetActiveBus(name)
        mags = dss.Bus.puVmagAngle()[0::2]
        rows.append({"bus": name.lower(), "x": dss.Bus.X(), "y": dss.Bus.Y(),
                     "kv_base": dss.Bus.kVBase(),
                     "v_min_pu": min(mags), "v_max_pu": max(mags)})
    return pd.DataFrame(rows)


def get_lines():
    rows = []
    i = dss.Lines.First()
    while i > 0:
        rows.append({"line": dss.Lines.Name(), "bus1": bus_name(dss.Lines.Bus1()),
                     "bus2": bus_name(dss.Lines.Bus2()), "length": dss.Lines.Length(),
                     "linecode": dss.Lines.LineCode(), "phases": dss.Lines.Phases(),
                     "norm_amps": dss.Lines.NormAmps()})
        i = dss.Lines.Next()
    return pd.DataFrame(rows)


def get_transformers():
    rows = []
    i = dss.Transformers.First()
    while i > 0:
        buses = dss.CktElement.BusNames()
        dss.Transformers.Wdg(1)
        kva, kv_hv = dss.Transformers.kVA(), dss.Transformers.kV()
        dss.Transformers.Wdg(2)
        kv_lv = dss.Transformers.kV()
        rows.append({"transformer": dss.Transformers.Name(),
                     "hv_bus": bus_name(buses[0]), "lv_bus": bus_name(buses[1]),
                     "kva": kva, "kv_hv": kv_hv, "kv_lv": kv_lv})
        i = dss.Transformers.Next()
    return pd.DataFrame(rows)


def get_loads():
    rows = []
    i = dss.Loads.First()
    while i > 0:
        full = dss.CktElement.BusNames()[0]
        parts = full.split(".")
        phase = {"1": "A", "2": "B", "3": "C"}.get(parts[1], "?") if len(parts) > 1 else "3ph"
        rows.append({"load": dss.Loads.Name(), "bus": bus_name(full), "phase": phase,
                     "kw": dss.Loads.kW(), "kv": dss.Loads.kV(), "pf": dss.Loads.PF(),
                     "daily_shape": dss.Loads.Daily()})
        i = dss.Loads.Next()
    return pd.DataFrame(rows)


def build_graph(buses, lines, transformers):
    G = nx.Graph()
    for r in buses.itertuples(index=False):
        G.add_node(r.bus, x=r.x, y=r.y, kv_base=r.kv_base, v_min_pu=r.v_min_pu)
    for r in lines.itertuples(index=False):
        G.add_edge(r.bus1, r.bus2, kind="line", name=r.line, length=r.length)
    for r in transformers.itertuples(index=False):
        G.add_edge(r.hv_bus, r.lv_bus, kind="transformer", name=r.transformer, length=0.0)
    return G


def main():
    load_feeder()
    buses, lines, tx, loads = get_buses(), get_lines(), get_transformers(), get_loads()
    G = build_graph(buses, lines, tx)

    lv_bus, hv_bus = tx.loc[0, "lv_bus"], tx.loc[0, "hv_bus"]
    dist = nx.single_source_dijkstra_path_length(G, lv_bus, weight="length")

    trunk = G.copy()
    trunk.remove_nodes_from([lv_bus, hv_bus])
    feeder_of = {}
    comps = sorted(nx.connected_components(trunk), key=len, reverse=True)
    for k, comp in enumerate(comps, start=1):
        for b in comp:
            feeder_of[b] = f"F{k}"
    nx.set_node_attributes(G, feeder_of, "feeder")

    loads.insert(0, "consumer_id", [f"C{n:03d}" for n in range(1, len(loads) + 1)])
    loads["feeder"] = loads["bus"].map(feeder_of)
    loads["distance_m"] = loads["bus"].map(dist).round(1)
    buses["feeder"] = buses["bus"].map(feeder_of)
    buses["distance_m"] = buses["bus"].map(dist).round(1)

    ASSETS.mkdir(parents=True, exist_ok=True)
    MODELS.mkdir(parents=True, exist_ok=True)
    buses.to_csv(ASSETS / "buses.csv", index=False)
    lines.to_csv(ASSETS / "lines.csv", index=False)
    tx.to_csv(ASSETS / "transformers.csv", index=False)
    loads.to_csv(ASSETS / "consumers.csv", index=False)
    nx.write_graphml(G, MODELS / "network.graphml")

    print(f"=== {FEEDER} ===")
    print(f"Nodes: {G.number_of_nodes()}  Edges: {G.number_of_edges()}")
    print("Connected:", nx.is_connected(G), "| Radial (tree):", nx.is_tree(G))
    print(f"Transformer: {tx.loc[0, 'transformer']} {tx.loc[0, 'kva']:.0f} kVA, "
          f"{tx.loc[0, 'kv_hv']} / {tx.loc[0, 'kv_lv']} kV")
    print("Feeders leaving the transformer:", len(comps))
    print("Customers per phase:", loads["phase"].value_counts().to_dict())
    print("Customers per feeder:", loads["feeder"].value_counts().sort_index().to_dict())
    far = loads.loc[loads["distance_m"].idxmax()]
    print(f"Furthest customer: {far['consumer_id']} ({far['load']}) at {far['distance_m']} m")
    low = buses.loc[buses["v_min_pu"].idxmin()]
    print(f"Lowest voltage: bus {low['bus']} at {low['v_min_pu']:.4f} pu")
    print(f"Saved: data/assets/{FEEDER}/*.csv and models/{FEEDER}/network.graphml")


if __name__ == "__main__":
    main()
