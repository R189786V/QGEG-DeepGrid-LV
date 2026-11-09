"""Phase 5: asset health and reliability (stress-based, from simulated monitoring).

Health bands come from simulated loading and voltage, not asset condition data
(age, inspections, oil tests). Reliability uses ASSUMED fault rates that should
be replaced with utility fault statistics, and covers LV faults only.
Results are relative rankings for planning, not failure probabilities.
"""
import sys
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
FEEDER = sys.argv[1] if len(sys.argv) > 1 else "zimbabwe_rural"
ASSETS = ROOT / "data" / "assets" / FEEDER
MON = ROOT / "results" / FEEDER / "monitoring"
OUT = ROOT / "results" / FEEDER / "asset_health"

# Transformer thermal model: IEC 60076-7 steady-state, ONAN distribution transformer
AMBIENT_C = 25.0
DTHETA_OR, HGR, R_LOSS, X_OIL, Y_WDG = 55.0, 23.0, 5.0, 0.8, 1.6
# Reliability assumptions (illustrative; replace with ZETDC fault statistics)
LINE_FAULTS_PER_KM_YR = 0.10
LINE_REPAIR_H = 4.0
TX_FAULTS_PER_YR = 0.01
TX_REPAIR_H = 24.0
WEIGHT = {"Red": 3, "Amber": 2, "Green": 1}


def band(value, amber, red, higher_is_worse=True):
    if higher_is_worse:
        return "Red" if value > red else "Amber" if value > amber else "Green"
    return "Red" if value < red else "Amber" if value < amber else "Green"


def worst(*bands):
    return max(bands, key=lambda b: WEIGHT[b])


def main():
    s = str
    consumers = pd.read_csv(ASSETS / "consumers.csv", dtype={"bus": s, "load": s})
    lines = pd.read_csv(ASSETS / "lines.csv", dtype={"line": s, "bus1": s, "bus2": s})
    tx = pd.read_csv(ASSETS / "transformers.csv")
    tts = pd.read_csv(MON / "transformer_timeseries.csv")
    lrep = pd.read_csv(MON / "line_report.csv", dtype={"line": s, "feeder": s})
    cust = pd.read_csv(MON / "customer_report.csv", dtype={"load": s, "feeder": s})
    fsum = pd.read_csv(MON / "feeder_summary.csv", dtype={"feeder": s})
    alerts = pd.read_csv(MON / "alerts.csv", dtype={"asset": s})

    n_total = len(consumers)
    n_feeder = consumers["feeder"].value_counts().to_dict()

    # 1. Transformer: loading and thermal ageing
    dt = 24 / len(tts)
    k = tts["loading_pct"] / 100
    hot = AMBIENT_C + DTHETA_OR * ((1 + R_LOSS * k ** 2) / (1 + R_LOSS)) ** X_OIL + HGR * k ** Y_WDG
    ageing_h = float((2 ** ((hot - 98) / 6)).sum() * dt)
    peak = float(tts["loading_pct"].max())
    tx_band = worst(band(peak, 80, 100), band(ageing_h, 12, 24))
    tx_health = pd.DataFrame([{
        "transformer": tx.loc[0, "transformer"], "kva": tx.loc[0, "kva"],
        "peak_loading_pct": round(peak, 1), "max_hotspot_c": round(float(hot.max()), 1),
        "ageing_hours_per_day": round(ageing_h, 2), "ageing_vs_normal": round(ageing_h / 24, 3),
        "band": tx_band}])

    # 2. Lines: loading band, customers interrupted by a fault, reliability
    lrep = lrep.merge(lines[["line", "phases"]], on="line", how="left")
    lrep["band"] = lrep["max_loading_pct"].apply(lambda v: band(v, 80, 100))
    lrep["customers_affected"] = [1 if ph == 1 else n_feeder.get(f, 0)
                                  for ph, f in zip(lrep["phases"], lrep["feeder"])]
    lrep["faults_per_yr"] = LINE_FAULTS_PER_KM_YR * lrep["length"] / 1000
    lrep["cust_interruptions_yr"] = lrep["faults_per_yr"] * lrep["customers_affected"]
    lrep["cust_minutes_yr"] = lrep["cust_interruptions_yr"] * LINE_REPAIR_H * 60

    # 3. Customer supply: voltage band
    cust["band"] = cust["v_min_pu"].apply(lambda v: band(v, 0.94, 0.90, higher_is_worse=False))

    # 4. Feeders: worst of their lines, customers and imbalance alerts; reliability
    avg_kw = dict(zip(fsum["feeder"], fsum["energy_kwh"] / 24))
    frows = []
    for f in sorted(n_feeder):
        fl, fc = lrep[lrep["feeder"] == f], cust[cust["feeder"] == f]
        n_imb = int(((alerts["type"] == "phase_imbalance") & (alerts["asset"] == f)).sum())
        fb = worst("Amber" if n_imb else "Green", *fl["band"], *fc["band"])
        main_faults = fl.loc[fl["phases"] != 1, "faults_per_yr"].sum()
        svc_faults = fl.loc[fl["phases"] == 1, "faults_per_yr"].sum()
        n = n_feeder[f]
        frows.append({
            "feeder": f, "customers": n, "line_km": round(fl["length"].sum() / 1000, 3),
            "min_voltage_pu": round(fc["v_min_pu"].min(), 4),
            "max_line_loading_pct": round(fl["max_loading_pct"].max(), 1),
            "imbalance_alerts": n_imb,
            "saifi": round(fl["cust_interruptions_yr"].sum() / n + TX_FAULTS_PER_YR, 4),
            "saidi_min": round(fl["cust_minutes_yr"].sum() / n + TX_FAULTS_PER_YR * TX_REPAIR_H * 60, 1),
            "eens_kwh_yr": round((main_faults + svc_faults / n) * LINE_REPAIR_H * avg_kw.get(f, 0)
                                 + TX_FAULTS_PER_YR * TX_REPAIR_H * avg_kw.get(f, 0), 1),
            "band": fb})
    feeders = pd.DataFrame(frows)

    tx_ci = TX_FAULTS_PER_YR * n_total
    saifi = (lrep["cust_interruptions_yr"].sum() + tx_ci) / n_total
    saidi = (lrep["cust_minutes_yr"].sum() + tx_ci * TX_REPAIR_H * 60) / n_total

    # 5. Maintenance priority: worst band first, then most customers affected
    pr = [{"asset_type": "transformer", "asset": tx.loc[0, "transformer"], "band": tx_band,
           "customers_affected": n_total,
           "reason": f"peak {peak:.0f}%, ageing {ageing_h / 24:.2f}x normal"}]
    for r in feeders.itertuples(index=False):
        pr.append({"asset_type": "feeder", "asset": r.feeder, "band": r.band,
                   "customers_affected": r.customers,
                   "reason": f"min {r.min_voltage_pu * 230:.0f} V, max line {r.max_line_loading_pct:.0f}%, "
                             f"{r.imbalance_alerts} imbalance alerts"})
    for r in lrep[lrep["band"] != "Green"].itertuples(index=False):
        pr.append({"asset_type": "line", "asset": r.line, "band": r.band,
                   "customers_affected": r.customers_affected,
                   "reason": f"max loading {r.max_loading_pct:.0f}%"})
    for r in cust[cust["band"] != "Green"].itertuples(index=False):
        pr.append({"asset_type": "customer_supply", "asset": r.consumer_id, "band": r.band,
                   "customers_affected": 1,
                   "reason": f"min {r.v_min_volts:.0f} V, {r.hours_below_limit} h below limit"})
    pr = pd.DataFrame(pr)
    pr["w"] = pr["band"].map(WEIGHT)
    pr = pr.sort_values(["w", "customers_affected"], ascending=False).drop(columns="w")
    pr.insert(0, "priority", range(1, len(pr) + 1))

    OUT.mkdir(parents=True, exist_ok=True)
    tx_health.to_csv(OUT / "transformer_health.csv", index=False)
    lrep.to_csv(OUT / "line_health.csv", index=False)
    cust.to_csv(OUT / "customer_health.csv", index=False)
    feeders.to_csv(OUT / "feeder_health.csv", index=False)
    pr.to_csv(OUT / "maintenance_priority.csv", index=False)
    pd.DataFrame([{"saifi": round(saifi, 4), "saidi_min": round(saidi, 1),
                   "eens_kwh_yr": round(feeders["eens_kwh_yr"].sum(), 1)}]) \
        .to_csv(OUT / "reliability_summary.csv", index=False)

    t = tx_health.iloc[0]
    print(f"=== {FEEDER}: asset health and reliability ===")
    print(f"Transformer {t['transformer']} ({t['kva']:.0f} kVA): {t['band']} | peak {t['peak_loading_pct']}% | "
          f"max hot-spot {t['max_hotspot_c']} C | ageing {t['ageing_vs_normal']}x normal")
    print("\nFeeders:")
    print(feeders.to_string(index=False))
    print(f"\nLines: {(lrep['band'] == 'Red').sum()} Red, {(lrep['band'] == 'Amber').sum()} Amber, "
          f"{(lrep['band'] == 'Green').sum()} Green")
    print(f"Customer supplies: {(cust['band'] == 'Red').sum()} Red, {(cust['band'] == 'Amber').sum()} Amber, "
          f"{(cust['band'] == 'Green').sum()} Green")
    print(f"\nReliability (ASSUMED fault rates, LV faults only): SAIFI {saifi:.3f} /yr, "
          f"SAIDI {saidi:.1f} min/yr, EENS {feeders['eens_kwh_yr'].sum():.0f} kWh/yr")
    print("\nMaintenance priority (top 8):")
    print(pr.head(8).to_string(index=False))
    print(f"\nSaved to results/{FEEDER}/asset_health/")


if __name__ == "__main__":
    main()
