"""Hartford County Healthcare Price Transparency — Patient Cost Optimizer & Investor View."""

import sqlite3
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
import streamlit as st

from config import DB_PATH

st.set_page_config(
    page_title="Hartford County Care Cost Finder",
    page_icon="🏥",
    layout="wide",
)


@st.cache_data(ttl=300)
def load_reference_data(db_path: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    conn = sqlite3.connect(db_path)
    cpts = pd.read_sql_query(
        "SELECT cpt_code, description, category FROM cpt_codes ORDER BY cpt_code", conn
    )
    hospitals = pd.read_sql_query(
        "SELECT hospital_id, hospital_name, ein, last_updated_on FROM hospitals ORDER BY hospital_name",
        conn,
    )
    conn.close()
    return cpts, hospitals


@st.cache_data(ttl=300)
def load_rates(db_path: str) -> pd.DataFrame:
    conn = sqlite3.connect(db_path)
    query = """
        SELECT
            r.cpt_code,
            c.description AS cpt_description,
            c.category AS cpt_category,
            i.canonical_name AS insurer_name,
            COALESCE(h.hospital_name, r.provider_name, 'Unknown Provider') AS hospital_name,
            r.plan_name,
            r.setting,
            r.billing_class,
            r.gross_charge,
            r.discounted_cash,
            r.negotiated_dollar
        FROM negotiated_rates r
        JOIN cpt_codes c ON r.cpt_code = c.cpt_code
        JOIN insurers i ON r.insurer_id = i.insurer_id
        LEFT JOIN hospitals h ON r.hospital_id = h.hospital_id
        WHERE r.negotiated_dollar IS NOT NULL AND r.negotiated_dollar > 0
    """
    df = pd.read_sql_query(query, conn)
    conn.close()
    return df


def format_currency(val: float) -> str:
    return f"${val:,.0f}" if pd.notna(val) else "—"


def render_patient_finder(rates_df: pd.DataFrame, cpts_df: pd.DataFrame) -> None:
    st.markdown(
        "Search by **CPT code** or **service name** and select your **health insurer** to compare "
        "in-network **minimum, median, and maximum** rates across Hartford County hospitals."
    )

    # Build searchable procedure options (only CPTs that exist in rates_df, or all 30)
    avail_cpts = sorted(rates_df["cpt_code"].unique().tolist())
    cpt_lookup = {
        row["cpt_code"]: f"{row['cpt_code']} — {row['description']} ({row['category']})"
        for _, row in cpts_df.iterrows()
        if row["cpt_code"] in avail_cpts
    }
    if not cpt_lookup:
        cpt_lookup = {
            row["cpt_code"]: f"{row['cpt_code']} — {row['description']}"
            for _, row in cpts_df.iterrows()
        }

    # --- Clean 3-column Patient Search Bar ---
    col_proc, col_ins, col_set = st.columns([2.2, 1.5, 1.0])

    with col_proc:
        selected_cpt = st.selectbox(
            "1. Procedure (type CPT code or service name)",
            options=list(cpt_lookup.keys()),
            format_func=lambda code: cpt_lookup.get(code, code),
            index=0,
        )

    cpt_rows = rates_df[rates_df["cpt_code"] == selected_cpt]
    insurers_for_cpt = sorted(cpt_rows["insurer_name"].unique().tolist())

    with col_ins:
        selected_insurer = st.selectbox(
            "2. Health Insurer",
            options=["All Insurers"] + insurers_for_cpt,
            index=1 if insurers_for_cpt else 0,
        )

    settings_for_cpt = sorted(cpt_rows["setting"].unique().tolist())
    with col_set:
        selected_setting = st.selectbox(
            "3. Care Setting",
            options=["All Settings"] + settings_for_cpt,
            index=0,
        )

    # Apply filters
    slice_df = cpt_rows.copy()
    if selected_insurer != "All Insurers":
        slice_df = slice_df[slice_df["insurer_name"] == selected_insurer]
    if selected_setting != "All Settings":
        slice_df = slice_df[slice_df["setting"] == selected_setting]

    if slice_df.empty:
        st.info("No in-network rates found for that combination. Try selecting 'All Settings' or another insurer.")
        return

    # Aggregate by Hospital (and Insurer if All Insurers selected)
    group_cols = ["hospital_name"] if selected_insurer != "All Insurers" else ["hospital_name", "insurer_name"]
    hosp_summary = (
        slice_df.groupby(group_cols, as_index=False)
        .agg(
            min_rate=("negotiated_dollar", "min"),
            median_rate=("negotiated_dollar", "median"),
            max_rate=("negotiated_dollar", "max"),
            cash_price=("discounted_cash", "median"),
            plan_count=("plan_name", "nunique"),
        )
        .sort_values("median_rate", ascending=True)
        .reset_index(drop=True)
    )

    best_row = hosp_summary.iloc[0]
    worst_row = hosp_summary.iloc[-1]
    savings_vs_max = worst_row["median_rate"] - best_row["median_rate"]
    savings_pct = (savings_vs_max / worst_row["median_rate"] * 100.0) if worst_row["median_rate"] > 0 else 0.0

    st.divider()

    # --- Clear Patient Recommendation Banner ---
    if len(hosp_summary) >= 2 and savings_vs_max > 0:
        ins_label = selected_insurer if selected_insurer != "All Insurers" else best_row["insurer_name"]
        st.success(
            f"**Lowest-Cost Option:** **{best_row['hospital_name']}** ({ins_label}) has a median in-network rate of "
            f"**{format_currency(best_row['median_rate'])}** — saving **{format_currency(savings_vs_max)} ({savings_pct:.0f}%)** "
            f"compared to the highest-priced hospital (**{worst_row['hospital_name']}** at {format_currency(worst_row['median_rate'])})."
        )

    # --- 3 Simple KPIs ---
    k1, k2, k3 = st.columns(3)
    k1.metric(
        "Lowest Hospital Median",
        format_currency(best_row["median_rate"]),
        delta=best_row["hospital_name"],
        delta_color="off",
    )
    k2.metric(
        "Typical (Market Median) Rate",
        format_currency(slice_df["negotiated_dollar"].median()),
        delta=f"Range: {format_currency(slice_df['negotiated_dollar'].min())} – {format_currency(slice_df['negotiated_dollar'].max())}",
        delta_color="off",
    )
    k3.metric(
        "Max Potential Savings",
        format_currency(savings_vs_max),
        delta=f"{savings_pct:.1f}% lower than highest hospital",
        delta_color="normal",
    )

    # --- Hospital Price Comparison Table ---
    st.subheader("Hospital Price Comparison (Sorted Lowest to Highest)")
    display_df = hosp_summary.copy()
    display_df["Savings vs. Highest"] = worst_row["median_rate"] - display_df["median_rate"]

    col_rename = {
        "hospital_name": "Hospital",
        "insurer_name": "Insurer",
        "min_rate": "Min Rate",
        "median_rate": "Median Rate",
        "max_rate": "Max Rate",
        "cash_price": "Cash Price",
        "plan_count": "Plans",
    }
    display_df = display_df.rename(columns=col_rename)

    ordered_cols = ["Hospital"]
    if "Insurer" in display_df.columns:
        ordered_cols.append("Insurer")
    ordered_cols += ["Min Rate", "Median Rate", "Max Rate", "Savings vs. Highest", "Cash Price", "Plans"]
    display_df = display_df[ordered_cols]

    st.dataframe(
        display_df.style.format(
            {
                "Min Rate": "${:,.0f}",
                "Median Rate": "${:,.0f}",
                "Max Rate": "${:,.0f}",
                "Savings vs. Highest": "${:,.0f}",
                "Cash Price": lambda x: f"${x:,.0f}" if pd.notna(x) else "—",
            }
        ),
        use_container_width=True,
        hide_index=True,
    )

    # --- Clean Range Chart (Min / Median / Max per Hospital) ---
    if selected_insurer != "All Insurers":
        st.subheader(f"Min, Median & Max In-Network Rates — {selected_insurer}")
        chart_df = hosp_summary.sort_values("median_rate", ascending=True)

        fig = go.Figure()
        for _, r in chart_df.iterrows():
            fig.add_trace(
                go.Scatter(
                    x=[r["min_rate"], r["max_rate"]],
                    y=[r["hospital_name"], r["hospital_name"]],
                    mode="lines",
                    line=dict(color="#64748b", width=6),
                    showlegend=False,
                    hoverinfo="skip",
                )
            )
        fig.add_trace(
            go.Scatter(
                x=chart_df["min_rate"],
                y=chart_df["hospital_name"],
                mode="markers",
                name="Min Rate",
                marker=dict(color="#22c55e", size=12),
            )
        )
        fig.add_trace(
            go.Scatter(
                x=chart_df["median_rate"],
                y=chart_df["hospital_name"],
                mode="markers+text",
                name="Median Rate",
                marker=dict(color="#3b82f6", size=14, symbol="diamond"),
                text=[f"${v:,.0f}" for v in chart_df["median_rate"]],
                textposition="top center",
            )
        )
        fig.add_trace(
            go.Scatter(
                x=chart_df["max_rate"],
                y=chart_df["hospital_name"],
                mode="markers",
                name="Max Rate",
                marker=dict(color="#ef4444", size=12),
            )
        )
        fig.update_layout(
            xaxis_title="Negotiated In-Network Rate ($)",
            xaxis_tickprefix="$",
            xaxis_tickformat=",.0f",
            yaxis=dict(type="category", categoryorder="array", categoryarray=chart_df["hospital_name"].tolist()[::-1]),
            height=max(300, 75 * len(chart_df)),
            margin=dict(l=10, r=20, t=30, b=40),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        )
        st.plotly_chart(fig, use_container_width=True)


def render_investor_overview(rates_df: pd.DataFrame) -> None:
    st.markdown("Macro price dispersion across all 30 target procedures and Hartford County hospitals.")

    agg = (
        rates_df.groupby(["cpt_code", "cpt_description", "insurer_name", "hospital_name"], as_index=False)
        .agg(
            min_rate=("negotiated_dollar", "min"),
            median_rate=("negotiated_dollar", "median"),
            max_rate=("negotiated_dollar", "max"),
        )
    )

    market_stats = (
        agg.groupby(["cpt_code", "cpt_description", "insurer_name"], as_index=False)
        .agg(
            hospital_count=("hospital_name", "nunique"),
            min_hosp_rate=("median_rate", "min"),
            avg_hosp_rate=("median_rate", "mean"),
            max_hosp_rate=("median_rate", "max"),
        )
    )
    market_stats["dollar_spread"] = market_stats["max_hosp_rate"] - market_stats["min_hosp_rate"]
    market_stats["spread_ratio"] = market_stats["max_hosp_rate"] / market_stats["min_hosp_rate"]
    market_stats["avg_member_savings"] = market_stats["avg_hosp_rate"] - market_stats["min_hosp_rate"]
    market_stats["savings_pct"] = (market_stats["avg_member_savings"] / market_stats["avg_hosp_rate"]) * 100.0
    market_stats["cpt_label"] = market_stats["cpt_code"] + " — " + market_stats["cpt_description"].str.slice(0, 32)

    multi_hosp = market_stats[market_stats["hospital_count"] >= 2]

    k1, k2, k3 = st.columns(3)
    k1.metric("Median Price Spread (Max / Min)", f"{multi_hosp['spread_ratio'].median():.2f}x" if not multi_hosp.empty else "N/A")
    k2.metric("Largest Dollar Spread", f"${multi_hosp['dollar_spread'].max():,.0f}" if not multi_hosp.empty else "N/A")
    k3.metric("Avg Potential Savings vs. Market Avg", f"{multi_hosp['savings_pct'].mean():.1f}%" if not multi_hosp.empty else "N/A")

    if not multi_hosp.empty:
        top_spread = (
            multi_hosp.sort_values("dollar_spread", ascending=False)
            .drop_duplicates(subset=["cpt_code"])
            .head(12)
            .sort_values("dollar_spread", ascending=True)
        )
        fig_spread = px.bar(
            top_spread,
            x="dollar_spread",
            y="cpt_label",
            color="insurer_name",
            orientation="h",
            hover_data={"min_hosp_rate": ":$,.0f", "max_hosp_rate": ":$,.0f", "spread_ratio": ":.2f"},
            title="Top Procedures by Dollar Spread (Highest vs. Lowest Hospital)",
            labels={"dollar_spread": "Dollar Spread ($)", "cpt_label": "Procedure", "insurer_name": "Insurer"},
        )
        fig_spread.update_layout(
            xaxis_tickprefix="$",
            xaxis_tickformat=",.0f",
            yaxis=dict(type="category"),
            height=460,
        )
        st.plotly_chart(fig_spread, use_container_width=True)

    st.subheader("Full Exportable Price Table")
    st.dataframe(
        agg.sort_values(["cpt_code", "insurer_name", "median_rate"]).style.format(
            {"min_rate": "${:,.0f}", "median_rate": "${:,.0f}", "max_rate": "${:,.0f}"}
        ),
        use_container_width=True,
        hide_index=True,
    )
    st.download_button(
        "Download Full Matrix (CSV)",
        data=agg.to_csv(index=False).encode("utf-8"),
        file_name="hartford_county_in_network_prices.csv",
        mime="text/csv",
    )


def main() -> None:
    st.title("Hartford County Care Cost Finder")

    if not Path(DB_PATH).exists():
        st.error(f"Database not found at `{DB_PATH}`. Run `python pipeline.py --init-only` first.")
        return

    cpts_df, hospitals_df = load_reference_data(str(DB_PATH))
    rates_df = load_rates(str(DB_PATH))

    if rates_df.empty:
        st.warning(
            "Database is initialized, but no negotiated rate records have been ingested yet. "
            "Run `python pipeline.py --ingest-hospitals` first."
        )
        return

    tab_patient, tab_investor = st.tabs(
        ["Patient Care Cost Optimizer", "Investor & Market Overview"]
    )

    with tab_patient:
        render_patient_finder(rates_df, cpts_df)

    with tab_investor:
        render_investor_overview(rates_df)


if __name__ == "__main__":
    main()