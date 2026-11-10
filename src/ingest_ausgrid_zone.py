"""Load, clean and quality-check Ausgrid zone substation 15-minute demand data.

Source: Ausgrid, Distribution Zone Substation Data (FY2025, FY26), from the
Research data sets page on ausgrid.com.au. Raw SCADA data, Ausgrid copyright:
not redistributed. Values are MW of NET demand; negative values mean reverse
power flow (e.g. rooftop solar export) and are kept. Times are local (AEST/AEDT)
and mark the END of each 15-minute interval. Raw column order is not
chronological (HH:00 follows HH:45), so columns are sorted by time label.

Cleaning (values set to missing, never interpolated here):
- held values: runs of >= FLAT_STEPS identical readings
- spikes: a reading that jumps away from BOTH neighbours in opposite directions
  by more than SPIKE_K x the substation's typical 15-minute change
Dataset-wide gaps (missing for every substation) are reported separately.
Sustained switching (level shifts) is not removed.
"""
from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw" / "ausgrid_zone"
PROC = ROOT / "data" / "processed"
META = ["year", "Zone Substation", "Date", "Unit"]
FLAT_STEPS = 4            # 1 h of identical readings = held value
SPIKE_K = 8.0             # x typical 15-minute change
MIN_SCALE_MW = 0.001      # floor for the typical-change scale
MAX_OWN_MISSING_PCT = 2.0
MAX_SPIKE_PCT = 0.1
CANDIDATES = ["City North 132_11kV", "Kogarah 132_11kV", "Maryland 132_11kV"]


def minutes(label):
    h, m = label.split(":")
    return int(h) * 60 + int(m)


def read_file(path):
    df = pd.read_csv(path)
    tcols = sorted([c for c in df.columns if c not in META], key=minutes)
    if len(tcols) != 96:
        raise ValueError(f"{path.name}: expected 96 time columns, got {len(tcols)}")
    offsets = pd.to_timedelta([minutes(c) for c in tcols], unit="m").to_numpy()
    dates = pd.to_datetime(df["Date"]).to_numpy()
    ts = (dates[:, None] + offsets[None, :]).ravel()
    vals = df[tcols].apply(pd.to_numeric, errors="coerce").to_numpy().ravel()
    return (str(df["Zone Substation"].iloc[0]), set(df["Unit"].astype(str).unique()),
            pd.Series(vals, index=pd.DatetimeIndex(ts)))


def clean(x):
    x = x.copy()
    same = x.diff().eq(0)
    grp = (~same).cumsum()
    repeats = same.groupby(grp).transform("sum")
    held = repeats >= FLAT_STEPS - 1
    x[held] = np.nan
    d = x.diff()
    scale = max(1.4826 * np.nanmedian(np.abs(d - np.nanmedian(d))), MIN_SCALE_MW)
    up, down = x - x.shift(1), x.shift(-1) - x
    spike = (up.abs() > SPIKE_K * scale) & (down.abs() > SPIKE_K * scale) & (np.sign(up) != np.sign(down))
    x[spike] = np.nan
    return x, int(held.sum()), int(spike.sum()), scale


def main():
    files = sorted(RAW.rglob("*.csv"))
    print(f"Reading {len(files)} files ...")
    series, units = {}, {}
    for f in files:
        name, unit, s = read_file(f)
        series.setdefault(name, []).append(s)
        units.setdefault(name, set()).update(unit)

    cols = {}
    for name, parts in series.items():
        s = pd.concat(parts).sort_index()
        cols[name] = s[~s.index.duplicated(keep="first")]
    raw = pd.DataFrame(cols)
    raw = raw.reindex(pd.date_range(raw.index.min(), raw.index.max(), freq="15min"))
    raw.index.name = "timestamp"
    common_gap = raw.isna().all(axis=1)

    cleaned, rows = {}, []
    for n in raw.columns:
        x, n_held, n_spike, scale = clean(raw[n])
        cleaned[n] = x
        valid = x.dropna()
        rows.append({"substation": n, "unit": ",".join(sorted(units[n])),
                     "pct_missing_raw": round(100 * raw[n].isna().mean(), 2),
                     "pct_missing_own": round(100 * (x.isna() & ~common_gap).mean(), 2),
                     "held_intervals": n_held, "spikes_removed": n_spike,
                     "typical_step_mw": round(scale, 4),
                     "pct_negative": round(100 * (valid < 0).mean(), 2),
                     "min_mw": round(valid.min(), 3), "mean_mw": round(valid.mean(), 3),
                     "peak_mw": round(valid.max(), 3)})
    wide = pd.DataFrame(cleaned, index=raw.index)
    q = pd.DataFrame(rows).sort_values("peak_mw", ascending=False)
    q["usable"] = ((q["pct_missing_own"] < MAX_OWN_MISSING_PCT)
                   & (100 * q["spikes_removed"] / len(wide) <= MAX_SPIKE_PCT))

    PROC.mkdir(parents=True, exist_ok=True)
    wide.to_parquet(PROC / "ausgrid_zone_15min.parquet")
    q.to_csv(PROC / "ausgrid_zone_quality.csv", index=False)
    gaps = pd.Series(raw.index[common_gap]).dt.normalize().value_counts().sort_index()
    gaps.rename("intervals_missing").to_csv(PROC / "ausgrid_zone_dataset_gaps.csv")

    print(f"Substations: {wide.shape[1]} | intervals: {len(wide)} ({wide.index.min()} to {wide.index.max()})")
    print(f"Dataset-wide gaps (missing for every substation): {int(common_gap.sum())} intervals "
          f"({100 * common_gap.mean():.2f}%) on {len(gaps)} days")
    print(f"Held values removed: {int(q['held_intervals'].sum())} intervals across "
          f"{int((q['held_intervals'] > 0).sum())} substations")
    print(f"Spikes removed: {int(q['spikes_removed'].sum())} across "
          f"{int((q['spikes_removed'] > 0).sum())} substations")
    print(f"Usable after screening: {int(q['usable'].sum())} of {len(q)}")
    show = ["substation", "pct_missing_raw", "pct_missing_own", "held_intervals",
            "spikes_removed", "pct_negative", "min_mw", "mean_mw", "peak_mw", "usable"]
    print("\nLargest 10 by peak demand:")
    print(q.head(10)[show].to_string(index=False))
    print("\nMost reverse power flow:")
    print(q.sort_values("pct_negative", ascending=False).head(5)[show].to_string(index=False))
    print("\nMost spikes removed:")
    print(q.sort_values("spikes_removed", ascending=False).head(5)[show].to_string(index=False))
    print("\nForecasting candidates:")
    print(q[q["substation"].isin(CANDIDATES)][show].to_string(index=False))
    print("\nSaved: data/processed/ausgrid_zone_15min.parquet, ausgrid_zone_quality.csv, "
          "ausgrid_zone_dataset_gaps.csv")


if __name__ == "__main__":
    main()
