"""Check a substation for sustained level changes (e.g. load transfers from switching)."""
import sys
from pathlib import Path
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parents[1]
names = sys.argv[1:] or ["Kogarah 132_11kV", "City North 132_11kV"]
w = pd.read_parquet(ROOT / "data" / "processed" / "ausgrid_zone_15min.parquet", columns=names)

for n in names:
    m = w[n].resample("MS").agg(["mean", "max", "min"]).round(1)
    m.index = m.index.strftime("%Y-%m")
    print(f"\n=== {n}: monthly mean / max / min (MW) ===")
    print(m.to_string())
    d = w[n].resample("D").mean()
    jumps = d.diff().abs().sort_values(ascending=False).head(6)
    print("Largest day-to-day changes in daily mean (MW):")
    print(jumps.round(1).to_string())

fig, ax = plt.subplots(figsize=(12, 5))
for n in names:
    ax.plot(w[n].resample("D").mean(), label=n)
ax.axvline(pd.Timestamp("2025-11-01"), color="red", linestyle="--", label="Test period starts")
ax.set_ylabel("Daily mean demand (MW)")
ax.legend()
fig.tight_layout()
out = ROOT / "results" / "forecasting" / "level_check.png"
fig.savefig(out, dpi=150)
print(f"\nSaved {out}")
