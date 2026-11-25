"""Hartford County Healthcare Price Transparency — Patient Cost Optimizer & Investor View."""

import sqlite3
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from config import DB_PATH

st.set_page_config(
    page_title="Hartford County Care Cost Finder",
    page_icon="🏥",
    layout="wide",
)

NON_HOSPITAL_ENTITIES = {"State of Connecticut", "Unknown Provider"}


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
            CASE WHEN r.hospital_id IS NOT NULL THEN 1 ELSE 0 END AS is_matched_hospital,
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
    return f"${val:,.0f}" if pd.notna(val) else "Not listed"


def build_price_variation_chart(chart_df: pd.DataFrame, insurer_label: str, market_median: float) -> go.Figure:
    ordered_df = chart_df.sort_values("median_rate", ascending=True).reset_index(drop=True)
    y_order = ordered_df["hospital_name"].tolist()[::-1]

    fig = go.Figure()

    # Connecting range bars
    for _, r in ordered_df.iterrows():
        if r["min_rate"] != r["max_rate"]:
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

    # Min Rate markers (label endpoints when spread is wide)
    fig.add_trace(
        go.Scatter(
            x=ordered_df["min_rate"],
            y=ordered_df["hospital_name"],
            mode="markers+text",
            name="Min Rate",
            marker=dict(color="#22c55e", size=11),
            text=[
                f"${r['min_rate']:,.0f}" if (r["max_rate"] - r["min_rate"]) > 800 else ""
                for _, r in ordered_df.iterrows()
            ],
            textposition="bottom left",
            textfont=dict(color="#86efac", size=11),
            hovertemplate="<b>%{y}</b><br>Min Rate: $%{x:,.0f}<extra></extra>",
        )
    )

    # Max Rate markers (label endpoints when spread is wide)
    fig.add_trace(
        go.Scatter(
            x=ordered_df["max_rate"],
            y=ordered_df["hospital_name"],
            mode="markers+text",
            name="Max Rate",
            marker=dict(color="#ef4444", size=11),
            text=[
                f"${r['max_rate']:,.0f}" if (r["max_rate"] - r["median_rate"]) > 800 else ""
                for _, r in ordered_df.iterrows()
            ],
            textposition="bottom right",
            textfont=dict(color="#fca5a5", size=11),
            hovertemplate="<b>%{y}</b><br>Max Rate: $%{x:,.0f}<extra></extra>",
        )
    )

    # Median Rate diamonds (rendered last so they stay visible when Min == Median == Max)
    fig.add_trace(
        go.Scatter(
            x=ordered_df["median_rate"],
            y=ordered_df["hospital_name"],
            mode="markers+text",
            name="Median Rate",
            marker=dict(
                color="#3b82f6",
                size=13,
                symbol="diamond",
                line=dict(color="#ffffff", width=1),
            ),
            text=[f"${v:,.0f}" for v in ordered_df["median_rate"]],
            textposition="top center",
            textfont=dict(size=12),
            hovertemplate="<b>%{y}</b><br>Median Rate: $%{x:,.0f}<extra></extra>",
        )
    )

    # Vertical Market Median reference line
    fig.add_vline(
        x=market_median,
        line_dash="dash",
        line_color="#f59e0b",
        line_width=1.5,
        annotation_text=f"Market Median: ${market_median:,.0f}",
        annotation_position="top right",
        annotation_font_color="#fbbf24",
    )

    fig.update_layout(
        xaxis_title="Negotiated In-Network Rate ($)",
        xaxis_tickprefix="$",
        xaxis_tickformat=",.0f",
        yaxis=dict(type="category", categoryorder="array", categoryarray=y_order),
        height=max(320, 75 * len(ordered_df)),
        margin=dict(l=10, r=20, t=45, b=40),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )
    return fig


def render_patient_finder(
    rates_df: pd.DataFrame,
    cpts_df: pd.DataFrame,
    include_non_hospitals: bool = False,
) -> None:
    st.caption(
        "Compare negotiated in-network surgery rates across Hartford County hospitals to understand "
        "price variation before scheduling your procedure."
    )

    filtered_rates = rates_df.copy()
    if not include_non_hospitals:
        filtered_rates = filtered_rates[
            (filtered_rates["is_matched_hospital"] == 1)
            & (~filtered_rates["hospital_name"].isin(NON_HOSPITAL_ENTITIES))
        ]

    # Build searchable procedure options (only CPTs that exist in filtered_rates, or all)
    avail_cpts = sorted(filtered_rates["cpt_code"].unique().tolist())
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
            "1. Procedure (CPT code or plain name)",
            options=list(cpt_lookup.keys()),
            format_func=lambda code: cpt_lookup.get(code, code),
            index=0,
            help="Search by billing CPT code if you have one, or by the common procedure name.",
        )

    cpt_rows = filtered_rates[filtered_rates["cpt_code"] == selected_cpt]
    insurers_for_cpt = sorted(cpt_rows["insurer_name"].unique().tolist())

    with col_ins:
        selected_insurer = st.selectbox(
            "2. Health Insurer",
            options=["All Insurers"] + insurers_for_cpt,
            index=1 if insurers_for_cpt else 0,
        )

    settings_for_cpt = sorted(cpt_rows["setting"].dropna().unique().tolist())
    with col_set:
        selected_setting = st.selectbox(
            "3. Care Setting",
            options=["All Settings"] + settings_for_cpt,
            index=0,
            help="Outpatient surgery centers often have lower facility fees than main hospital campuses.",
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
    # True Market Median across hospitals (unweighted by plan row count)
    market_median = float(hosp_summary["median_rate"].median())
    savings_vs_max = float(worst_row["median_rate"] - best_row["median_rate"])
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

    # --- 3 Clean KPIs (delta_color="off" avoids misleading up/down arrows) ---
    k1, k2, k3 = st.columns(3)
    k1.metric(
        "Lowest Hospital Median",
        format_currency(best_row["median_rate"]),
        delta=best_row["hospital_name"],
        delta_color="off",
    )
    k2.metric(
        "Typical (Market Median) Rate",
        format_currency(market_median),
        delta=f"Full range: {format_currency(hosp_summary['min_rate'].min())} – {format_currency(hosp_summary['max_rate'].max())}",
        delta_color="off",
    )
    k3.metric(
        "Max Potential Savings",
        format_currency(savings_vs_max),
        delta=f"{savings_pct:.1f}% lower than highest-priced hospital",
        delta_color="off",
    )

    # --- Visual Range Chart FIRST for immediate price-variation scanning ---
    if selected_insurer != "All Insurers":
        st.subheader(f"In-Network Price Range by Hospital — {selected_insurer}")
        fig = build_price_variation_chart(hosp_summary, selected_insurer, market_median)
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("Select a specific **Health Insurer** above to view the hospital price-range chart.")

    # --- Patient Decision Context ---
    with st.expander("How to read these numbers before choosing a hospital", expanded=False):
        ins_context = selected_insurer if selected_insurer != "All Insurers" else "your insurer"
        st.markdown(
            f"- **Negotiated Rate vs. Out-of-Pocket Cost:** These are the total rates negotiated between "
            f"{ins_context} and each hospital. What you actually pay depends on your deductible, copay, and coinsurance.\n"
            "- **Facility vs. Physician Fees:** Verify with the hospital whether the quoted rate covers only the "
            "facility fee or also includes the surgeon and anesthesiologist.\n"
            "- **Why `Plans` matters:** A hospital showing `1` plan (with identical Min/Median/Max) only has data "
            "for a single plan tier—confirm your exact plan is included before booking."
        )

    # --- Hospital Price Comparison Table ---
    st.subheader("Hospital Price Comparison (Sorted Lowest to Highest)")
    display_df = hosp_summary.copy()
    display_df["Savings vs. Highest"] = worst_row["median_rate"] - display_df["median_rate"]
    display_df["vs. Market Median"] = display_df["median_rate"].apply(
        lambda x: "At median"
        if x == market_median
        else (f"-${abs(x - market_median):,.0f} below" if x < market_median else f"+${x - market_median:,.0f} above")
    )

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

    # Format Cash Price cleanly so missing values show 'Not listed' instead of 'None'
    display_df["Cash Price"] = display_df["Cash Price"].apply(
        lambda x: f"${x:,.0f}" if pd.notna(x) else "Not listed"
    )

    ordered_cols = ["Hospital"]
    if "Insurer" in display_df.columns:
        ordered_cols.append("Insurer")
    ordered_cols += [
        "Min Rate",
        "Median Rate",
        "Max Rate",
        "vs. Market Median",
        "Savings vs. Highest",
        "Cash Price",
        "Plans",
    ]
    display_df = display_df[ordered_cols]

    st.dataframe(
        display_df.style.format(
            {
                "Min Rate": "${:,.0f}",
                "Median Rate": "${:,.0f}",
                "Max Rate": "${:,.0f}",
                "Savings vs. Highest": "${:,.0f}",
            }
        ),
        use_container_width=True,
        hide_index=True,
        column_config={
            "Plans": st.column_config.NumberColumn(
                "Plans",
                help="Number of insurer plan tiers with reported rates at this hospital.",
            ),
        },
    )


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

    cpts_df, _ = load_reference_data(str(DB_PATH))
    rates_df = load_rates(str(DB_PATH))

    if rates_df.empty:
        st.warning(
            "Database is initialized, but no negotiated rate records have been ingested yet. "
            "Run `python pipeline.py --ingest-hospitals` first."
        )
        return

    with st.sidebar:
        st.header("View Options")
        audience_view = st.radio(
            "Dashboard Mode",
            ["Patient Cost Optimizer", "Investor & Market Overview"],
            index=0,
        )
        include_non_hospitals = False
        if audience_view == "Patient Cost Optimizer":
            include_non_hospitals = st.checkbox(
                "Include non-hospital entities",
                value=False,
                help="Show unmatched provider rows (e.g., State of Connecticut) alongside hospitals.",
            )

    if audience_view == "Patient Cost Optimizer":
        render_patient_finder(rates_df, cpts_df, include_non_hospitals=include_non_hospitals)
    else:
        render_investor_overview(rates_df)


if __name__ == "__main__":
    main()