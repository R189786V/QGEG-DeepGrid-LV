"""Diagnose the cross-layer mismatch: transformer import vs sum of customer meter profiles (no DER)."""
import sys
from pathlib import Path
import numpy as np
import pandas as pd
import opendssdirect as dss

sys.path.insert(0, str(Path(__file__).resolve().parent))
from customer_nodes import build_nodes, simulate, meter_profile, ROOT, HOURS  # noqa: E402

feeder = sys.argv[1] if len(sys.argv) > 1 else "zimbabwe_urban"
cons = pd.read_csv(ROOT / "data" / "assets" / feeder / "consumers.csv", dtype={"bus": str, "load": str})
nodes = build_nodes(feeder, {"pv": 0, "ev": 0, "hp": 0, "battery": 0}, cons, "none")
m, ts = simulate(feeder, nodes, cons, with_der=False)

names = []
i = dss.Loads.First()
while i > 0:
    names.append(dss.Loads.Name().lower())
    i = dss.Loads.Next()
listed = set(cons["load"].str.lower())
print(f"Consumers listed: {len(cons)} | nodes: {len(nodes)} | OpenDSS loads: {len(names)}")
print("OpenDSS loads not in the customer list:", sorted(set(names) - listed)[:20])
print("Customer list entries not in OpenDSS:", sorted(listed - set(names))[:20])

expected = sum(meter_profile(ld) for ld in cons["load"])
measured = ts["tx_kw"].values - ts["loss_kw"].values
d = measured - expected
k = int(np.abs(d).argmax())
print(f"\nMax mismatch {d[k]:+.2f} kW at step {k} (hour {HOURS[k]:.2f}): measured {measured[k]:.1f}, expected {expected[k]:.1f}")
print(f"Mean mismatch {d.mean():+.2f} kW | steps with |mismatch| > 5 kW: {(np.abs(d) > 5).sum()} of {len(d)}")
for s in [0, 1, 2, 3, 4]:
    print(f"  shift expected by {s} step(s): max mismatch {np.abs(measured - np.roll(expected, -s)).max():.2f} kW")
print("\nSteps around the worst one:")
print(pd.DataFrame({"hour": HOURS, "measured_kw": measured.round(1), "expected_kw": expected.round(1),
                    "diff_kw": d.round(1)}).iloc[max(0, k - 5):k + 6].to_string(index=False))
