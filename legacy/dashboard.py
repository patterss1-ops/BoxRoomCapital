"""
Trading Bot Dashboard — Streamlit app for monitoring positions, P&L, and strategy performance.

Usage: streamlit run dashboard.py
"""
import streamlit as st
import pandas as pd
import os
import sys
import json
import time
import requests
from datetime import datetime, date, timedelta
from dotenv import load_dotenv

# Add project root to path
sys.path.insert(0, os.path.dirname(__file__))
load_dotenv()

import config
from data.trade_db import (
    get_all_trades, get_closed_trades, get_open_positions,
    get_daily_snapshots, get_strategy_stats, get_summary,
    get_bot_events, init_db, DB_PATH,
    get_open_option_positions, get_closed_option_positions,
    get_shadow_trades,
)

# ─── Page config ─────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="Trading Bot Dashboard",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ─── Custom CSS ──────────────────────────────────────────────────────────────

st.markdown("""
<style>
    .metric-card {
        background: linear-gradient(135deg, #1e1e2e 0%, #2d2d44 100%);
        border-radius: 12px;
        padding: 20px;
        border: 1px solid #3d3d5c;
    }
    .positive { color: #00d4aa; }
    .negative { color: #ff6b6b; }
    .neutral { color: #a0a0b0; }
    div[data-testid="stMetricValue"] { font-size: 1.5rem; }
    .stTabs [data-baseweb="tab-list"] { gap: 8px; }
    .stTabs [data-baseweb="tab"] {
        padding: 10px 20px;
        border-radius: 8px;
    }
</style>
""", unsafe_allow_html=True)


# ─── IG Live Data Fetcher ────────────────────────────────────────────────────

@st.cache_data(ttl=30)  # Cache for 30 seconds
def fetch_ig_live_data():
    """Fetch live account info and positions from IG API."""
    username = os.getenv("IG_USERNAME")
    password = os.getenv("IG_PASSWORD")
    api_key = os.getenv("IG_API_KEY")
    acc_num = os.getenv("IG_ACC_NUMBER", "")
    acc_type = os.getenv("IG_ACC_TYPE", "LIVE")

    if not all([username, password, api_key]):
        return None, None, None

    base = "https://api.ig.com/gateway/deal" if acc_type == "LIVE" else "https://demo-api.ig.com/gateway/deal"

    try:
        s = requests.Session()
        s.headers.update({
            "Content-Type": "application/json; charset=UTF-8",
            "Accept": "application/json; charset=UTF-8",
            "X-IG-API-KEY": api_key,
        })

        # Auth
        r = s.post(f"{base}/session",
                   json={"identifier": username, "password": password},
                   headers={**s.headers, "Version": "2"})
        if r.status_code != 200:
            return None, None, None

        s.headers.update({"CST": r.headers["CST"], "X-SECURITY-TOKEN": r.headers["X-SECURITY-TOKEN"]})

        # Switch account if needed
        auth = r.json()
        if auth.get("currentAccountId") != acc_num and acc_num:
            s.put(f"{base}/session", json={"accountId": acc_num, "defaultAccount": "false"},
                  headers={**s.headers, "Version": "1"})

        # Account info
        acc_resp = s.get(f"{base}/accounts", headers={**s.headers, "Version": "1"})
        account_info = None
        if acc_resp.status_code == 200:
            for acc in acc_resp.json().get("accounts", []):
                if acc.get("accountId") == acc_num or acc.get("accountType") == "SPREADBET":
                    balance = acc.get("balance", {})
                    account_info = {
                        "balance": balance.get("balance", 0),
                        "deposit": balance.get("deposit", 0),
                        "pnl": balance.get("profitLoss", 0),
                        "available": balance.get("available", 0),
                        "currency": acc.get("currency", "GBP"),
                    }
                    break

        # Positions
        pos_resp = s.get(f"{base}/positions", headers={**s.headers, "Version": "2"})
        positions = []
        if pos_resp.status_code == 200:
            for p in pos_resp.json().get("positions", []):
                mkt = p.get("market", {})
                pos = p.get("position", {})
                positions.append({
                    "market": mkt.get("instrumentName", ""),
                    "epic": mkt.get("epic", ""),
                    "direction": pos.get("direction", ""),
                    "size": pos.get("size", 0),
                    "open_level": pos.get("openLevel", pos.get("level", 0)),
                    "current_bid": mkt.get("bid", 0),
                    "current_offer": mkt.get("offer", 0),
                    "pnl": pos.get("profit", 0) if pos.get("profit") else 0,
                    "deal_id": pos.get("dealId", ""),
                    "created": pos.get("createdDateUTC", ""),
                    "currency": pos.get("currency", mkt.get("currency", "GBP")),
                    "stop_level": pos.get("stopLevel"),
                    "limit_level": pos.get("limitLevel"),
                })

        # Activity (last 7 days)
        from_date = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%dT00:00:00")
        to_date = datetime.now().strftime("%Y-%m-%dT23:59:59")
        act_resp = s.get(f"{base}/history/activity",
                        params={"from": from_date, "to": to_date},
                        headers={**s.headers, "Version": "3"})
        activities = []
        if act_resp.status_code == 200:
            activities = act_resp.json().get("activities", [])

        # Don't logout — preserves web session
        return account_info, positions, activities

    except Exception as e:
        st.error(f"IG API error: {e}")
        return None, None, None


# ─── Sidebar ─────────────────────────────────────────────────────────────────

with st.sidebar:
    st.title("Trading Bot")
    st.caption("IBS++ | Trend Following | SPY/TLT Rotation")
    st.divider()

    # Auto-refresh toggle
    auto_refresh = st.toggle("Auto-refresh (30s)", value=False)
    if auto_refresh:
        st.caption("Dashboard refreshes every 30 seconds")
        time.sleep(0.1)  # Prevents immediate rerun race

    if st.button("Refresh Now", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

    st.divider()

    # Data source info
    st.caption(f"Database: {os.path.basename(DB_PATH)}")
    ig_connected = bool(os.getenv("IG_USERNAME"))
    if ig_connected:
        st.caption("IG API: Connected (Live)")
    else:
        st.caption("IG API: Not configured")


# ─── Fetch data ──────────────────────────────────────────────────────────────

account_info, live_positions, activities = fetch_ig_live_data()
db_trades = get_all_trades()
closed_trades = get_closed_trades()
snapshots = get_daily_snapshots()
strategy_stats = get_strategy_stats()
summary = get_summary()

# ─── Navigation (sidebar) — persists across reruns ───────────────────────────

TAB_NAMES = [
    "Overview", "Open Spreads", "Spread History", "Risk & Sizing", "Positions", "Trade History",
    "Strategy Performance", "Equity & Drawdown", "Performance Review", "Screener", "Backtester",
    "Bot Activity", "IG Activity",
]

with st.sidebar:
    st.markdown("### Navigation")
    selected_tab = st.radio("Go to", TAB_NAMES, key="nav_tab", label_visibility="collapsed")

# Create tab-like containers (only the selected one renders)
tab_overview = st.container() if selected_tab == "Overview" else None
tab_open_spreads = st.container() if selected_tab == "Open Spreads" else None
tab_spread_history = st.container() if selected_tab == "Spread History" else None
tab_risk = st.container() if selected_tab == "Risk & Sizing" else None
tab_positions = st.container() if selected_tab == "Positions" else None
tab_history = st.container() if selected_tab == "Trade History" else None
tab_strategies = st.container() if selected_tab == "Strategy Performance" else None
tab_equity = st.container() if selected_tab == "Equity & Drawdown" else None
tab_perf_review = st.container() if selected_tab == "Performance Review" else None
tab_screener = st.container() if selected_tab == "Screener" else None
tab_backtest = st.container() if selected_tab == "Backtester" else None
tab_bot_feed = st.container() if selected_tab == "Bot Activity" else None
tab_activity = st.container() if selected_tab == "IG Activity" else None

# ─── TAB: Overview ───────────────────────────────────────────────────────────

if tab_overview is not None:
 with tab_overview:
    st.header("Portfolio Overview")

    # Trading mode banner
    trading_mode = getattr(config, "TRADING_MODE", "shadow")
    if trading_mode == "shadow":
        st.info("🔵 **SHADOW MODE** — Bot logs signals but does NOT execute trades on IG")
    elif trading_mode == "live":
        st.success("🟢 **LIVE MODE** — Bot is executing real trades on IG")

    # Options safety status
    open_opts = get_open_option_positions()
    if open_opts:
        safety_col1, safety_col2, safety_col3 = st.columns(3)
        total_heat = sum(float(p.get("max_loss", 0)) * float(p.get("size", 1)) for p in open_opts)
        equity_val = account_info["balance"] if account_info else (summary["latest_balance"] or 5000)
        max_heat = equity_val * (config.OPTIONS_SAFETY.get("max_total_heat_pct", 4.0) / 100)
        with safety_col1:
            st.metric("Open Spreads", f"{len(open_opts)} / {config.OPTIONS_SAFETY.get('max_open_spreads', 6)}")
        with safety_col2:
            heat_pct = (total_heat / max_heat * 100) if max_heat > 0 else 0
            st.metric("Portfolio Heat", f"£{total_heat:,.0f} / £{max_heat:,.0f}", delta=f"{heat_pct:.0f}%")
        with safety_col3:
            st.metric("Mode", trading_mode.upper())
        st.divider()

    # Top-level metrics
    col1, col2, col3, col4 = st.columns(4)

    if account_info:
        balance = account_info["balance"]
        pnl = account_info["pnl"]
        available = account_info["available"]
        deposit = account_info["deposit"]

        with col1:
            st.metric("Account Balance", f"£{balance:,.2f}")
        with col2:
            st.metric("Unrealised P&L", f"£{pnl:,.2f}",
                      delta=f"£{pnl:,.2f}" if pnl != 0 else None,
                      delta_color="normal" if pnl >= 0 else "inverse")
        with col3:
            st.metric("Available Funds", f"£{available:,.2f}")
        with col4:
            st.metric("Margin Used", f"£{deposit:,.2f}")
    else:
        # Fallback to DB summary
        with col1:
            st.metric("Balance", f"£{summary['latest_balance']:,.2f}" if summary['latest_balance'] else "—")
        with col2:
            st.metric("Total P&L", f"£{summary['total_pnl']:,.2f}")
        with col3:
            st.metric("Today's P&L", f"£{summary['today_pnl']:,.2f}")
        with col4:
            st.metric("Open Positions", summary['open_positions'])

    st.divider()

    # Second row
    col5, col6, col7, col8 = st.columns(4)

    n_live_pos = len(live_positions) if live_positions else summary['open_positions']
    with col5:
        st.metric("Open Positions", n_live_pos)
    with col6:
        st.metric("Total Closed Trades", summary['total_closed_trades'])
    with col7:
        st.metric("Realised P&L", f"£{summary['total_pnl']:,.2f}")
    with col8:
        if strategy_stats:
            all_trades = sum(s["trades"] for s in strategy_stats.values())
            all_wins = sum(1 for t in closed_trades if t.get("pnl", 0) and t["pnl"] > 0)
            wr = round(all_wins / all_trades * 100, 1) if all_trades > 0 else 0
            st.metric("Win Rate", f"{wr}%")
        else:
            st.metric("Win Rate", "—")

    # Quick positions preview
    if live_positions:
        st.divider()
        st.subheader("Open Positions")
        pos_df = pd.DataFrame(live_positions)
        if not pos_df.empty:
            display_cols = ["market", "direction", "size", "open_level", "current_bid", "pnl"]
            available_cols = [c for c in display_cols if c in pos_df.columns]
            st.dataframe(
                pos_df[available_cols],
                use_container_width=True,
                hide_index=True,
                column_config={
                    "market": "Market",
                    "direction": "Dir",
                    "size": st.column_config.NumberColumn("Size", format="%.2f"),
                    "open_level": st.column_config.NumberColumn("Entry", format="%.2f"),
                    "current_bid": st.column_config.NumberColumn("Current", format="%.2f"),
                    "pnl": st.column_config.NumberColumn("P&L (£)", format="%.2f"),
                },
            )


# ─── TAB: Risk & Sizing ──────────────────────────────────────────────────────

if tab_risk is not None:
 with tab_risk:
    st.header("Risk Management & Position Sizing")

    from portfolio.risk import RISK_PARAMS, calc_position_size, get_portfolio_risk_summary

    # Get equity
    equity = account_info["balance"] + account_info["pnl"] if account_info else config.PORTFOLIO["initial_capital"]

    # ─── Portfolio Risk Gauges ────────────────────────────────────────────
    st.subheader("Portfolio Risk Status")

    if live_positions:
        risk_summary = get_portfolio_risk_summary(live_positions, equity)

        col1, col2, col3, col4 = st.columns(4)
        with col1:
            heat = risk_summary["portfolio_heat_pct"]
            st.metric("Portfolio Heat",
                      f"{heat:.1f}%",
                      delta=f"of {risk_summary['max_heat_pct']}% max",
                      delta_color="off")
        with col2:
            margin_util = risk_summary["margin_utilisation_pct"]
            st.metric("Margin Used",
                      f"{margin_util:.1f}%",
                      delta=f"of {risk_summary['max_margin_pct']}% max",
                      delta_color="off")
        with col3:
            st.metric("Risk Budget Left",
                      f"£{risk_summary['risk_budget_remaining']:,.0f}")
        with col4:
            st.metric("Margin Available",
                      f"£{risk_summary['margin_remaining']:,.0f}")

        # Progress bars
        st.divider()
        col_a, col_b = st.columns(2)
        with col_a:
            st.caption("Portfolio Heat")
            st.progress(min(heat / risk_summary["max_heat_pct"], 1.0))
        with col_b:
            st.caption("Margin Utilisation")
            st.progress(min(margin_util / risk_summary["max_margin_pct"], 1.0))

        # Per-position risk table
        st.divider()
        st.subheader("Risk per Position")
        if risk_summary["positions"]:
            risk_df = pd.DataFrame(risk_summary["positions"])
            st.dataframe(
                risk_df,
                use_container_width=True,
                hide_index=True,
                column_config={
                    "ticker": "Market",
                    "strategy": "Strategy",
                    "size": st.column_config.NumberColumn("Size (£/pt)", format="%.2f"),
                    "entry_price": st.column_config.NumberColumn("Entry", format="%.2f"),
                    "est_risk": st.column_config.NumberColumn("Est. Risk (£)", format="%.2f"),
                    "margin": st.column_config.NumberColumn("Margin (£)", format="%.2f"),
                    "risk_pct": st.column_config.NumberColumn("Risk % Equity", format="%.2f%%"),
                },
            )
    else:
        st.info("No open positions — risk metrics will appear when the bot opens trades.")

    # ─── Position Sizing Calculator ──────────────────────────────────────
    st.divider()
    st.subheader("Position Size Calculator")
    st.caption("See what the risk engine would size for any market right now")

    calc_col1, calc_col2, calc_col3 = st.columns(3)
    with calc_col1:
        all_tickers = list(config.MARKET_MAP.keys())
        calc_ticker = st.selectbox("Market", all_tickers)
    with calc_col2:
        strategies_list = ["IBS++ v3", "Trend Following v2", "SPY/TLT Rotation v3"]
        calc_strategy = st.selectbox("Strategy", strategies_list)
    with calc_col3:
        calc_equity = st.number_input("Equity (£)", value=equity, min_value=100.0, step=500.0)

    if st.button("Calculate Size", use_container_width=True):
        from data.provider import DataProvider as DP
        dp = DP(lookback_days=500)
        data_ticker = calc_ticker.replace("_trend", "")
        calc_df = dp.get_daily_bars(data_ticker)

        if not calc_df.empty:
            result = calc_position_size(
                ticker=calc_ticker,
                strategy_name=calc_strategy,
                df=calc_df,
                equity=calc_equity,
            )

            if result.stake_per_point > 0:
                res_col1, res_col2, res_col3, res_col4 = st.columns(4)
                with res_col1:
                    st.metric("Stake", f"£{result.stake_per_point:.2f}/pt")
                with res_col2:
                    st.metric("Risk", f"£{result.risk_amount:.2f}")
                with res_col3:
                    st.metric("Stop Distance", f"{result.stop_distance:.1f} pts")
                with res_col4:
                    st.metric("Margin Required", f"£{result.margin_required:.2f}")

                st.caption(
                    f"Stop type: {result.stop_type} | "
                    f"Risk: {result.risk_pct_of_equity:.1f}% of equity | "
                    f"Price: {calc_df['Close'].iloc[-1]:.2f} | "
                    f"Notes: {result.notes}"
                )
            else:
                st.warning(f"Trade blocked: {result.notes}")
        else:
            st.error(f"Could not fetch data for {data_ticker}")

    # ─── Risk Parameters ─────────────────────────────────────────────────
    st.divider()
    st.subheader("Risk Parameters")

    params_col1, params_col2 = st.columns(2)
    with params_col1:
        st.markdown(f"""
        **Per-Trade Limits:**
        - Risk per trade: **{RISK_PARAMS['risk_per_trade_pct']}%** of equity
        - With £{equity:,.0f} equity = **£{equity * RISK_PARAMS['risk_per_trade_pct'] / 100:,.0f}** max risk per trade

        **Position Sizing Formula:**
        - stake = (equity x risk%) / stop_distance
        - Stop distance based on ATR (adapts to volatility)
        """)
    with params_col2:
        st.markdown(f"""
        **Portfolio Limits:**
        - Max portfolio heat: **{RISK_PARAMS['max_portfolio_heat_pct']}%** (£{equity * RISK_PARAMS['max_portfolio_heat_pct'] / 100:,.0f})
        - Max single position margin: **{RISK_PARAMS['max_position_margin_pct']}%** (£{equity * RISK_PARAMS['max_position_margin_pct'] / 100:,.0f})
        - Max total margin: **{RISK_PARAMS['max_total_margin_pct']}%** (£{equity * RISK_PARAMS['max_total_margin_pct'] / 100:,.0f})
        - Min stake: **£{RISK_PARAMS['min_stake']:.2f}**/pt (IG minimum)
        """)

    st.caption("ATR stop multipliers — IBS++: 2.0x | Trend Following: 2.5x | Rotation: 3.0x")


# ─── TAB: Positions ─────────────────────────────────────────────────────────

if tab_positions is not None:
 with tab_positions:
    st.header("Live Positions")

    if live_positions:
        pos_df = pd.DataFrame(live_positions)
        if not pos_df.empty:
            # Summary row
            total_pnl = sum(p.get("pnl", 0) for p in live_positions)
            total_margin = sum(p.get("size", 0) * p.get("open_level", 0) * 0.05 for p in live_positions)

            col1, col2, col3 = st.columns(3)
            with col1:
                st.metric("Total Positions", len(live_positions))
            with col2:
                st.metric("Combined P&L", f"£{total_pnl:,.2f}",
                          delta=f"£{total_pnl:,.2f}" if total_pnl != 0 else None)
            with col3:
                st.metric("Est. Margin", f"£{total_margin:,.0f}")

            st.divider()

            # Full positions table
            st.dataframe(
                pos_df,
                use_container_width=True,
                hide_index=True,
                column_config={
                    "market": "Market",
                    "epic": "EPIC",
                    "direction": "Direction",
                    "size": st.column_config.NumberColumn("Size (£/pt)", format="%.2f"),
                    "open_level": st.column_config.NumberColumn("Entry Price", format="%.2f"),
                    "current_bid": st.column_config.NumberColumn("Bid", format="%.2f"),
                    "current_offer": st.column_config.NumberColumn("Offer", format="%.2f"),
                    "pnl": st.column_config.NumberColumn("P&L (£)", format="%.2f"),
                    "deal_id": "Deal ID",
                    "stop_level": st.column_config.NumberColumn("Stop", format="%.2f"),
                    "limit_level": st.column_config.NumberColumn("Limit", format="%.2f"),
                },
            )
        else:
            st.info("No open positions.")
    else:
        st.info("No live data available. Connect IG API credentials in .env to see live positions.")


# ─── TAB: Trade History ─────────────────────────────────────────────────────

if tab_history is not None:
 with tab_history:
    st.header("Trade History")

    if db_trades:
        trades_df = pd.DataFrame(db_trades)

        # Filters
        col1, col2, col3 = st.columns(3)
        with col1:
            strat_filter = st.selectbox("Strategy", ["All"] + list(trades_df["strategy"].unique()))
        with col2:
            action_filter = st.selectbox("Action", ["All", "OPEN", "CLOSE"])
        with col3:
            ticker_filter = st.selectbox("Market", ["All"] + list(trades_df["ticker"].unique()))

        # Apply filters
        filtered = trades_df.copy()
        if strat_filter != "All":
            filtered = filtered[filtered["strategy"] == strat_filter]
        if action_filter != "All":
            filtered = filtered[filtered["action"] == action_filter]
        if ticker_filter != "All":
            filtered = filtered[filtered["ticker"] == ticker_filter]

        st.dataframe(
            filtered,
            use_container_width=True,
            hide_index=True,
            column_config={
                "timestamp": "Time",
                "ticker": "Market",
                "strategy": "Strategy",
                "direction": "Direction",
                "action": "Action",
                "size": st.column_config.NumberColumn("Size", format="%.2f"),
                "price": st.column_config.NumberColumn("Price", format="%.2f"),
                "pnl": st.column_config.NumberColumn("P&L (£)", format="%.2f"),
                "deal_id": "Deal ID",
                "notes": "Notes",
            },
        )

        # P&L distribution chart (closed trades only)
        closed_df = trades_df[(trades_df["action"] == "CLOSE") & (trades_df["pnl"].notna())]
        if not closed_df.empty:
            st.divider()
            st.subheader("P&L Distribution")
            st.bar_chart(closed_df.set_index("timestamp")["pnl"], use_container_width=True)
    else:
        st.info("No trades recorded yet. The bot will log trades here as it runs.")


# ─── TAB: Strategy Performance ──────────────────────────────────────────────

if tab_strategies is not None:
 with tab_strategies:
    st.header("Strategy Performance")

    if strategy_stats:
        # Strategy comparison cards
        cols = st.columns(len(strategy_stats))
        for i, (strat, stats) in enumerate(strategy_stats.items()):
            with cols[i]:
                st.subheader(strat)
                st.metric("Total P&L", f"£{stats['total_pnl']:,.2f}")
                st.metric("Trades", stats["trades"])
                st.metric("Win Rate", f"{stats['win_rate']}%")
                st.metric("Profit Factor", f"{stats['profit_factor']:.2f}")
                st.metric("Avg Trade", f"£{stats['avg_pnl']:,.2f}")
                st.metric("Best Trade", f"£{stats['best_trade']:,.2f}")
                st.metric("Worst Trade", f"£{stats['worst_trade']:,.2f}")

        # Cumulative P&L by strategy chart
        st.divider()
        st.subheader("Cumulative P&L by Strategy")

        if closed_trades:
            closed_df = pd.DataFrame(closed_trades)
            closed_df["timestamp"] = pd.to_datetime(closed_df["timestamp"])
            closed_df = closed_df.sort_values("timestamp")

            # Build cumulative P&L per strategy using a single DataFrame to avoid duplicate index issues
            closed_df["trade_num"] = range(len(closed_df))
            cum_frames = []
            for strat in closed_df["strategy"].unique():
                strat_df = closed_df[closed_df["strategy"] == strat].copy()
                strat_df[strat] = strat_df["pnl"].cumsum()
                cum_frames.append(strat_df[["timestamp", "trade_num", strat]])

            if cum_frames:
                cum_df = cum_frames[0]
                for extra in cum_frames[1:]:
                    cum_df = pd.merge(cum_df, extra, on=["timestamp", "trade_num"], how="outer")
                cum_df = cum_df.sort_values("trade_num").set_index("timestamp")
                cum_df = cum_df.drop(columns=["trade_num"]).ffill()
                st.line_chart(cum_df, use_container_width=True)
    else:
        st.info("No strategy data yet. Performance stats will appear after the bot completes trades.")


# ─── TAB: Equity & Drawdown ─────────────────────────────────────────────────

if tab_equity is not None:
 with tab_equity:
    st.header("Equity Curve & Drawdown")

    if snapshots:
        snap_df = pd.DataFrame(snapshots)
        snap_df["date"] = pd.to_datetime(snap_df["date"])

        # Equity curve
        st.subheader("Equity Curve")
        st.line_chart(snap_df.set_index("date")["equity"], use_container_width=True)

        # Drawdown
        st.subheader("Drawdown (%)")
        st.area_chart(snap_df.set_index("date")["drawdown_pct"], use_container_width=True)

        # Daily P&L
        st.subheader("Daily Realised P&L")
        st.bar_chart(snap_df.set_index("date")["realised_pnl_today"], use_container_width=True)

        # Stats
        st.divider()
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            st.metric("Peak Equity", f"£{snap_df['equity'].max():,.2f}")
        with col2:
            st.metric("Current Equity", f"£{snap_df['equity'].iloc[-1]:,.2f}")
        with col3:
            st.metric("Max Drawdown", f"{snap_df['drawdown_pct'].min():.1f}%")
        with col4:
            days = len(snap_df)
            st.metric("Trading Days", days)
    else:
        st.info("No daily snapshots yet. The bot saves a snapshot at the end of each trading day.")

        # If we have account info, show starting point
        if account_info:
            st.caption(f"Current account balance: £{account_info['balance']:,.2f}")


# ─── TAB: Performance Review ────────────────────────────────────────────────

if tab_perf_review is not None:
 with tab_perf_review:
    st.header("Performance Review")
    st.caption("Institutional-grade analytics — weekly report, strategy decay, P&L attribution")

    from analytics.performance import PerformanceAnalyser
    pa = PerformanceAnalyser()

    # ─── Weekly Report ────────────────────────────────────────────────
    st.subheader("Weekly Report")
    weeks_back = st.selectbox("Report period", [1, 2, 4], format_func=lambda x: f"Last {x} week(s)")
    report = pa.weekly_report(weeks_back=weeks_back)

    # Period summary
    rc1, rc2, rc3, rc4 = st.columns(4)
    with rc1:
        st.metric("Period P&L", f"£{report.period_pnl:,.2f}",
                  delta=f"{report.period_return_pct:+.2f}%")
    with rc2:
        st.metric("Trades Closed", report.trades_closed)
    with rc3:
        st.metric("Signals / Rejections", f"{report.signals_generated} / {report.signals_rejected}")
    with rc4:
        st.metric("Current Drawdown", f"{report.current_drawdown_pct:.1f}%")

    rc5, rc6, rc7, rc8 = st.columns(4)
    with rc5:
        st.metric("Sharpe (30d)", f"{report.sharpe_30d:.2f}")
    with rc6:
        st.metric("Sharpe (90d)", f"{report.sharpe_90d:.2f}")
    with rc7:
        st.metric("Sortino (30d)", f"{report.sortino_30d:.2f}")
    with rc8:
        st.metric("Max Drawdown", f"{report.max_drawdown_pct:.1f}%")

    # Alerts
    if report.alerts:
        st.divider()
        for alert in report.alerts:
            if "WARNING" in alert or "negative" in alert:
                st.error(alert)
            else:
                st.warning(alert)

    # Per-strategy metrics
    if report.strategy_metrics:
        st.divider()
        st.subheader("Strategy Metrics (All Time)")

        for strat_name, metrics in report.strategy_metrics.items():
            with st.expander(f"{strat_name} — {metrics.trades} trades, £{metrics.total_pnl:,.2f} P&L", expanded=False):
                sc1, sc2, sc3, sc4 = st.columns(4)
                with sc1:
                    st.metric("Win Rate", f"{metrics.win_rate:.1f}%")
                    st.metric("Avg Win", f"£{metrics.avg_win:,.2f}")
                with sc2:
                    st.metric("Profit Factor", f"{metrics.profit_factor:.2f}")
                    st.metric("Avg Loss", f"£{metrics.avg_loss:,.2f}")
                with sc3:
                    st.metric("Sharpe", f"{metrics.sharpe:.2f}")
                    st.metric("Payoff Ratio", f"{metrics.payoff_ratio:.2f}")
                with sc4:
                    st.metric("Sortino", f"{metrics.sortino:.2f}")
                    st.metric("Max Consec. Losses", metrics.max_consecutive_losses)

                st.caption(
                    f"Expectancy: {metrics.expectancy_r:.3f}R | "
                    f"Recovery Factor: {metrics.recovery_factor:.2f} | "
                    f"Best: £{metrics.best_trade:,.2f} | Worst: £{metrics.worst_trade:,.2f}"
                )

    # P&L by market
    if report.pnl_by_market:
        st.divider()
        st.subheader("P&L Attribution by Market (Period)")
        mkt_df = pd.DataFrame(
            [(k, v) for k, v in report.pnl_by_market.items()],
            columns=["Market", "P&L (£)"],
        )
        st.bar_chart(mkt_df.set_index("Market"), use_container_width=True)

    # Strategy Decay
    st.divider()
    st.subheader("Strategy Decay Monitor")
    st.caption("Compares rolling Sharpe ratio against baseline to detect edge erosion")

    decay_reports = pa.strategy_decay()
    if decay_reports:
        for dr in decay_reports:
            status_color = {"healthy": "🟢", "warning": "🟡", "decaying": "🔴"}.get(dr.status, "⚪")
            with st.expander(f"{status_color} {dr.strategy} — {dr.status.upper()} (decay: {dr.decay_pct:.1f}%)", expanded=dr.status != "healthy"):
                dc1, dc2, dc3 = st.columns(3)
                with dc1:
                    st.metric("Baseline Sharpe", f"{dr.baseline_sharpe:.3f}")
                with dc2:
                    st.metric("Current Rolling Sharpe", f"{dr.current_rolling_sharpe:.3f}")
                with dc3:
                    st.metric("Decay", f"{dr.decay_pct:.1f}%")

                if dr.rolling_sharpe_history and len(dr.rolling_sharpe_history) >= 2:
                    hist_df = pd.DataFrame(dr.rolling_sharpe_history, columns=["date", "sharpe"])
                    hist_df["date"] = pd.to_datetime(hist_df["date"])
                    st.line_chart(hist_df.set_index("date")["sharpe"], use_container_width=True)
    else:
        st.info("Not enough trade data yet for decay analysis. Need at least 10 closed trades per strategy.")


# ─── TAB: Screener ─────────────────────────────────────────────────────────

if tab_screener is not None:
 with tab_screener:
    st.header("Strategy Screener")
    st.caption("Automated scan of all markets — run once, see what's viable for live trading")

    from analytics.screener import (
        SCREENER_DB, init_screener_db, run_screener,
        COST_MODE_ZERO, COST_MODE_REALISTIC,
    )

    screener_db_path = SCREENER_DB

    # ── Run controls ─────────────────────────────────────────────
    scr_col1, scr_col2, scr_col3 = st.columns(3)
    with scr_col1:
        scr_lookback = st.selectbox(
            "Lookback", [1000, 2000, 3000, 5000, 0],
            index=1,
            format_func=lambda x: "Max available" if x == 0 else f"{x} days (~{x//365}y)",
            key="scr_lookback",
        )
    with scr_col2:
        scr_include_candidates = st.checkbox("Include candidate markets", value=False, key="scr_cand")
    with scr_col3:
        scr_run = st.button("Run Full Screener", type="primary", use_container_width=True,
                            help="Scans all strategies × all markets with zero + realistic costs. Takes a few minutes.")

    if scr_run:
        with st.spinner("Running screener across all strategies and markets... this will take a few minutes"):
            try:
                results = run_screener(
                    lookback=scr_lookback,
                    include_candidates=scr_include_candidates,
                    db_path=screener_db_path,
                )
                st.session_state.scr_ran = True
                st.success(f"Screener complete — {len(results)} markets assessed")
            except Exception as e:
                st.error(f"Screener failed: {e}")

    # ── Display stored results ───────────────────────────────────
    import sqlite3 as _sqlite3

    if os.path.exists(screener_db_path):
        scr_conn = _sqlite3.connect(screener_db_path)
        scr_conn.row_factory = _sqlite3.Row

        # Get available run dates
        run_dates = [r[0] for r in scr_conn.execute(
            "SELECT DISTINCT run_date FROM screener_runs ORDER BY run_date DESC"
        ).fetchall()]

        if run_dates:
            scr_filter_col1, scr_filter_col2, scr_filter_col3 = st.columns(3)
            with scr_filter_col1:
                selected_run = st.selectbox("Run date", run_dates, key="scr_run_date")
            with scr_filter_col2:
                scr_cost_view = st.selectbox(
                    "Cost mode", ["realistic", "zero", "both"],
                    key="scr_cost_view",
                    help="Realistic = what you'd pay on IG. Zero = raw edge (Pine Script compatible).",
                )
            with scr_filter_col3:
                scr_viable_filter = st.selectbox(
                    "Filter", ["All", "Viable only", "Viable + Marginal"],
                    key="scr_viable_filter",
                )

            # Build query
            where_clauses = ["run_date = ?"]
            params = [selected_run]

            if scr_cost_view != "both":
                where_clauses.append("cost_mode = ?")
                params.append(scr_cost_view)

            if scr_viable_filter == "Viable only":
                where_clauses.append("viable = 'YES'")
            elif scr_viable_filter == "Viable + Marginal":
                where_clauses.append("viable IN ('YES', 'MARGINAL')")

            query = f"""
                SELECT strategy, ticker, product_type, category, cost_mode,
                       total_trades, win_rate, gross_pnl, net_pnl,
                       spread_cost, financing_cost,
                       profit_factor_gross, profit_factor_net,
                       sharpe, sortino, max_drawdown_pct,
                       avg_bars_held, expectancy_r,
                       cost_drag_pct, annual_return_pct, viable
                FROM screener_runs
                WHERE {' AND '.join(where_clauses)}
                ORDER BY
                    CASE viable WHEN 'YES' THEN 0 WHEN 'MARGINAL' THEN 1 ELSE 2 END,
                    sharpe DESC
            """
            rows = scr_conn.execute(query, params).fetchall()

            if rows:
                # Summary counts
                yes_count = sum(1 for r in rows if r["viable"] == "YES")
                marginal_count = sum(1 for r in rows if r["viable"] == "MARGINAL")
                no_count = sum(1 for r in rows if r["viable"] == "NO")

                sum_col1, sum_col2, sum_col3, sum_col4 = st.columns(4)
                with sum_col1:
                    st.metric("Viable", yes_count)
                with sum_col2:
                    st.metric("Marginal", marginal_count)
                with sum_col3:
                    st.metric("Not Viable", no_count)
                with sum_col4:
                    total_net = sum(r["net_pnl"] for r in rows if r["viable"] == "YES" and r["cost_mode"] == "realistic")
                    st.metric("Combined Net (viable)", f"£{total_net:,.2f}")

                st.divider()

                # Main results table
                scr_df = pd.DataFrame([dict(r) for r in rows])

                # Add verdict emoji column
                verdict_map = {"YES": "✅ YES", "MARGINAL": "⚠️ MARGINAL", "NO": "❌ NO"}
                scr_df["verdict"] = scr_df["viable"].map(verdict_map)

                display_cols = [
                    "verdict", "strategy", "ticker", "product_type", "cost_mode",
                    "total_trades", "win_rate", "gross_pnl", "net_pnl",
                    "spread_cost", "financing_cost", "cost_drag_pct",
                    "profit_factor_gross", "profit_factor_net",
                    "sharpe", "max_drawdown_pct", "avg_bars_held",
                ]

                st.dataframe(
                    scr_df[display_cols],
                    use_container_width=True,
                    hide_index=True,
                    column_config={
                        "verdict": st.column_config.TextColumn("Verdict", width="medium"),
                        "strategy": st.column_config.TextColumn("Strategy", width="medium"),
                        "ticker": st.column_config.TextColumn("Ticker", width="small"),
                        "product_type": st.column_config.TextColumn("Type", width="small"),
                        "cost_mode": st.column_config.TextColumn("Costs", width="small"),
                        "total_trades": st.column_config.NumberColumn("Trades", format="%d"),
                        "win_rate": st.column_config.NumberColumn("Win %", format="%.1f%%"),
                        "gross_pnl": st.column_config.NumberColumn("Gross P&L", format="£%.2f"),
                        "net_pnl": st.column_config.NumberColumn("Net P&L", format="£%.2f"),
                        "spread_cost": st.column_config.NumberColumn("Spread", format="£%.2f"),
                        "financing_cost": st.column_config.NumberColumn("Financing", format="£%.2f"),
                        "cost_drag_pct": st.column_config.NumberColumn("Cost Drag %", format="%.1f%%"),
                        "profit_factor_gross": st.column_config.NumberColumn("PF (gross)", format="%.2f"),
                        "profit_factor_net": st.column_config.NumberColumn("PF (net)", format="%.2f"),
                        "sharpe": st.column_config.NumberColumn("Sharpe", format="%.2f"),
                        "max_drawdown_pct": st.column_config.NumberColumn("Max DD %", format="%.1f%%"),
                        "avg_bars_held": st.column_config.NumberColumn("Avg Bars", format="%.1f"),
                    },
                )

                # ── Charts ───────────────────────────────────────────
                # Only show charts for realistic cost results
                realistic_rows = [r for r in rows if r["cost_mode"] == "realistic"]
                if realistic_rows:
                    st.divider()

                    # Cost breakdown
                    st.markdown("#### Cost Breakdown by Market (Realistic)")
                    cost_chart_df = pd.DataFrame([
                        {"Market": f"{r['ticker']} ({r['strategy'][:6]})",
                         "Spread": r["spread_cost"], "Financing": r["financing_cost"]}
                        for r in realistic_rows
                    ])
                    st.bar_chart(cost_chart_df.set_index("Market"), use_container_width=True)

                    # Net P&L comparison
                    st.markdown("#### Net P&L by Market (Realistic)")
                    pnl_chart_df = pd.DataFrame([
                        {"Market": f"{r['ticker']} ({r['strategy'][:6]})",
                         "Net P&L": r["net_pnl"]}
                        for r in realistic_rows
                    ])
                    st.bar_chart(pnl_chart_df.set_index("Market"), use_container_width=True)

                    # Sharpe comparison
                    st.markdown("#### Sharpe Ratio by Market (Realistic)")
                    sharpe_chart_df = pd.DataFrame([
                        {"Market": f"{r['ticker']} ({r['strategy'][:6]})",
                         "Sharpe": r["sharpe"]}
                        for r in realistic_rows
                    ])
                    st.bar_chart(sharpe_chart_df.set_index("Market"), use_container_width=True)

                    # ── Recommended Portfolio ────────────────────────
                    viable_rows = [r for r in realistic_rows if r["viable"] == "YES"]
                    if viable_rows:
                        st.divider()
                        st.markdown("#### Recommended Portfolio")
                        st.caption("Markets that pass all viability filters — ready for live trading")
                        for r in viable_rows:
                            st.markdown(
                                f"- **{r['ticker']}** ({r['strategy']}) — "
                                f"PF={r['profit_factor_net']:.2f}, Sharpe={r['sharpe']:.2f}, "
                                f"Win={r['win_rate']:.0f}%, Cost drag={r['cost_drag_pct']:.0f}%, "
                                f"{r['total_trades']} trades"
                            )
                        combined_net = sum(r["net_pnl"] for r in viable_rows)
                        st.success(f"Combined net P&L across viable markets: £{combined_net:,.2f} (on £10k per market)")
                    else:
                        st.warning(
                            "No markets currently meet all viability criteria. "
                            "Check MARGINAL markets or consider adjusting strategy parameters."
                        )
            else:
                st.info("No results match your filters.")

        else:
            st.info("No screener results stored yet. Click 'Run Full Screener' above, or run from terminal: `python3 -m analytics.screener`")

        scr_conn.close()
    else:
        st.info("No screener database found. Click 'Run Full Screener' above, or run from terminal: `python3 -m analytics.screener`")


# ─── TAB: Backtester ───────────────────────────────────────────────────────

if tab_backtest is not None:
 with tab_backtest:
    st.header("Automated Backtester")
    st.caption("Run backtests using the same strategies as the live bot — no TradingView needed")

    from analytics.backtester import (
        Backtester, COST_MODE_ZERO, COST_MODE_REALISTIC, COST_MODE_CUSTOM,
        ENTRY_AT_CLOSE, ENTRY_AT_NEXT_OPEN,
    )

    # ─── Strategy & settings ──────────────────────────────────────────
    bt_col1, bt_col2, bt_col3 = st.columns(3)
    with bt_col1:
        bt_strategy = st.selectbox("Strategy", [
            "IBS++ v3", "IBS Short (Bear)", "IBS++ Futures",
            "SPY/TLT Rotation v4", "SPY/TLT Rotation v3",
            "Trend Following v2 [DEPRECATED]",
        ], key="bt_strat")
    with bt_col2:
        bt_equity = st.number_input("Starting Equity (£)", value=10000.0, min_value=1000.0, step=1000.0, key="bt_eq")
    with bt_col3:
        bt_lookback = st.selectbox("Data Lookback",
                                    [365, 500, 750, 1000, 1500, 2000, 3000, 5000, 0],
                                    index=3,
                                    format_func=lambda x: "Max available" if x == 0 else f"{x} days (~{x//365}y {(x%365)//30}m)",
                                    key="bt_lookback")

    # ─── Cost & execution settings ────────────────────────────────────
    cost_col1, cost_col2 = st.columns(2)
    with cost_col1:
        bt_cost_mode = st.selectbox(
            "Cost Mode",
            [COST_MODE_ZERO, COST_MODE_REALISTIC, COST_MODE_CUSTOM],
            index=1,
            format_func=lambda x: {
                COST_MODE_ZERO: "Zero costs (Pine Script compatible)",
                COST_MODE_REALISTIC: "Realistic (IG spreads + historical financing)",
                COST_MODE_CUSTOM: "Custom (set your own rates)",
            }.get(x, x),
            key="bt_cost_mode",
            help="Zero = matches TradingView results exactly. Realistic = what you'd actually pay on IG.",
        )
    with cost_col2:
        bt_entry_timing = st.selectbox(
            "Entry Timing",
            [ENTRY_AT_NEXT_OPEN, ENTRY_AT_CLOSE],
            index=0,
            format_func=lambda x: {
                ENTRY_AT_NEXT_OPEN: "Next bar open (Pine Script default)",
                ENTRY_AT_CLOSE: "Signal bar close",
            }.get(x, x),
            key="bt_entry_timing",
            help="Pine Script default is next bar open. Signal bar close gives slightly different results.",
        )

    # ─── Market selection — checkbox grid (easier to toggle than multiselect) ─
    strat_markets = config.BACKTEST_MARKETS.get(bt_strategy, {"proven": {}, "candidates": {}})
    proven_markets = strat_markets.get("proven", {})
    candidate_markets = strat_markets.get("candidates", {})

    st.divider()

    # Quick select/deselect buttons
    mkt_btn_col1, mkt_btn_col2, mkt_btn_col3 = st.columns(3)
    with mkt_btn_col1:
        select_all_proven = st.button("Select all proven", key="sel_all_proven")
    with mkt_btn_col2:
        deselect_all = st.button("Deselect all", key="desel_all")
    with mkt_btn_col3:
        select_all_candidates = st.button("Select all candidates", key="sel_all_cand")

    # Handle bulk selection buttons via session state
    if select_all_proven:
        for t in proven_markets:
            st.session_state[f"mkt_{t}"] = True
        for t in candidate_markets:
            st.session_state[f"mkt_{t}"] = False
    if deselect_all:
        for t in list(proven_markets) + list(candidate_markets):
            st.session_state[f"mkt_{t}"] = False
    if select_all_candidates:
        for t in candidate_markets:
            st.session_state[f"mkt_{t}"] = True

    # Proven markets — checkboxes in columns (default ON)
    st.markdown("**Proven Markets** — live on the bot, backtested & verified")
    bt_proven_selected = []
    proven_tickers = list(proven_markets.keys())
    n_cols = min(4, len(proven_tickers))
    if n_cols > 0:
        cols = st.columns(n_cols)
        for idx, t in enumerate(proven_tickers):
            default_val = st.session_state.get(f"mkt_{t}", True)
            with cols[idx % n_cols]:
                checked = st.checkbox(f"**{t}**", value=default_val, key=f"mkt_{t}",
                                      help=proven_markets[t])
                if checked:
                    bt_proven_selected.append(t)

    # Candidate markets — checkboxes in columns (default OFF)
    st.markdown("**Candidate Markets** — untested, fit strategy criteria")
    bt_candidate_selected = []
    candidate_tickers = list(candidate_markets.keys())
    n_cols_c = min(4, max(len(candidate_tickers), 1))
    if candidate_tickers:
        cols_c = st.columns(n_cols_c)
        for idx, t in enumerate(candidate_tickers):
            default_val = st.session_state.get(f"mkt_{t}", False)
            with cols_c[idx % n_cols_c]:
                checked = st.checkbox(f"{t}", value=default_val, key=f"mkt_{t}",
                                      help=candidate_markets[t])
                if checked:
                    bt_candidate_selected.append(t)

    # Combine selections
    bt_tickers = bt_proven_selected + bt_candidate_selected

    if not bt_tickers:
        st.warning("Select at least one market to backtest.")

    # Summary
    st.markdown(
        f"**Running:** {len(bt_proven_selected)} proven + {len(bt_candidate_selected)} candidates = "
        f"**{len(bt_tickers)} markets**"
    )

    st.divider()

    # ─── Run buttons ──────────────────────────────────────────────────
    bt_run_col1, bt_run_col2, bt_run_col3 = st.columns(3)
    run_backtest = bt_run_col1.button("Run Backtest", use_container_width=True, type="primary",
                                       disabled=len(bt_tickers) == 0)
    run_walkforward = bt_run_col2.button("Walk-Forward Validation", use_container_width=True,
                                          disabled=len(bt_tickers) == 0)
    run_montecarlo = bt_run_col3.button("Monte Carlo (after backtest)", use_container_width=True)

    # Store results in session state
    if "bt_result" not in st.session_state:
        st.session_state.bt_result = None
    if "wf_result" not in st.session_state:
        st.session_state.wf_result = None
    if "mc_result" not in st.session_state:
        st.session_state.mc_result = None

    def _make_backtester(lookback=bt_lookback):
        return Backtester(
            equity=bt_equity,
            lookback_days=lookback,
            cost_mode=bt_cost_mode,
            entry_timing=bt_entry_timing,
        )

    if run_backtest and bt_tickers:
        with st.spinner(f"Running {bt_strategy} backtest across {len(bt_tickers)} markets..."):
            bt = _make_backtester()
            st.session_state.bt_result = bt.run(bt_strategy, tickers=bt_tickers)
            st.session_state.mc_result = None

    if run_walkforward and bt_tickers:
        with st.spinner(f"Running walk-forward validation (4 windows)..."):
            bt = _make_backtester()
            st.session_state.wf_result = bt.walk_forward(bt_strategy, tickers=bt_tickers)

    if run_montecarlo and st.session_state.bt_result:
        with st.spinner("Running 2,000 Monte Carlo simulations..."):
            bt = _make_backtester()
            st.session_state.mc_result = bt.monte_carlo(st.session_state.bt_result.trades)

    # ─── Display backtest results ─────────────────────────────────────
    res = st.session_state.bt_result
    if res:
        st.divider()
        st.subheader(f"Backtest Results: {res.strategy}")
        st.caption(f"{res.period_start} → {res.period_end} | {len(res.tickers)} markets | {res.total_trades} trades")

        # ── Portfolio Aggregate ──────────────────────────────────────
        st.markdown("#### Portfolio Aggregate")
        total_costs = res.total_spread_cost + res.total_financing
        cost_pct_of_gross = (total_costs / abs(res.gross_pnl) * 100) if res.gross_pnl != 0 else 0

        r1, r2, r3, r4, r5 = st.columns(5)
        with r1:
            color = "normal" if res.net_pnl >= 0 else "inverse"
            st.metric("Net P&L", f"£{res.net_pnl:,.2f}", delta=f"{res.total_return_pct:+.1f}%", delta_color=color)
        with r2:
            st.metric("Win Rate", f"{res.win_rate:.1f}%", delta=f"{res.total_trades} trades")
        with r3:
            pf_label = f"{res.profit_factor:.2f}"
            pf_gross_str = f"gross {res.profit_factor_gross:.2f}" if hasattr(res, 'profit_factor_gross') and res.profit_factor_gross else ""
            st.metric("Profit Factor", pf_label, delta=pf_gross_str)
        with r4:
            st.metric("Sharpe / Sortino", f"{res.sharpe:.2f} / {res.sortino:.2f}")
        with r5:
            st.metric("Max Drawdown", f"{res.max_drawdown_pct:.1f}%",
                      delta=f"£{total_costs:,.0f} costs ({cost_pct_of_gross:.0f}% of gross)" if total_costs > 0 else "zero costs",
                      delta_color="inverse" if total_costs > 0 else "off")

        # ── Per-Market Comparison Table ──────────────────────────────
        if hasattr(res, 'stats_by_market') and res.stats_by_market:
            st.divider()
            st.markdown("#### Market Comparison")

            # Build comparison dataframe
            comp_rows = []
            for ticker in sorted(res.stats_by_market.keys()):
                s = res.stats_by_market[ticker]
                comp_rows.append({
                    "Market": ticker,
                    "Type": s["product_type"],
                    "Trades": s["trades"],
                    "Win %": s["win_rate"],
                    "Gross P&L": s["gross_pnl"],
                    "Spread": s["spread_cost"],
                    "Financing": s["financing"],
                    "Net P&L": s["net_pnl"],
                    "PF": s["profit_factor"],
                    "Sharpe": s["sharpe"],
                    "Avg Bars": s["avg_bars"],
                    "Avg R": s["avg_r"],
                })

            comp_df = pd.DataFrame(comp_rows)

            # Styled dataframe with column formatting
            st.dataframe(
                comp_df,
                use_container_width=True,
                hide_index=True,
                column_config={
                    "Market": st.column_config.TextColumn("Market", width="small"),
                    "Type": st.column_config.TextColumn("Type", width="small",
                        help="dfb=Daily Funded Bet (overnight financing), future=no financing, fx=tom-next, com_spot=basis/roll"),
                    "Trades": st.column_config.NumberColumn("Trades", format="%d"),
                    "Win %": st.column_config.NumberColumn("Win %", format="%.1f%%"),
                    "Gross P&L": st.column_config.NumberColumn("Gross P&L", format="£%.2f"),
                    "Spread": st.column_config.NumberColumn("Spread", format="£%.2f"),
                    "Financing": st.column_config.NumberColumn("Financing", format="£%.2f",
                        help="Overnight financing cost. Futures should show £0.00"),
                    "Net P&L": st.column_config.NumberColumn("Net P&L", format="£%.2f"),
                    "PF": st.column_config.NumberColumn("PF", format="%.2f",
                        help="Profit Factor (net). >1.5 is good, >2.0 is strong"),
                    "Sharpe": st.column_config.NumberColumn("Sharpe", format="%.2f"),
                    "Avg Bars": st.column_config.NumberColumn("Avg Bars", format="%.1f"),
                    "Avg R": st.column_config.NumberColumn("Avg R", format="%.3f"),
                },
            )

            # ── Cost Breakdown Chart ─────────────────────────────────
            st.divider()
            st.markdown("#### Cost Breakdown by Market")
            st.caption("Spread (blue) vs Overnight Financing (orange) — futures should show zero financing")

            cost_df = pd.DataFrame([
                {"Market": t, "Spread": res.stats_by_market[t]["spread_cost"],
                 "Financing": res.stats_by_market[t]["financing"]}
                for t in sorted(res.stats_by_market.keys())
            ])
            if not cost_df.empty:
                st.bar_chart(cost_df.set_index("Market"), use_container_width=True)

            # ── Gross vs Net P&L Chart ───────────────────────────────
            st.markdown("#### Gross vs Net P&L by Market")
            st.caption("The gap between bars shows cost impact — wider gap = more expensive to trade")

            pnl_comp_df = pd.DataFrame([
                {"Market": t, "Gross P&L": res.stats_by_market[t]["gross_pnl"],
                 "Net P&L": res.stats_by_market[t]["net_pnl"]}
                for t in sorted(res.stats_by_market.keys())
            ])
            if not pnl_comp_df.empty:
                st.bar_chart(pnl_comp_df.set_index("Market"), use_container_width=True)

            # ── Normalised Equity Curves ─────────────────────────────
            st.divider()
            st.markdown("#### Normalised Equity Curves (rebased to 100)")
            st.caption("Each market's cumulative P&L shown independently — steeper = better risk-adjusted returns")

            # Build a wide dataframe: date index, one column per market
            eq_frames = []
            for ticker, s in res.stats_by_market.items():
                if s.get("equity_curve"):
                    mkt_eq = pd.DataFrame(s["equity_curve"], columns=["date", ticker])
                    mkt_eq["date"] = pd.to_datetime(mkt_eq["date"])
                    mkt_eq = mkt_eq.set_index("date")
                    eq_frames.append(mkt_eq)

            if eq_frames:
                # Merge all on date (outer join so all dates present)
                merged_eq = eq_frames[0]
                for f in eq_frames[1:]:
                    merged_eq = merged_eq.join(f, how="outer")
                merged_eq = merged_eq.sort_index().ffill()
                st.line_chart(merged_eq, use_container_width=True)

        # ── Portfolio Equity Curve ───────────────────────────────────
        if res.equity_curve:
            st.divider()
            st.markdown("#### Portfolio Equity Curve (combined)")
            eq_df = pd.DataFrame(res.equity_curve, columns=["date", "equity"])
            eq_df["date"] = pd.to_datetime(eq_df["date"])
            st.line_chart(eq_df.set_index("date")["equity"], use_container_width=True)

        # ── Trade Log (collapsible) ──────────────────────────────────
        if res.trades:
            with st.expander(f"Trade Log ({len(res.trades)} trades)", expanded=False):
                trade_data = [
                    {
                        "Entry": t.entry_date, "Exit": t.exit_date,
                        "Ticker": t.ticker, "Dir": t.direction,
                        "Entry £": t.entry_price, "Exit £": t.exit_price,
                        "Bars": t.bars_held, "Gross": t.pnl_gross,
                        "Spread": t.spread_cost, "Fin.": t.financing_cost,
                        "Net": t.pnl_net, "R": t.r_multiple,
                        "Reason": t.exit_reason,
                    }
                    for t in res.trades
                ]
                st.dataframe(pd.DataFrame(trade_data), use_container_width=True, hide_index=True)

    # ─── Display walk-forward results ─────────────────────────────────
    wf = st.session_state.wf_result
    if wf:
        st.divider()
        st.subheader("Walk-Forward Validation")
        status_icon = {"robust": "🟢", "marginal": "🟡", "overfitted": "🔴"}.get(wf.status, "⚪")
        st.markdown(f"**Status: {status_icon} {wf.status.upper()}**")

        wc1, wc2, wc3, wc4 = st.columns(4)
        with wc1:
            st.metric("In-Sample Sharpe", f"{wf.in_sample_sharpe:.2f}")
        with wc2:
            st.metric("Out-of-Sample Sharpe", f"{wf.out_of_sample_sharpe:.2f}")
        with wc3:
            st.metric("Sharpe Degradation", f"{wf.sharpe_degradation_pct:.1f}%")
        with wc4:
            st.metric("OOS Win Rate", f"{wf.oos_win_rate:.1f}%")

        st.caption(f"OOS trades: {wf.total_oos_trades} | OOS P&L: £{wf.total_oos_pnl:,.2f}")

    # ─── Display Monte Carlo results ──────────────────────────────────
    mc = st.session_state.mc_result
    if mc and mc.simulations > 0:
        st.divider()
        st.subheader("Monte Carlo Simulation")
        st.caption(f"{mc.simulations:,} random trade-sequence simulations")

        mc1, mc2, mc3, mc4 = st.columns(4)
        with mc1:
            st.metric("Median Final P&L", f"£{mc.median_final_pnl:,.2f}")
        with mc2:
            st.metric("Prob. Profitable", f"{mc.prob_profitable:.1f}%")
        with mc3:
            st.metric("5th Percentile", f"£{mc.percentile_5:,.2f}")
        with mc4:
            st.metric("95th Percentile", f"£{mc.percentile_95:,.2f}")

        st.caption(
            f"Worst-case max DD: £{mc.worst_max_drawdown:,.2f} | "
            f"Median max DD: £{mc.median_max_drawdown:,.2f} | "
            f"25th–75th range: £{mc.percentile_25:,.2f} to £{mc.percentile_75:,.2f}"
        )


# ─── TAB: Bot Activity Feed ─────────────────────────────────────────────────

if tab_bot_feed is not None:
 with tab_bot_feed:
    st.header("Bot Activity Feed")
    st.caption("What the bot has been doing while you were away")

    # Fetch events
    bot_events = get_bot_events(limit=500)

    if bot_events:
        # ─── Filter controls ──────────────────────────────────────────────
        filter_col1, filter_col2, filter_col3 = st.columns(3)

        all_categories = sorted(set(e["category"] for e in bot_events))
        with filter_col1:
            cat_filter = st.multiselect(
                "Category",
                options=all_categories,
                default=[c for c in all_categories if c != "HEARTBEAT"],
                help="Filter by event type. Heartbeats hidden by default.",
            )
        with filter_col2:
            time_options = {"Last hour": 1, "Last 6 hours": 6, "Last 24 hours": 24,
                           "Last 3 days": 72, "Last 7 days": 168, "All time": 999999}
            time_filter = st.selectbox("Time range", list(time_options.keys()), index=2)
        with filter_col3:
            all_tickers = sorted(set(e["ticker"] for e in bot_events if e.get("ticker")))
            ticker_filter_bot = st.selectbox("Market", ["All"] + all_tickers, key="bot_ticker")

        # Apply filters
        cutoff = datetime.now() - timedelta(hours=time_options[time_filter])
        filtered_events = []
        for e in bot_events:
            if e["category"] not in cat_filter:
                continue
            try:
                evt_time = datetime.fromisoformat(e["timestamp"])
            except (ValueError, TypeError):
                continue
            if evt_time < cutoff:
                continue
            if ticker_filter_bot != "All" and e.get("ticker") != ticker_filter_bot:
                continue
            filtered_events.append(e)

        # ─── Summary counters ─────────────────────────────────────────────
        counter_cols = st.columns(6)
        cat_counts = {}
        for e in filtered_events:
            cat_counts[e["category"]] = cat_counts.get(e["category"], 0) + 1

        important_cats = [
            ("ORDER", "Orders Filled"),
            ("SIGNAL", "Signals Detected"),
            ("REJECTION", "Rejections"),
            ("ERROR", "Errors"),
            ("SCAN", "Scans Run"),
            ("POSITION", "Position Events"),
        ]
        for i, (cat, label) in enumerate(important_cats):
            with counter_cols[i]:
                count = cat_counts.get(cat, 0)
                st.metric(label, count)

        st.divider()

        # ─── Timeline view ────────────────────────────────────────────────
        if not filtered_events:
            st.info("No events match your filters.")
        else:
            # Group by date
            events_by_date = {}
            for e in filtered_events:
                try:
                    evt_date = datetime.fromisoformat(e["timestamp"]).strftime("%A, %d %B %Y")
                except (ValueError, TypeError):
                    evt_date = "Unknown"
                events_by_date.setdefault(evt_date, []).append(e)

            for day, day_events in events_by_date.items():
                st.subheader(day)

                for e in day_events:
                    icon = e.get("icon", "")
                    try:
                        time_str = datetime.fromisoformat(e["timestamp"]).strftime("%H:%M:%S")
                    except (ValueError, TypeError):
                        time_str = "??:??:??"

                    headline = e.get("headline", "")
                    detail = e.get("detail", "")
                    category = e.get("category", "")

                    # Color-code by category
                    if category in ("ORDER",):
                        color = "#00d4aa"
                    elif category in ("ERROR", "REJECTION"):
                        color = "#ff6b6b"
                    elif category in ("SIGNAL",):
                        color = "#ffd93d"
                    elif category in ("STARTUP", "SHUTDOWN"):
                        color = "#6bcbff"
                    elif category in ("HEARTBEAT",):
                        color = "#888"
                    else:
                        color = "#ccc"

                    # Render event
                    detail_html = ""
                    if detail:
                        detail_html = '<br/><span style="color: #999; font-size: 0.85em;">' + detail + '</span>'
                    st.markdown(
                        f"<div style='padding: 6px 0; border-left: 3px solid {color}; padding-left: 12px; margin-bottom: 4px;'>"
                        f"<span style='color: #888; font-size: 0.85em;'>{time_str}</span> "
                        f"{icon} <strong>{headline}</strong>"
                        f"{detail_html}"
                        f"</div>",
                        unsafe_allow_html=True,
                    )

                st.divider()

            st.caption(f"Showing {len(filtered_events)} events")
    else:
        st.info("No bot activity recorded yet. Events will appear here once the bot starts running.")


# ─── TAB: IG Activity ───────────────────────────────────────────────────────

if tab_activity is not None:
 with tab_activity:
    st.header("IG Activity Log (Last 7 Days)")

    if activities:
        act_data = []
        for a in activities:
            details = a.get("details") or {}
            act_data.append({
                "date": a.get("date", ""),
                "type": a.get("type", ""),
                "status": a.get("status", ""),
                "market": details.get("marketName", ""),
                "direction": details.get("direction", ""),
                "size": details.get("size", ""),
                "level": details.get("level", ""),
                "description": a.get("description", ""),
            })

        act_df = pd.DataFrame(act_data)
        st.dataframe(act_df, use_container_width=True, hide_index=True)

        # Rejection analysis
        rejects = [a for a in activities if a.get("status") == "REJECTED"]
        if rejects:
            st.warning(f"{len(rejects)} rejected order(s) in the last 7 days")
    else:
        st.info("No recent activity from IG.")


# ─── TAB: Open Spreads ────────────────────────────────────────────────────────

if tab_open_spreads is not None:
  with tab_open_spreads:
    st.header("Open Option Spreads")

    open_opts = get_open_option_positions()
    shadow_mode_note = getattr(config, "TRADING_MODE", "shadow") == "shadow"

    if shadow_mode_note:
        st.info("🔵 **SHADOW MODE** — These are live IG positions (if any). Shadow trades are logged separately below.")

    if open_opts:
        opt_data = []
        for p in open_opts:
            days_open = 0
            try:
                entry_dt = datetime.fromisoformat(p["entry_date"])
                days_open = (datetime.now() - entry_dt).days
            except Exception:
                pass

            dte = max(0, config.IBS_CREDIT_SPREAD_PARAMS.get("expiry_days", 10) - days_open)
            pnl_estimate = float(p.get("unrealised_pnl", 0))

            opt_data.append({
                "Ticker": p["ticker"],
                "Type": p.get("trade_type", "put_spread"),
                "Short Strike": f"{float(p.get('short_strike', 0)):,.0f}",
                "Long Strike": f"{float(p.get('long_strike', 0)):,.0f}",
                "Width": f"{float(p.get('spread_width', 0)):,.0f}",
                "Premium": f"{float(p.get('premium_collected', 0)):,.1f}",
                "Max Loss": f"£{float(p.get('max_loss', 0)) * float(p.get('size', 1)):,.0f}",
                "Size": int(float(p.get("size", 1))),
                "Days Open": days_open,
                "DTE": dte,
                "P&L": f"£{pnl_estimate:+,.2f}",
                "Entry": p.get("entry_date", "")[:10],
            })

        opt_df = pd.DataFrame(opt_data)
        st.dataframe(opt_df, use_container_width=True, hide_index=True)

        # Summary metrics
        total_max_loss = sum(float(p.get("max_loss", 0)) * float(p.get("size", 1)) for p in open_opts)
        total_premium = sum(float(p.get("premium_collected", 0)) * float(p.get("size", 1)) for p in open_opts)
        st.markdown(f"**Total max risk:** £{total_max_loss:,.0f}  |  **Total premium collected:** {total_premium:,.1f}pts  |  **Spreads open:** {len(open_opts)}")
    else:
        st.info("No open option spreads.")

    # Shadow trades section
    st.subheader("Recent Shadow Trades")
    shadow = get_shadow_trades(limit=50)
    if shadow:
        shadow_data = []
        for s in shadow:
            shadow_data.append({
                "Time": s["timestamp"][:16],
                "Ticker": s["ticker"],
                "Action": s["action"],
                "Short": f"{float(s.get('short_strike', 0)):,.0f}" if s.get("short_strike") else "—",
                "Long": f"{float(s.get('long_strike', 0)):,.0f}" if s.get("long_strike") else "—",
                "Width": f"{float(s.get('spread_width', 0)):,.0f}" if s.get("spread_width") else "—",
                "Size": int(float(s.get("size", 0))) if s.get("size") else "—",
                "Reason": s.get("reason", ""),
            })
        shadow_df = pd.DataFrame(shadow_data)
        st.dataframe(shadow_df, use_container_width=True, hide_index=True)
    else:
        st.info("No shadow trades yet. Start the bot in shadow mode to see what it would trade.")


# ─── TAB: Spread History ─────────────────────────────────────────────────────

if tab_spread_history is not None:
  with tab_spread_history:
    st.header("Closed Option Spreads")

    closed_opts = get_closed_option_positions(limit=200)

    if closed_opts:
        hist_data = []
        total_pnl = 0
        wins = 0
        losses = 0
        for p in closed_opts:
            pnl = float(p.get("exit_pnl", 0))
            total_pnl += pnl
            if pnl >= 0:
                wins += 1
            else:
                losses += 1

            hist_data.append({
                "Ticker": p["ticker"],
                "Type": p.get("trade_type", "put_spread"),
                "Entry": p.get("entry_date", "")[:10],
                "Exit": p.get("exit_date", "")[:10] if p.get("exit_date") else "—",
                "Short": f"{float(p.get('short_strike', 0)):,.0f}",
                "Long": f"{float(p.get('long_strike', 0)):,.0f}",
                "Width": f"{float(p.get('spread_width', 0)):,.0f}",
                "Premium": f"{float(p.get('premium_collected', 0)):,.1f}",
                "Size": int(float(p.get("size", 1))),
                "P&L": f"£{pnl:+,.2f}",
                "Reason": p.get("exit_reason", ""),
            })

        # Summary metrics
        n_trades = wins + losses
        win_rate = (wins / n_trades * 100) if n_trades > 0 else 0
        avg_pnl = total_pnl / n_trades if n_trades > 0 else 0

        col1, col2, col3, col4 = st.columns(4)
        with col1:
            st.metric("Total Trades", n_trades)
        with col2:
            st.metric("Win Rate", f"{win_rate:.0f}%")
        with col3:
            st.metric("Total P&L", f"£{total_pnl:+,.2f}")
        with col4:
            st.metric("Avg P&L", f"£{avg_pnl:+,.2f}")

        st.divider()

        hist_df = pd.DataFrame(hist_data)
        st.dataframe(hist_df, use_container_width=True, hide_index=True)

        # P&L chart
        if len(hist_data) > 1:
            st.subheader("Cumulative P&L")
            pnl_series = [float(p.get("exit_pnl", 0)) for p in closed_opts]
            cum_pnl = []
            running = 0
            for p in reversed(pnl_series):  # Oldest first
                running += p
                cum_pnl.append(running)
            chart_df = pd.DataFrame({"Cumulative P&L (£)": cum_pnl})
            st.line_chart(chart_df)
    else:
        st.info("No closed option spreads yet.")


# ─── Auto-refresh ────────────────────────────────────────────────────────────

if auto_refresh:
    time.sleep(30)
    st.cache_data.clear()
    st.rerun()
