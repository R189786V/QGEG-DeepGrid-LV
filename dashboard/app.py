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
PAGES = ["Overview", "What-if simulator", "Network and planning", "Operations", "Asset health",
         "Forecasting", "Flexibility and agents", "Connection check", "Customer nodes", "Fault location"]

st.set_page_config(page_title="QGEG LV Digital Twin", page_icon="⚡", layout="wide")
st.markdown("<style>" + (ROOT / "dashboard" / "style.css").read_text(encoding="utf-8") + "</style>", unsafe_allow_html=True)


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


def section(title):
    st.markdown(f"<div class='section-title'>{title}</div><div class='section-rule'></div>", unsafe_allow_html=True)


def page_header(text, sub=None, crumb=None):
    if sub is None and ": " in text:
        page, rest = text.split(": ", 1)
    else:
        page, rest = text, (sub or "")
    st.markdown(f"<div class='page-head'><div class='crumb'>QGEG LV Digital Twin &gt; {crumb or page}</div>"
                f"<div class='page-title'>{page}</div>"
                + (f"<div class='page-sub'>{rest}</div>" if rest else "")
                + "</div><div class='accent-strip'></div>", unsafe_allow_html=True)


def callout(text):
    st.markdown(f"<div class='callout'>{text}</div>", unsafe_allow_html=True)


BAND_COLOUR = {"Green": "#2e7d32", "Amber": "#ef6c00", "Red": "#c62828"}
RANK = {"Green": 0, "Amber": 1, "Red": 2}


VSCALE_DASH = [[0.0, "#b71c1c"], [0.25, "#ef6c00"], [0.375, "#43a047"], [0.7917, "#43a047"], [0.9167, "#ab47bc"], [1.0, "#7b1fa2"]]


def mini_map(net):
    buses, lines = load(ASSETS / net / "buses.csv"), load(ASSETS / net / "lines.csv")
    cons, cr = load(ASSETS / net / "consumers.csv"), load(RES / net / "monitoring" / "customer_report.csv")
    tx = load(ASSETS / net / "transformers.csv")
    if buses is None or lines is None:
        return
    hv = str(tx.loc[0, "hv_bus"]) if tx is not None else None
    xy = {str(b): (x, y) for b, x, y in zip(buses["bus"], buses["x"], buses["y"]) if str(b) != hv}
    xs, ys = [], []
    for b1, b2 in zip(lines["bus1"].astype(str), lines["bus2"].astype(str)):
        if b1 in xy and b2 in xy:
            xs += [xy[b1][0], xy[b2][0], None]
            ys += [xy[b1][1], xy[b2][1], None]
    fig = go.Figure(go.Scatter(x=xs, y=ys, mode="lines", line=dict(color="#5b6b8a", width=1), hoverinfo="skip"))
    if cons is not None and cr is not None:
        m = cons[["consumer_id", "bus"]].merge(cr[["consumer_id", "v_min_pu"]], on="consumer_id")
        m["bus"] = m["bus"].astype(str)
        m = m[m["bus"].isin(list(xy))]
        fig.add_trace(go.Scatter(
            x=[xy[b][0] for b in m["bus"]], y=[xy[b][1] for b in m["bus"]], mode="markers",
            marker=dict(size=7, color=m["v_min_pu"], cmin=0.88, cmax=1.12, colorscale=VSCALE_DASH,
                        line=dict(width=0)),
            text=[f"{cid}: lowest {v:.3f} pu ({v * 230:.0f} V)" for cid, v in zip(m["consumer_id"], m["v_min_pu"])],
            hoverinfo="text"))
    fig.update_layout(height=230, margin=dict(l=0, r=0, t=0, b=0), showlegend=False,
                      paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)")
    fig.update_xaxes(visible=False)
    fig.update_yaxes(visible=False, scaleanchor="x")
    st.plotly_chart(fig, config={"displayModeBar": False}, key=f"mini-{net}", theme=None)
    st.caption("Customers coloured by their lowest voltage of the day (hover for values)")


def network_card(net, label):
    th = load(RES / net / "asset_health" / "transformer_health.csv")
    fh = load(RES / net / "asset_health" / "feeder_health.csv")
    bands = ([th.loc[0, "band"]] if th is not None else []) + (fh["band"].tolist() if fh is not None else [])
    worst = max(bands, key=lambda b: RANK.get(b, 0)) if bands else "Green"
    word = {"Green": "Healthy", "Amber": "Watch", "Red": "Action needed"}.get(worst, worst)
    colour = BAND_COLOUR.get(worst, "#757575")
    with st.container(border=True):
        st.markdown(f"<div style='display:flex;align-items:center;gap:10px;font-size:1.05rem'>"
                    f"<span style='width:14px;height:14px;border-radius:50%;background:{colour};"
                    f"display:inline-block'></span><b>{label}</b>"
                    f"<span style='margin-left:auto;color:{colour};font-weight:600'>{word}</span></div>",
                    unsafe_allow_html=True)
        mini_map(net)
        d = load(RES / net / "daily_timeseries.csv")
        a = load(RES / net / "monitoring" / "alerts.csv")
        hc = load(RES / net / "der" / "hosting_capacity.csv")
        if d is not None:
            c1, c2 = st.columns(2)
            c1.metric("Peak loading", f"{d['tx_loading_pct'].max():.0f}%")
            c2.metric("Lowest voltage", f"{d['v_min_pu'].min():.3f} pu")
            fig = px.area(d, x="hour", y="tx_loading_pct")
            fig.add_hline(y=100, line_dash="dash", line_color="#c62828")
            fig.update_layout(height=110, margin=dict(l=0, r=0, t=0, b=0), showlegend=False)
            fig.update_xaxes(visible=False)
            fig.update_yaxes(visible=False, range=[0, max(110, float(d["tx_loading_pct"].max()) * 1.1)])
            st.plotly_chart(fig, config={"displayModeBar": False}, key=f"spark-{net}")
            st.caption("Transformer loading over the day (dashed line = rating)")
        c3, c4 = st.columns(2)
        c3.metric("Alerts today", len(a) if a is not None else 0)
        if hc is not None:
            bad = hc[hc["violations"] != "none"]
            c4.metric("PV hosting", f"~{int(bad['pv_share_pct'].iloc[0]) - 10}% of homes" if len(bad) else "100%+")


def overview():
    page_header("QGEG LV Digital Twin", "Digital model of low-voltage networks | simulated networks, real Ausgrid demand data | not live", crumb="Overview")
    val = load(RES / "validation" / "validation_checks.csv")
    cv = load(RES / "validation" / "forecast_cross_validation.csv")
    k = st.columns(4)
    k[0].metric("Networks modelled", "3", help="IEEE European LV benchmark + synthetic Zimbabwe urban and rural")
    k[1].metric("Modules", "15")
    if val is not None:
        k[2].metric("Validation checks passed", f"{int((val['result'] == 'PASS').sum())} / {len(val)}")
    if cv is not None:
        best = cv[cv["model"].isin(["xgb_weather", "xgb_weather_level"])] \
            .groupby(["substation", "fold"])["skill_vs_naive_7d_pct"].max()
        k[3].metric("Forecast error cut vs baseline", f"{best.min():.0f}–{best.max():.0f}%",
                    help="Real Ausgrid data, every cross-validation fold")

    section("Network status")
    cols = st.columns(3)
    for col, (net, label) in zip(cols, NETWORKS.items()):
        with col:
            network_card(net, label)

    left, right = st.columns(2)
    with left:
        section("Forecast vs actual demand")
        p = load(RES / "forecasting" / "city_north" / "test_predictions.csv", index_col=0, parse_dates=True)
        if p is not None:
            t = p["actual"].idxmax()
            wk = p.loc[t - pd.Timedelta(days=3): t + pd.Timedelta(days=3), ["actual", "xgb_weather"]]
            wk.columns = ["Measured (Ausgrid)", "Day-ahead forecast"]
            fig = px.line(wk, color_discrete_sequence=["#212121", "#1e88e5"])
            fig.update_layout(height=320, margin=dict(l=0, r=0, t=10, b=0), legend_title=None,
                              xaxis_title=None, yaxis_title="MW")
            st.plotly_chart(fig, key="ov-forecast")
            st.caption("City North zone substation (Sydney CBD), week of the test-period peak. Real data.")
    with right:
        section("Agents: severity before and after")
        rows = []
        for net in ZIM:
            c = load(RES / net / "agents" / "coordination_results.csv")
            if c is None or "severity" not in c:
                continue
            base = c[c["combination"] == "EV uncontrolled"]
            if len(base):
                name = NETWORKS[net].split(" (")[0]
                rows += [{"Network": name, "Case": "Do nothing", "Severity": max(float(base.iloc[0]["severity"]), 0.01)},
                         {"Network": name, "Case": "Agent recommendation", "Severity": max(float(c.iloc[0]["severity"]), 0.01)}]
        if rows:
            fig = px.bar(pd.DataFrame(rows), x="Network", y="Severity", color="Case", barmode="group",
                         text="Severity", log_y=True,
                         color_discrete_map={"Do nothing": "#c62828", "Agent recommendation": "#2e7d32"})
            fig.update_traces(texttemplate="%{text:.1f}", textposition="outside")
            fig.update_layout(height=320, margin=dict(l=0, r=0, t=10, b=0), legend_title=None,
                              yaxis_title="Severity (log scale)", xaxis_title=None)
            st.plotly_chart(fig, key="ov-agents")
            st.caption("Simulated stress test: 50% rooftop PV + 30% EVs. Lower is better.")

    with st.expander("About this project"):
        st.markdown("A research digital twin for LV distribution networks, built on OpenDSS and Python. It models "
                    "three networks, monitors them, assesses asset health and power quality, tests rooftop solar, "
                    "batteries and EVs, forecasts real substation demand, checks new connections, and uses "
                    "rule-based agents whose every recommendation is verified by re-running the network model.")
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
    page_header(f"Network and planning: {NETWORKS[net]}")
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
        section("Daily simulation (base case)")
        st.plotly_chart(fig)
    hc = load(RES / net / "der" / "hosting_capacity.csv")
    if have(hc, "hosting capacity"):
        section("PV hosting capacity sweep (simulated, clear-sky day)")
        fig = px.line(hc, x="pv_share_pct", y="v_max_pu", markers=True,
                      labels={"pv_share_pct": "Homes with 5 kW PV (%)", "v_max_pu": "Highest voltage (pu)"})
        fig.add_hline(y=V_HIGH, line_dash="dash", line_color="red")
        st.plotly_chart(fig)
        table(hc)
    sc = load(RES / net / "der" / "scenario_comparison.csv")
    if sc is not None:
        section("DER scenarios")
        table(sc)


def operations_page(net):
    page_header(f"Operations: {NETWORKS[net]}")
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
        section("Feeders")
        table(f)
    section("Alert log")
    if a is not None:
        if len(a):
            table(a)
        else:
            st.success("No alerts.")
    if cr is not None:
        section("Lowest-voltage customers")
        table(cr.head(10))
    pq = RES / net / "power_quality"
    section("Power quality")
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
    page_header(f"Asset health: {NETWORKS[net]}")
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
        section("Feeders")
        table(fh)
    if pr is not None:
        section("Maintenance priority")
        table(pr.head(15))
    if rel is not None and len(rel):
        section("Reliability (assumed fault rates, LV faults only)")
        r = rel.iloc[0]
        c = st.columns(3)
        c[0].metric("SAIFI (interruptions/yr)", r["saifi"])
        c[1].metric("SAIDI (min/yr)", r["saidi_min"])
        c[2].metric("Energy not supplied (kWh/yr)", r["eens_kwh_yr"])
        st.caption("Fault rate 0.1 per km per year, 4 h repair; excludes MV faults and load shedding.")


def forecasting_page():
    page_header("Forecasting: real Ausgrid zone substation data")
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
        section("Accuracy on the test period")
        table(m.drop(columns="substation"))
    fa = load(RES / "agents" / "forecast_agent.csv")
    if fa is not None:
        section("Forecast agent (switches to the level-adjusted model after sustained bias)")
        table(fa[fa["substation"] == sub])
    fi = load(RES / "forecasting" / sub / "feature_importance.csv")
    if fi is not None:
        fi = fi.copy()
        fi.columns = ["feature", "importance"]
        section("Most important features (XGBoost with weather)")
        st.plotly_chart(px.bar(fi.head(10), x="importance", y="feature", orientation="h"))
    st.caption("Weather is actual (reanalysis), so with-weather results assume a perfect weather forecast. "
               "Kogarah's load roughly doubles from 21 Mar 2026, probably a load transfer. "
               "Branxton's data is net demand, including rooftop solar.")


EV_LABEL = {"uncontrolled": "EV charging uncontrolled (from 18:00)",
            "managed": "Managed EV charging (23:00 to 05:00)",
            "slow": "Slow EV charging (21:00 to 07:00)"}


def actions_of(r):
    acts = []
    if r.get("ev") and r["ev"] != "uncontrolled":
        acts.append(EV_LABEL.get(r["ev"], f"EV charging: {r['ev']}"))
    if bool(r.get("battery", False)):
        acts.append("Smart batteries on PV homes")
    if bool(r.get("voltwatt", False)):
        acts.append("Volt-watt inverter control")
    if bool(r.get("rebalance", False)):
        acts.append("Rebalance phases on the worst feeder (field crew)")
    if bool(r.get("pump_day", False)):
        acts.append("Move irrigation pump to daytime (farmer agreement)")
    return acts or ["No action"]


def option_card(title, r, base, recommended=False):
    with st.container(border=True):
        badge = ("<span style='background:#fab724;color:#1c1f3e;font-weight:700;font-size:0.75rem;"
                 "padding:3px 8px;border-radius:4px;margin-left:8px'>RECOMMENDED</span>") if recommended else ""
        st.markdown(f"<div style='font-weight:700;font-size:1.05rem;color:#1c1f3e'>{title}{badge}</div>",
                    unsafe_allow_html=True)
        st.markdown("\n".join(f"- {a}" for a in actions_of(r)))
        a, b = st.columns(2)
        a.metric("Severity", f"{r['severity']:.2f}", f"{r['severity'] - base['severity']:.1f}", delta_color="inverse")
        b.metric("Customers out of limits", int(r["customers_out_of_limits"]),
                 int(r["customers_out_of_limits"] - base["customers_out_of_limits"]), delta_color="inverse")
        st.caption("Changes are against doing nothing.")
        st.caption(f"Transformer peak {r['max_tx_loading_pct']}% | voltage {r['v_min_pu']:.3f} to "
                   f"{r['v_max_pu']:.3f} pu | solar curtailed {r.get('curtail_pct', 0)}%")


def flexibility_page(net):
    page_header(f"Flexibility and agents: {NETWORKS[net]}")
    if net not in ZIM:
        st.info("Flexibility actions and agents were run on the two Zimbabwean networks, "
                "which are the networks with problems to solve.")
        return
    ag, fl = RES / net / "agents", RES / net / "flexibility"
    cr = load(ag / "coordination_results.csv")
    if cr is None or "severity" not in cr:
        st.info("No agent results yet. Run python src/agents.py for this network.")
        return
    valid = cr[cr["valid"].astype(bool)] if "valid" in cr else cr
    base = cr[cr["combination"] == "EV uncontrolled"].iloc[0]
    best = valid.iloc[0]
    callout(f"<b>Recommendation:</b> {len(actions_of(best))} actions | severity {base['severity']:.1f} to "
            f"{best['severity']:.1f} | customers out of limits {int(base['customers_out_of_limits'])} to "
            f"{int(best['customers_out_of_limits'])} | awaiting operator approval")
    n_invalid = int((~cr["valid"].astype(bool)).sum()) if "valid" in cr else 0
    st.caption(f"Simulated stress test: 50% of homes with 5 kW rooftop PV and 30% with a 7 kW EV. "
               f"{len(cr)} combinations re-simulated in OpenDSS; {n_invalid} invalid results excluded.")

    section("Options for the operator")
    no_pump = valid[~valid["pump_day"].astype(bool)] if "pump_day" in valid else valid
    no_field = no_pump[~no_pump["rebalance"].astype(bool)] if "rebalance" in no_pump else no_pump
    shown, seen = [], set()
    for title, df in [("Best overall", valid), ("Best without moving the pump", no_pump),
                      ("Best without field work", no_field)]:
        if len(df) and df.iloc[0]["combination"] not in seen:
            seen.add(df.iloc[0]["combination"])
            shown.append((title, df.iloc[0]))
    cols = st.columns(len(shown))
    for i, (col, (title, r)) in enumerate(zip(cols, shown)):
        with col:
            option_card(title, r, base, recommended=(i == 0))

    section("Operator decision")
    log_path = ag / "approvals.csv"
    c1, c2 = st.columns([2, 1])
    choice = c2.selectbox("Option", [t for t, _ in shown], key=f"opt-{net}")
    note = c1.text_input("Note for the record (optional)", key=f"note-{net}")
    b1, b2, b3, _ = st.columns([1, 1, 1.4, 2])
    decision = None
    if b1.button("Approve", type="primary", key=f"ap-{net}"):
        decision = "Approved"
    if b2.button("Reject", key=f"rj-{net}"):
        decision = "Rejected"
    if b3.button("Request further study", key=f"rs-{net}"):
        decision = "Further study requested"
    if decision:
        r = dict(shown)[choice]
        pd.DataFrame([{"time": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M"), "network": net, "option": choice,
                       "combination": r["combination"], "decision": decision, "note": note}]) \
            .to_csv(log_path, mode="a", header=not log_path.exists(), index=False)
        st.success(f"{decision}: {r['combination']}. Recorded in the decision log.")
    if log_path.exists():
        with st.expander("Decision log"):
            st.dataframe(pd.read_csv(log_path).iloc[::-1], hide_index=True)
    st.caption("The decision is recorded for audit only. This research prototype never sends commands to a real network.")

    section("How the options compare")
    top = valid.head(12).iloc[::-1].copy()
    top["Option"] = top["combination"].eq(best["combination"]).map({True: "Recommended", False: "Other"})
    fig = px.bar(top, x="severity", y="combination", orientation="h", log_x=True, text="severity", color="Option",
                 color_discrete_map={"Recommended": "#fab724", "Other": "#1c1f3e"})
    fig.update_traces(texttemplate="%{text:.2f}", textposition="outside")
    fig.update_layout(height=520, margin=dict(l=0, r=40, t=10, b=0), legend_title=None, yaxis_title=None,
                      xaxis_title="Severity score (log scale, lower is better)")
    st.plotly_chart(fig, key=f"cmp-{net}")

    section("Single actions, each re-simulated")
    sr, ts = load(fl / "scenario_results.csv"), load(fl / "scenario_timeseries.csv")
    if ts is not None:
        names = ts["scenario"].unique().tolist()
        scen = st.multiselect("Scenarios to compare", names,
                              default=[s for s in ["pv50", "pv50 + smart battery", "ev30 uncontrolled", "ev30 managed"]
                                       if s in names], key=f"scen-{net}")
        labels = {"tx_kw": "Transformer kW (negative = export)", "v_max": "Highest voltage (pu)",
                  "v_min": "Lowest voltage (pu)"}
        metric = st.radio("Show", list(labels), format_func=labels.get, horizontal=True, key=f"met-{net}")
        fig = px.line(ts[ts["scenario"].isin(scen)], x="hour", y=metric, color="scenario")
        fig.add_hline(y={"tx_kw": 0, "v_max": V_HIGH, "v_min": V_LOW}[metric], line_dash="dash",
                      line_color="#c62828" if metric != "tx_kw" else "#999999")
        fig.update_layout(yaxis_title=labels[metric], xaxis_title="Hour of day", height=440, legend_title=None)
        st.plotly_chart(fig, key=f"single-{net}")
    if sr is not None:
        with st.expander("Single-action results table"):
            table(sr)
    mo = load(fl / "motor_start_options.csv")
    if mo is not None and len(mo):
        with st.expander("Motor starting options"):
            table(mo)

    with st.expander("Caveats"):
        st.markdown("- Rule-based controls on a synthetic network with hypothetical DER and EVs; one clear-sky day.\n"
                    "- Every EV option delivers the same energy per car (14 kWh); the pump keeps its 7 full-load hours.\n"
                    "- Moving irrigation to daytime increases evaporation and needs the farmer's agreement.\n"
                    "- Phase rebalancing needs a field crew to reconnect service cables.\n"
                    "- Volt-watt holds voltage just above 1.10 pu by design (control band 253 to 260 V).")
    rep = ag / "decision_report.md"
    if rep.exists():
        with st.expander("Full decision report (text)"):
            st.markdown(rep.read_text(encoding="utf-8"))
    with st.expander("All tested combinations"):
        table(cr)


def connection_page(net):
    page_header(f"Connection check: {NETWORKS[net]}")
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


@st.cache_data(show_spinner=False)
def run_whatif(net, items):
    sys.path.insert(0, str(ROOT / "src"))
    from whatif import run
    return run(net, dict(items))


def whatif_page(net):
    import plotly.io as pio
    page_header(f"What-if simulator: {NETWORKS[net]}")
    st.markdown("Change the network and its customers, then press **Run simulation**. The twin re-simulates a "
                "full day in OpenDSS and replays it on the map. Results are simulated (a digital model), not live data.")
    with st.expander("How it works"):
        st.graphviz_chart('''digraph { rankdir=LR; node [shape=box, style="rounded", fontname="Helvetica", fontsize=11];
            a [label="Your settings\\nPV, EVs, batteries,\\ncontrols, equipment"];
            b [label="Network model\\n+ daily load profiles"];
            c [label="OpenDSS power flow\\nevery 15 minutes"];
            d [label="Voltages, currents,\\nloading, losses"];
            e [label="Animated map\\n+ severity score"];
            a -> c; b -> c; c -> d -> e; }''')
    tx = load(ASSETS / net / "transformers.csv")
    kva0 = int(tx.loc[0, "kva"]) if tx is not None else 100
    src0 = 1.05 if net == "european_lv" else 1.00
    c1, c2, c3 = st.columns(3)
    with c1:
        st.markdown("**Customers**")
        pv = st.slider("Homes with 5 kW rooftop PV (%)", 0, 100, 0, 10)
        ev = st.slider("Homes with a 7 kW EV (%)", 0, 100, 0, 10)
        mode = st.selectbox("EV charging", ["uncontrolled", "managed", "slow"],
                            format_func={"uncontrolled": "Uncontrolled (from 18:00)",
                                         "managed": "Managed (23:00 to 05:00)",
                                         "slow": "Slow (21:00 to 07:00)"}.get)
    with c2:
        st.markdown("**Controls and field actions**")
        battery = st.checkbox("Smart batteries on PV homes")
        voltwatt = st.checkbox("Volt-watt inverter control")
        rebalance = st.checkbox("Rebalance phases on the worst feeder")
        pump = st.checkbox("Move irrigation pump to daytime", disabled=(net != "zimbabwe_rural"))
    with c3:
        st.markdown("**Equipment**")
        sizes = sorted({kva0, 100, 200, 315, 500, 800})
        kva = st.selectbox("Transformer rating (kVA)", sizes, index=sizes.index(kva0))
        src = st.slider("Supply voltage (pu)", 0.95, 1.05, src0, 0.01)
    params = dict(pv=pv / 100, ev=ev / 100, mode=mode, battery=battery, voltwatt=voltwatt,
                  rebalance=rebalance, pump_day=bool(pump), tx_kva=float(kva), source_pu=float(src))
    base = dict(pv=0.0, ev=0.0, mode="uncontrolled", battery=False, voltwatt=False, rebalance=False,
                pump_day=False, tx_kva=float(kva0), source_pu=float(src0))
    if st.button("Run simulation", type="primary"):
        st.session_state["whatif"] = (net, params)
    saved = st.session_state.get("whatif")
    if not saved or saved[0] != net:
        st.info("Choose settings and press Run simulation. A Zimbabwean network takes a few seconds; "
                "the European feeder about half a minute.")
        return
    with st.spinner("Re-simulating the network for a full day ..."):
        fig_json, s = run_whatif(net, tuple(sorted(saved[1].items())))
        _, b = run_whatif(net, tuple(sorted(base.items())))
    st.caption("Compared with today's network (no new PV or EVs, original equipment).")
    m = st.columns(5)
    m[0].metric("Severity score", s["severity"], round(s["severity"] - b["severity"], 2), delta_color="inverse")
    m[1].metric("Customers out of limits", s["customers_out_of_limits"],
                s["customers_out_of_limits"] - b["customers_out_of_limits"], delta_color="inverse")
    m[2].metric("Transformer peak", f"{s['tx_peak_pct']}%",
                f"{s['tx_peak_pct'] - b['tx_peak_pct']:+.1f} points", delta_color="inverse")
    m[3].metric("Voltage range (pu)", f"{s['v_min_pu']:.3f} to {s['v_max_pu']:.3f}")
    m[4].metric("Losses (kWh/day)", s["losses_kwh"], round(s["losses_kwh"] - b["losses_kwh"], 1),
                delta_color="inverse")
    for note in s["notes"]:
        st.caption(note)
    if s["nonconverged_steps"]:
        st.warning(f"{s['nonconverged_steps']} time steps did not converge; treat these results with caution.")
    st.plotly_chart(pio.from_json(fig_json))
    st.caption("The map opens at the moment of peak transformer loading. Press Play or drag the slider to replay the day.")


def num(df, col, row):
    try:
        return float(df.loc[row, col])
    except (KeyError, ValueError, TypeError):
        return None


def customer_nodes_page(net):
    page_header(f"Customer nodes: {NETWORKS[net]}")
    out = RES / net / "customer_nodes"
    comp = load(out / "network_comparison.csv", index_col=0)
    if comp is None:
        st.info(f"No results yet. Run: python src/customer_nodes.py {net} pv=0.5 ev=0.3 hp=0.4 battery=0.5 control=all")
        return
    ctrls = [c for c in comp.columns if c.startswith("controller:")]
    sm, nw = "controller: smart", "controller: network"
    if sm in comp and nw in comp:
        callout(f"<b>Network-aware control:</b> transformer peak {num(comp, sm, 'tx_peak_pct'):.0f}% to "
                f"{num(comp, nw, 'tx_peak_pct'):.0f}% | customers out of limits {num(comp, sm, 'customers_out_of_limits'):.0f} "
                f"to {num(comp, nw, 'customers_out_of_limits'):.0f} | peak export {num(comp, sm, 'max_export_kw'):.0f} to "
                f"{num(comp, nw, 'max_export_kw'):.0f} kW, compared with home-only control")
    st.caption("Each customer is a node: meter profile, rooftop PV, home battery, EV charger and heat-pump water heater, "
               "a node controller and a net meter. Every net meter drives the OpenDSS network model at each 15-minute "
               "step. All DER is hypothetical and simulated.")
    section("Controllers compared")
    keep = ["severity", "customers_out_of_limits", "customers_undervoltage", "customers_overvoltage", "v_min_pu",
            "v_max_pu", "tx_peak_pct", "max_import_kw", "max_export_kw", "max_line_pct", "losses_kwh",
            "solar_used_on_site_pct"]
    st.dataframe(comp.loc[[k for k in keep if k in comp.index]].T, use_container_width=True)
    if ctrls and "layer_check_max_kw" in comp.index:
        worst = max(num(comp, c, "layer_check_max_kw") or 0 for c in ctrls)
        st.caption(f"Cross-layer balance check: transformer import minus losses equals the sum of all net meters "
                   f"at every step (largest mismatch {worst:.3f} kW).")
    img = out / "example_node.png"
    if img.exists():
        section("One home, and the whole network")
        st.image(str(img), use_container_width=True)
    section("Customer table")
    ctrl = st.selectbox("Controller", [c.replace("controller: ", "") for c in ctrls], key=f"cn-{net}")
    t = load(out / f"node_summary_{ctrl}.csv")
    if t is not None:
        st.dataframe(t, hide_index=True, use_container_width=True)


def fault_page(net):
    page_header(f"Fault location: {NETWORKS[net]}")
    out = RES / net / "fault_location"
    summ, tf = load(out / "summary.csv"), load(out / "test_faults.csv")
    if summ is None or tf is None:
        st.info(f"No results yet. Run: python src/fault_locator.py {net} 200")
        return
    s = summ.set_index("method")
    callout(f"<b>{len(tf)} simulated faults located from busbar measurements only:</b> feeder identified "
            f"{100 * tf['feeder_identified'].mean():.0f}% | median error {s.loc['reactance', 'median_error_m']:.0f} m "
            f"(reactance) | typically {tf['reactance_candidates'].median():.0f} candidate points to check")
    st.caption("Random faults with unknown fault resistance (0.001 to 0.5 ohm), load level (30 to 100%) and measurement "
               "error (1%, 0.5 degrees). The locator sees only busbar voltage and feeder-head currents; errors are "
               "cable distance to the true fault. Simulated (phasor values), not field-validated.")
    section("Methods compared")
    cols = st.columns(2)
    for col, m in zip(cols, ["impedance", "reactance"]):
        with col:
            with st.container(border=True):
                st.markdown(f"**{m.capitalize()} method**")
                a, b, c = st.columns(3)
                a.metric("Median error", f"{s.loc[m, 'median_error_m']:.0f} m")
                b.metric("Within 100 m", f"{s.loc[m, 'within_100m_pct']:.0f}%")
                c.metric("90% within", f"{s.loc[m, 'p90_error_m']:.0f} m")
    section("Effect of fault resistance")
    tf = tf.copy()
    tf["Fault resistance"] = pd.cut(tf["fault_r_ohm"], [0, 0.01, 0.1, 0.5],
                                    labels=["solid (<= 0.01 ohm)", "0.01 to 0.1 ohm", "0.1 to 0.5 ohm"])
    by = (tf.groupby("Fault resistance", observed=True)[["impedance_error_m", "reactance_error_m"]].median()
          .round(1).rename(columns={"impedance_error_m": "Impedance: median error (m)",
                                    "reactance_error_m": "Reactance: median error (m)"}))
    st.dataframe(by, use_container_width=True)
    st.caption("The impedance method is exact for solid faults but fault resistance makes faults look farther away; "
               "the reactance method is unaffected by fault resistance. Branching leaves several points that look "
               "the same from the busbar.")
    img = out / "fault_location.png"
    if img.exists():
        st.image(str(img), use_container_width=True)
    with st.expander("All test faults"):
        st.dataframe(tf.drop(columns=["Fault resistance"]), hide_index=True, use_container_width=True)


st.sidebar.title("QGEG LV Digital Twin")
page = st.sidebar.radio("Section", PAGES)
net = None
if page not in ("Overview", "Forecasting"):
    net = st.sidebar.selectbox("Network", list(NETWORKS), format_func=NETWORKS.get)
st.sidebar.caption("Simulated networks, DER and monitoring; real Ausgrid data for forecasting. "
                   "See Overview for data sources and limitations.")

if page == "Overview":
    overview()
elif page == "What-if simulator":
    whatif_page(net)
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
elif page == "Connection check":
    connection_page(net)
elif page == "Customer nodes":
    customer_nodes_page(net)
else:
    fault_page(net)
