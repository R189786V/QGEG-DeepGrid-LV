import sys
import opendssdirect as dss
from pathlib import Path

feeder = sys.argv[1] if len(sys.argv) > 1 else "european_lv"
master = Path(__file__).resolve().parents[1] / "opendss" / feeder / "Master.dss"
dss.Text.Command(f'compile "{master}"')
dss.Text.Command("set mode=snapshot")
dss.Text.Command("solve")

print("Feeder:", feeder, "| Circuit:", dss.Circuit.Name())
print("Converged:", dss.Solution.Converged())
print("Buses:", dss.Circuit.NumBuses(), "| Lines:", dss.Lines.Count(),
      "| Transformers:", dss.Transformers.Count(), "| Loads:", dss.Loads.Count())
v = [x for x in dss.Circuit.AllBusMagPu() if x > 0]
print(f"Voltage range: {min(v):.4f} to {max(v):.4f} pu")
p, q = dss.Circuit.TotalPower()
print(f"Total demand: {-p:.1f} kW, {-q:.1f} kvar")

dss.Transformers.First()
name = dss.Transformers.Name()
kva = dss.Transformers.kVA()
dss.Circuit.SetActiveElement(f"Transformer.{name}")
n = dss.CktElement.NumConductors()
pw = dss.CktElement.Powers()[: 2 * n]
s = (sum(pw[0::2]) ** 2 + sum(pw[1::2]) ** 2) ** 0.5
print(f"Transformer {name}: {s:.1f} kVA of {kva:.0f} kVA ({100 * s / kva:.0f}% loaded)")
