"""Download hourly weather for the forecasting substations from Open-Meteo.

Source: Open-Meteo Historical Weather API (ERA5-based reanalysis), CC BY 4.0.
This is reconstructed observed weather, not a forecast. Using it in a day-ahead
model assumes a perfect weather forecast, so results are optimistic.
Coordinates are approximate substation areas.
"""
import json
import urllib.parse
import urllib.request
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "processed" / "weather"
SITES = {"City North 132_11kV": (-33.865, 151.207),
         "Kogarah 132_11kV": (-33.963, 151.133),
         "Branxton 66_11kV": (-32.656, 151.349)}
START, END = "2024-04-30", "2026-05-01"
VARS = "temperature_2m,relative_humidity_2m,shortwave_radiation,cloud_cover"


def fetch(lat, lon):
    q = urllib.parse.urlencode({"latitude": lat, "longitude": lon, "start_date": START,
                                "end_date": END, "hourly": VARS, "timezone": "Australia/Sydney"})
    url = f"https://archive-api.open-meteo.com/v1/archive?{q}"
    with urllib.request.urlopen(url, timeout=120) as r:
        data = json.load(r)
    df = pd.DataFrame(data["hourly"])
    df["time"] = pd.to_datetime(df["time"])
    df = df.set_index("time")
    return df[~df.index.duplicated(keep="first")]


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    for name, (lat, lon) in SITES.items():
        print(f"Downloading weather for {name} ({lat}, {lon}) ...")
        df = fetch(lat, lon)
        safe = name.split(" ")[0].lower() + ("_" + name.split(" ")[1].lower() if name.split(" ")[1][0].isalpha() else "")
        df.to_parquet(OUT / f"{safe}.parquet")
        print(f"  {len(df)} hours, {df.index.min()} to {df.index.max()}, "
              f"missing {100 * df.isna().mean().mean():.2f}%")
        print(f"  temperature {df['temperature_2m'].min():.1f} to {df['temperature_2m'].max():.1f} C, "
              f"max radiation {df['shortwave_radiation'].max():.0f} W/m2 -> saved {safe}.parquet")


if __name__ == "__main__":
    main()
