"""Phase 9: day-ahead forecasting of zone substation net demand (real Ausgrid data).

Forecast issued at midnight for the next day's 96 x 15-minute intervals.
Features available at issue time: demand 1 day and 7 days earlier, previous-day
mean, calendar, NSW public holidays, and weather for the target time.
Weather is reanalysis (actual), so the with-weather models assume a PERFECT weather
forecast and are optimistic; the no-weather model shows the floor.
Level-adjusted model: predicts demand relative to the previous day's mean, so a
sustained level change (e.g. a load transfer from network switching) is picked up
after one day. Plain tree models cannot predict outside their training range.
Known event: Kogarah load roughly doubles from 21 Mar 2026 (likely a load transfer);
its metrics are also reported before and after that date.
Train: May 2024 - Oct 2025. Test (never seen in training): Nov 2025 - Apr 2026.
"""
from pathlib import Path
import numpy as np
import pandas as pd
import holidays
import xgboost as xgb
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parents[1]
PROC = ROOT / "data" / "processed"
OUT = ROOT / "results" / "forecasting"
SITES = {"City North 132_11kV": "city_north", "Kogarah 132_11kV": "kogarah",
         "Branxton 66_11kV": "branxton"}
TEST_START = pd.Timestamp("2025-11-01 00:15")
EVENTS = {"Kogarah 132_11kV": pd.Timestamp("2026-03-21 00:15")}
PARAMS = dict(n_estimators=600, learning_rate=0.05, max_depth=6, subsample=0.8,
              colsample_bytree=0.8, tree_method="hist", random_state=0)
CAL = ["minute_of_day", "dow", "is_weekend", "is_holiday", "doy_sin", "doy_cos"]
LAGS = ["lag_1d", "lag_7d", "prev_day_mean"]
WX = ["temperature_2m", "relative_humidity_2m", "shortwave_radiation", "cloud_cover", "cdh", "hdh"]
REL = CAL + ["lag_1d_rel", "lag_7d_rel"] + WX
MODELS = {"xgb_no_weather": (CAL + LAGS, False),
          "xgb_weather": (CAL + LAGS + WX, False),
          "xgb_weather_level": (REL, True)}
ORDER = ["naive_1d", "naive_7d", "xgb_no_weather", "xgb_weather", "xgb_weather_level"]


def features(y, wx):
    df = pd.DataFrame({"y": y})
    start = df.index - pd.Timedelta(minutes=15)      # interval start, for calendar
    df["minute_of_day"] = start.hour * 60 + start.minute
    df["dow"] = start.day_of_week
    df["is_weekend"] = (start.day_of_week >= 5).astype(int)
    nsw = holidays.Australia(subdiv="NSW", years=range(2024, 2027))
    df["is_holiday"] = pd.Index(start.date).isin(set(nsw.keys())).astype(int)
    doy = start.day_of_year
    df["doy_sin"] = np.sin(2 * np.pi * doy / 365.25)
    df["doy_cos"] = np.cos(2 * np.pi * doy / 365.25)
    df["lag_1d"] = y.shift(96)
    df["lag_7d"] = y.shift(672)
    day = pd.Series(start.normalize(), index=df.index)
    daily = df["y"].groupby(day.values).mean()
    df["prev_day_mean"] = day.map(daily.shift(1))
    df["lag_1d_rel"] = df["lag_1d"] - df["prev_day_mean"]
    df["lag_7d_rel"] = df["lag_7d"] - df["prev_day_mean"]
    w = wx.reindex(wx.index.union(df.index)).interpolate("time").reindex(df.index)
    for c in WX[:4]:
        df[c] = w[c].values
    df["cdh"] = (df["temperature_2m"] - 22).clip(lower=0)
    df["hdh"] = (16 - df["temperature_2m"]).clip(lower=0)
    df["date"] = start.normalize()
    return df


def metrics(a, p, peak, use_mape):
    e = p - a
    mae = float(np.mean(np.abs(e)))
    return {"mae_mw": round(mae, 3), "rmse_mw": round(float(np.sqrt(np.mean(e ** 2))), 3),
            "mae_pct_of_peak": round(100 * mae / peak, 2),
            "mape_pct": round(float(100 * np.mean(np.abs(e) / np.abs(a))), 2) if use_mape else np.nan}


def evaluate(name, preds, dates, period):
    peak = preds["actual"].max()
    use_mape = preds["actual"].min() > 0.1 * preds["actual"].mean()
    daily_peak = preds.groupby(dates).max()
    base_mae = np.mean(np.abs(preds["naive_7d"] - preds["actual"]))
    rows = []
    for m in ORDER:
        r = {"substation": name, "period": period, "model": m,
             **metrics(preds["actual"], preds[m], peak, use_mape),
             "daily_peak_mae_mw": round(float(np.mean(np.abs(daily_peak[m] - daily_peak["actual"]))), 3)}
        r["skill_vs_naive_7d_pct"] = round(100 * (1 - r["mae_mw"] / base_mae), 1)
        rows.append(r)
    return rows


def main():
    w = pd.read_parquet(PROC / "ausgrid_zone_15min.parquet", columns=list(SITES))
    all_rows = []
    for name, key in SITES.items():
        wx = pd.read_parquet(PROC / "weather" / f"{key}.parquet")
        df = features(w[name], wx)
        is_test = df.index >= TEST_START
        train = df[~is_test & df["y"].notna()]
        test = df[is_test & df["y"].notna() & df["lag_1d"].notna()
                  & df["lag_7d"].notna() & df["prev_day_mean"].notna()]

        preds = pd.DataFrame({"actual": test["y"], "naive_1d": test["lag_1d"],
                              "naive_7d": test["lag_7d"]}, index=test.index)
        importance = None
        for m, (cols, relative) in MODELS.items():
            model = xgb.XGBRegressor(**PARAMS)
            target = train["y"] - train["prev_day_mean"] if relative else train["y"]
            ok = target.notna()
            model.fit(train.loc[ok, cols], target[ok])
            p = model.predict(test[cols])
            preds[m] = p + test["prev_day_mean"].values if relative else p
            if m == "xgb_weather":
                importance = pd.Series(model.feature_importances_, index=cols).sort_values(ascending=False)

        rows = evaluate(name, preds, test["date"], "full test")
        if name in EVENTS:
            ev = EVENTS[name]
            before, after = preds.index < ev, preds.index >= ev
            rows += evaluate(name, preds[before], test["date"][before], f"before {ev:%d %b %Y}")
            rows += evaluate(name, preds[after], test["date"][after], f"after {ev:%d %b %Y}")
        all_rows += rows

        o = OUT / key
        o.mkdir(parents=True, exist_ok=True)
        preds.to_csv(o / "test_predictions.csv")
        pd.DataFrame(rows).to_csv(o / "metrics.csv", index=False)
        importance.to_csv(o / "feature_importance.csv", header=["importance"])

        t_peak = preds["actual"].idxmax()
        wk = preds.loc[t_peak - pd.Timedelta(days=3): t_peak + pd.Timedelta(days=4)]
        fig, ax = plt.subplots(figsize=(12, 5))
        ax.plot(wk.index, wk["actual"], color="black", linewidth=2, label="Actual")
        ax.plot(wk.index, wk["naive_7d"], linestyle=":", label="Naive (last week)")
        ax.plot(wk.index, wk["xgb_weather"], label="XGBoost, with weather")
        ax.plot(wk.index, wk["xgb_weather_level"], label="XGBoost, with weather, level-adjusted")
        ax.axhline(0, color="grey", linewidth=0.6)
        ax.set_ylabel("Net demand (MW)")
        ax.set_title(f"{name}: day-ahead forecasts, week of the test-period peak (Ausgrid data)")
        ax.legend()
        fig.autofmt_xdate()
        fig.tight_layout()
        fig.savefig(o / "peak_week.png", dpi=150)

        print(f"\n=== {name} | train {len(train)} intervals, test {len(test)} "
              f"({test.index.min():%Y-%m-%d} to {test.index.max():%Y-%m-%d}) ===")
        print(pd.DataFrame(rows).drop(columns="substation").to_string(index=False))
        print("Top features (with weather):", ", ".join(f"{k} {v:.2f}" for k, v in importance.head(5).items()))

    pd.DataFrame(all_rows).to_csv(OUT / "metrics_all.csv", index=False)
    print("\nSaved to results/forecasting/ (metrics_all.csv, and per substation: "
          "metrics, predictions, feature importance, peak_week.png)")


if __name__ == "__main__":
    main()
