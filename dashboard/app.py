"""QGEG LV Digital Twin - research dashboard (Phase 12).

Reads the result files produced by the scripts in src/; it does not re-run simulations,
except the Connection check page, which re-simulates one request on demand.
Run from the project folder:  streamlit run dashboard/app.py
"""
import sys
from pathlib import Path
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st
import streamlit.components.v1 as components

ROOT = Path(__file__).resolve().parents[1]
RES = ROOT / "results"
ASSETS = ROOT / "data" / "assets"
NETWORKS = {"zimbabwe_rural": "Zimbabwe rural (synthetic)",
            "zimbabwe_urban": "Zimbabwe urban (synthetic)",
            "european_lv": "IEEE European LV (benchmark)"}
SUBSTATIONS = {"city_north": "City North (Sydney CBD)", "kogarah": "Kogarah (suburban)",
               "branxton": "Branxton (high rooftop solar)"}
ZIM = ["zimbabwe_rural", "zimbabwe_urban"]
V_LOW, V_HIGH = 0.94, 1.10
PAGES = ["Overview", "Network and planning", "Operations", "Asset health",
         "Forecasting", "Flexibility and agents", "Connection check"]

st.set_page_config(page_title="QGEG LV Digital Twin", page_icon="⚡", layout="wide")


@st.cache_data
def load(path, index_col=None, parse_dates=False):
    p = Path(path)
    if not p.exists():
        return None
    try:
        return pd.read_csv(p, index_col=index_col, parse_dates=parse_dates)
    except pd.errors.EmptyDataError:
        return pd.DataFrame()


def have(df, what):
    if df is None:
        st.info(f"No results found for {what}. Run the matching script in src/ first.")
        return False
    return True


def table(df):
    st.dataframe(df, hide_index=True)


def overview():
    st.title("QGEG LV Digital Twin")
    st.markdown("A research digital twin for low-voltage (LV) distribution networks, built on OpenDSS and "
                "Python. It models three networks, monitors them, assesses asset health and power quality, "
                "tests rooftop solar, batteries and EVs, forecasts real substation demand, checks new "
                "connections, and uses rule-based agents whose every recommendation is verified by re-running "
                "the network model.")
    rows = []
    for k, label in NETWORKS.items():
        r = {"Network": label}
        tx, cons = load(ASSETS / k / "transformers.csv"), load(ASSETS / k / "consumers.csv")
        d, hc = load(RES / k / "daily_timeseries.csv"), load(RES / k / "der" / "hosting_capacity.csv")
        if tx is not None:
            r["Transformer (kVA)"] = int(tx.loc[0, "kva"])
        if cons is not None:
            r["Customers"] = len(cons)
        if d is not None:
            r["Daily peak loading (%)"] = round(float(d["tx_loading_pct"].max()))
            r["Lowest voltage (pu)"] = round(float(d["v_min_pu"].min()), 3)
        if hc is not None:
            bad = hc[hc["violations"] != "none"]
            r["PV hosting capacity"] = (f"about {int(bad['pv_share_pct'].iloc[0]) - 10}% of homes"
                                        if len(bad) else "at least 100% of homes")
        rows.append(r)
    st.subheader("Networks")
    table(pd.DataFrame(rows))

    m = load(RES / "forecasting" / "metrics_all.csv")
    if m is not None:
        st.subheader("Day-ahead forecasting on real Ausgrid data (test period Nov 2025 to Apr 2026)")
        keep = m[m["model"].isin(["naive_7d", "xgb_weather", "xgb_weather_level"])]
        if "period" in m:
            keep = keep[keep["period"] == "full test"]
        table(keep[["substation", "model", "mae_mw", "mape_pct", "mae_pct_of_peak", "skill_vs_naive_7d_pct"]])

    st.subheader("Coordination agent: future stress test (50% rooftop PV + 30% EVs, simulated)")
    for k in ZIM:
        c = load(RES / k / "agents" / "coordination_results.csv")
        if c is None or "severity" not in c:
            continue
        base, best = c[c["combination"] == "EV uncontrolled"], c.iloc[0]
        if len(base):
            b = base.iloc[0]
            st.markdown(f"- **{NETWORKS[k]}:** {best['combination']}. Severity {b['severity']} → "
                        f"{best['severity']}; customers out of limits {b['customers_out_of_limits']} → "
                        f"{best['customers_out_of_limits']}; transformer peak {b['max_tx_loading_pct']}% → "
                        f"{best['max_tx_loading_pct']}%.")

    with st.expander("Data sources and limitations"):
        st.markdown(
            "- **Networks:** the IEEE European LV Test Feeder (published benchmark) and two **synthetic** "
            "Zimbabwean networks (not real ZETDC feeders; approximate equipment values; synthetic load profiles).\n"
            "- **Monitoring, asset health, power quality, DER, flexibility and connection checks** are "
            "**simulated** (OpenDSS), not field measurements. Rooftop PV, batteries and EVs are hypothetical.\n"
            "- **Forecasting** uses real 15-minute data from the Ausgrid Distribution Zone Substation Data "
            "(Ausgrid copyright, not redistributed) and Open-Meteo reanalysis weather (CC BY 4.0). Using actual "
            "weather assumes a perfect weather forecast, so with-weather results are optimistic.\n"
            "- **Reliability** uses assumed fault rates. **Harmonics and flicker** are not covered.\n"
            "- **Agents** are deterministic rules. Recommendations are verified in simulation only and require "
            "operator approval before any field action.")


def network_page(net):
    st.header(f"Network and planning: {NETWORKS[net]}")
    html = RES / net / "maps" / "network_map.html"
    if html.exists():
        st.caption("Interactive map: use the buttons to switch views, scroll to zoom, hover for details.")
        components.html(html.read_text(encoding="utf-8"), height=820, scrolling=True)
    else:
        st.info("No map yet. Run src/network_map.py.")
    d = load(RES / net / "daily_timeseries.csv")
    if have(d, "the daily simulation"):
        fig = make_subplots(rows=3, cols=1, shared_xaxes=True,
                            subplot_titles=("Demand (kW)", "Transformer loading (%)", "Lowest voltage (pu)"))
        fig.add_trace(go.Scatter(x=d["hour"], y=d["demand_kw"], name="Demand"), 1, 1)
        fig.add_trace(go.Scatter(x=d["hour"], y=d["tx_loading_pct"], name="Loading"), 2, 1)
        fig.add_trace(go.Scatter(x=d["hour"], y=d["v_min_pu"], name="Lowest voltage"), 3, 1)
        fig.add_hline(y=100, line_dash="dash", line_color="red", row=2, col=1)
        fig.add_hline(y=V_LOW, line_dash="dash", line_color="red", row=3, col=1)
        fig.update_layout(height=650, showlegend=False)
        fig.update_xaxes(title_text="Hour of day", row=3, col=1)
        st.subheader("Daily simulation (base case)")
        st.plotly_chart(fig)
    hc = load(RES / net / "der" / "hosting_capacity.csv")
    if have(hc, "hosting capacity"):
        st.subheader("PV hosting capacity sweep (simulated, clear-sky day)")
        fig = px.line(hc, x="pv_share_pct", y="v_max_pu", markers=True,
                      labels={"pv_share_pct": "Homes with 5 kW PV (%)", "v_max_pu": "Highest voltage (pu)"})
        fig.add_hline(y=V_HIGH, line_dash="dash", line_color="red")
        st.plotly_chart(fig)
        table(hc)
    sc = load(RES / net / "der" / "scenario_comparison.csv")
    if sc is not None:
        st.subheader("DER scenarios")
        table(sc)


def operations_page(net):
    st.header(f"Operations: {NETWORKS[net]}")
    st.caption("Simulated measurements from the daily OpenDSS run, not field telemetry.")
    mon = RES / net / "monitoring"
    t = load(mon / "transformer_timeseries.csv")
    if have(t, "monitoring"):
        c1, c2 = st.columns(2)
        f1 = px.line(t, x="hour", y="loading_pct", title="Transformer loading (%)")
        f1.add_hline(y=100, line_dash="dash", line_color="red")
        c1.plotly_chart(f1)
        c2.plotly_chart(px.line(t, x="hour", y="pf", title="Transformer power factor"))
    f, a, cr = load(mon / "feeder_summary.csv"), load(mon / "alerts.csv"), load(mon / "customer_report.csv")
    if f is not None:
        st.subheader("Feeders")
        table(f)
    st.subheader("Alert log")
    if a is not None:
        if len(a):
            table(a)
        else:
            st.success("No alerts.")
    if cr is not None:
        st.subheader("Lowest-voltage customers")
        table(cr.head(10))
    pq = RES / net / "power_quality"
    st.subheader("Power quality")
    bu = load(pq / "bus_unbalance.csv")
    if bu is not None and len(bu):
        st.markdown("**Voltage unbalance by feeder (limit 2%)**")
        table(bu.groupby("feeder", as_index=False)[["vuf_max_pct", "vuf_p95_pct"]].max())
    ns = load(pq / "feeder_neutral_summary.csv")
    if ns is not None and len(ns):
        st.markdown("**Neutral current at feeder heads**")
        table(ns)
    md = load(pq / "motor_start_dips.csv")
    if md is not None and len(md):
        st.markdown("**Motor-starting dips (direct-on-line)**")
        table(md)
    fs = load(pq / "fault_sags.csv")
    if fs is not None and len(fs):
        st.markdown("**Phase-earth fault at the far end of each feeder**")
        table(fs)
    st.caption("Harmonics and flicker are not covered (they need measured spectra or time-domain models).")


def asset_page(net):
    st.header(f"Asset health: {NETWORKS[net]}")
    st.caption("Stress-based bands from simulated loading and voltage, not asset condition data.")
    ah = RES / net / "asset_health"
    t = load(ah / "transformer_health.csv")
    if have(t, "asset health"):
        r = t.iloc[0]
        c = st.columns(4)
        c[0].metric("Transformer band", r["band"])
        c[1].metric("Peak loading", f"{r['peak_loading_pct']}%")
        c[2].metric("Max hot-spot", f"{r['max_hotspot_c']} °C")
        c[3].metric("Ageing vs normal", f"{r['ageing_vs_normal']}x")
    fh, pr, rel = load(ah / "feeder_health.csv"), load(ah / "maintenance_priority.csv"), load(ah / "reliability_summary.csv")
    if fh is not None:
        st.subheader("Feeders")
        table(fh)
    if pr is not None:
        st.subheader("Maintenance priority")
        table(pr.head(15))
    if rel is not None and len(rel):
        st.subheader("Reliability (assumed fault rates, LV faults only)")
        r = rel.iloc[0]
        c = st.columns(3)
        c[0].metric("SAIFI (interruptions/yr)", r["saifi"])
        c[1].metric("SAIDI (min/yr)", r["saidi_min"])
        c[2].metric("Energy not supplied (kWh/yr)", r["eens_kwh_yr"])
        st.caption("Fault rate 0.1 per km per year, 4 h repair; excludes MV faults and load shedding.")


def forecasting_page():
    st.header("Forecasting: real Ausgrid zone substation data")
    sub = st.selectbox("Substation", list(SUBSTATIONS), format_func=SUBSTATIONS.get)
    p = load(RES / "forecasting" / sub / "test_predictions.csv", index_col=0, parse_dates=True)
    if not have(p, "forecasting"):
        return
    peak = p["actual"].idxmax()
    lo, hi = p.index.min(), p.index.max()
    default = (max(lo, peak - pd.Timedelta(days=3)).date(), min(hi, peak + pd.Timedelta(days=4)).date())
    rng = st.date_input("Dates to show", value=default, min_value=lo.date(), max_value=hi.date())
    if isinstance(rng, (list, tuple)):
        start, end = (rng[0], rng[-1]) if len(rng) else default
    else:
        start = end = rng
    models = [c for c in p.columns if c != "actual"]
    chosen = st.multiselect("Models", models,
                            default=[m for m in ["naive_7d", "xgb_weather", "xgb_weather_level"] if m in models])
    view = p.loc[str(start): f"{end} 23:59", ["actual"] + chosen]
    fig = px.line(view)
    fig.update_layout(xaxis_title="Time", yaxis_title="Net demand (MW)", legend_title="Series", height=480)
    st.plotly_chart(fig)
    m = load(RES / "forecasting" / sub / "metrics.csv")
    if m is not None:
        st.subheader("Accuracy on the test period")
        table(m.drop(columns="substation"))
    fa = load(RES / "agents" / "forecast_agent.csv")
    if fa is not None:
        st.subheader("Forecast agent (switches to the level-adjusted model after sustained bias)")
        table(fa[fa["substation"] == sub])
    fi = load(RES / "forecasting" / sub / "feature_importance.csv")
    if fi is not None:
        fi = fi.copy()
        fi.columns = ["feature", "importance"]
        st.subheader("Most important features (XGBoost with weather)")
        st.plotly_chart(px.bar(fi.head(10), x="importance", y="feature", orientation="h"))
    st.caption("Weather is actual (reanalysis), so with-weather results assume a perfect weather forecast. "
               "Kogarah's load roughly doubles from 21 Mar 2026, probably a load transfer. "
               "Branxton's data is net demand, including rooftop solar.")


def flexibility_page(net):
    st.header(f"Flexibility and agents: {NETWORKS[net]}")
    if net not in ZIM:
        st.info("Flexibility actions and agents were run on the two Zimbabwean networks, "
                "which are the networks with problems to solve.")
        return
    fl = RES / net / "flexibility"
    sr, ts = load(fl / "scenario_results.csv"), load(fl / "scenario_timeseries.csv")
    if have(sr, "flexibility"):
        st.subheader("Single actions, each tested by re-simulation")
        table(sr)
    if ts is not None:
        names = ts["scenario"].unique().tolist()
        scen = st.multiselect("Scenarios to compare", names,
                              default=[s for s in ["pv50", "pv50 + smart battery", "ev30 uncontrolled", "ev30 managed"]
                                       if s in names])
        labels = {"tx_kw": "Transformer kW (negative = export)", "v_max": "Highest voltage (pu)",
                  "v_min": "Lowest voltage (pu)"}
        metric = st.radio("Show", list(labels), format_func=labels.get, horizontal=True)
        fig = px.line(ts[ts["scenario"].isin(scen)], x="hour", y=metric, color="scenario")
        fig.add_hline(y={"tx_kw": 0, "v_max": V_HIGH, "v_min": V_LOW}[metric], line_dash="dash",
                      line_color="red" if metric != "tx_kw" else "grey")
        fig.update_layout(yaxis_title=labels[metric], xaxis_title="Hour of day", height=480)
        st.plotly_chart(fig)
    mo = load(fl / "motor_start_options.csv")
    if mo is not None and len(mo):
        st.subheader("Motor starting options")
        table(mo)
    st.subheader("Coordination agent: decision report")
    st.warning("Verified in simulation only. Requires operator approval before any field action.")
    rep = RES / net / "agents" / "decision_report.md"
    if rep.exists():
        st.markdown(rep.read_text(encoding="utf-8"))
    cr = load(RES / net / "agents" / "coordination_results.csv")
    if cr is not None:
        with st.expander("All tested combinations"):
            table(cr)


def connection_page(net):
    st.header(f"Connection check: {NETWORKS[net]}")
    st.markdown("Should a new rooftop PV system or EV charger be connected? The twin re-simulates a full day "
                "with the request and answers approve, approve with conditions, or refuse.")
    cons = load(ASSETS / net / "consumers.csv")
    if not have(cons, "the asset database"):
        return
    one = cons[cons["phase"].isin(["A", "B", "C"])]
    labels = {r.consumer_id: f"{r.consumer_id} (feeder {r.feeder}, phase {r.phase}, {r.distance_m:.0f} m)"
              for r in one.itertuples()}
    c1, c2, c3 = st.columns(3)
    cid = c1.selectbox("Customer", list(labels), format_func=labels.get)
    pv = c2.number_input("Rooftop PV (kW)", 0.0, 15.0, 5.0, 0.5)
    ev = c3.selectbox("EV charger (kW)", [0.0, 3.7, 7.0], index=0)
    if st.button("Check", type="primary"):
        if pv <= 0 and ev <= 0:
            st.warning("Choose a PV size and/or an EV charger.")
            return
        sys.path.insert(0, str(ROOT / "src"))
        from connection_check import check, report
        with st.spinner("Re-simulating the network for a full day ..."):
            res = check(net, cid, float(pv), float(ev))
        msg = res["decision"] + (f": {res['condition']}" if res["condition"] else "")
        {"APPROVE": st.success, "APPROVE WITH CONDITIONS": st.warning}.get(res["decision"], st.error)(msg)
        b = res["base"]
        st.caption(f"Today: severity {b['severity']}, customer voltage {b['req_v_min']} to {b['req_v_max']} pu, "
                   f"transformer peak {b['max_tx_loading_pct']}%.")
        table(res["tested"].drop(columns=["valid"]))
        st.download_button("Download report", report(res), file_name=f"{cid}_pv{pv:g}_ev{ev:g}.md")
    st.caption("Each request is judged against today's network on a simulated clear-sky day; it does not yet "
               "count connections approved earlier. Decision support only: requires the network planner's approval.")


st.sidebar.title("QGEG LV Digital Twin")
page = st.sidebar.radio("Section", PAGES)
net = None
if page not in ("Overview", "Forecasting"):
    net = st.sidebar.selectbox("Network", list(NETWORKS), format_func=NETWORKS.get)
st.sidebar.caption("Simulated networks, DER and monitoring; real Ausgrid data for forecasting. "
                   "See Overview for data sources and limitations.")

if page == "Overview":
    overview()
elif page == "Network and planning":
    network_page(net)
elif page == "Operations":
    operations_page(net)
elif page == "Asset health":
    asset_page(net)
elif page == "Forecasting":
    forecasting_page()
elif page == "Flexibility and agents":
    flexibility_page(net)
else:
    connection_page(net)
