"""Diagnose patterns in the Ausgrid zone substation data (missing data, flat runs, steps)."""
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
w = pd.read_parquet(ROOT / "data" / "processed" / "ausgrid_zone_15min.parquet")

all_missing = w.isna().all(axis=1)
print(f"Intervals missing for ALL substations: {int(all_missing.sum())} of {len(w)}")
share = w.isna().mean(axis=1)
bad_days = share[share > 0.5].index.normalize().value_counts().sort_index()
print(f"Days where more than half the substations are missing data: {len(bad_days)}")
print(bad_days.head(40).to_string())

starts, lengths = [], []
for n in w.columns:
    x = w[n].dropna()
    same = x.diff().eq(0)
    grp = (~same).cumsum()
    runlen = same.groupby(grp).transform("sum")
    if runlen.max() == 0:
        continue
    g = grp[runlen.idxmax()]
    starts.append(x.index[grp == g][0])
    lengths.append(int(runlen.max()))
s = pd.Series(lengths, index=starts)
print("\nMost common start times of each substation's longest flat run:")
print(pd.Series(s.index).value_counts().head(8).to_string())

for name in ["Meadowbank 132_11kV", "Medowie 33_11kV", "Kogarah 132_11kV"]:
    if name not in w.columns:
        continue
    x = w[name].dropna()
    print(f"\n{name}: {100 * (x.diff() == 0).mean():.1f}% of steps unchanged, "
          f"{x.nunique()} distinct values, smallest step {x.diff().abs()[x.diff() != 0].min():.6f} MW")
    print(x.loc["2025-06-03"].head(12).round(4).to_string())
