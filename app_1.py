import streamlit as st
import sqlite3
import pandas as pd
import numpy as np
import plotly.express as px
from pathlib import Path

# Page configuration
st.set_page_config(
    page_title="Hartford County Care Cost Finder",
    page_icon="🏥",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# Custom CSS styling to match the clean medical consumer aesthetic
st.markdown(
    """
    <style>
    .main {
        background-color: #F8FAFC;
    }
    .header-title {
        font-size: 2.2rem;
        font-weight: 700;
        color: #0F172A;
        display: flex;
        align-items: center;
        gap: 12px;
        margin-bottom: 0.5rem;
    }
    .patient-card {
        background: white;
        border: 1px solid #E2E8F0;
        border-radius: 12px;
        padding: 20px;
        margin-bottom: 16px;
        box-shadow: 0 1px 3px rgba(0,0,0,0.05);
    }
    .best-value-badge {
        background-color: #10B981;
        color: white;
        padding: 3px 8px;
        border-radius: 6px;
        font-size: 0.75rem;
        font-weight: 600;
        text-transform: uppercase;
        margin-left: 8px;
    }
    .cost-highlight {
        font-size: 1.8rem;
        font-weight: 700;
        color: #0284C7;
    }
    .sub-rate-text {
        color: #64748B;
        font-size: 0.85rem;
    }
    .fee-pill {
        background-color: #F1F5F9;
        border-radius: 8px;
        padding: 8px 12px;
        margin-top: 6px;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

DB_PATH = Path(__file__).resolve().parent / "hartford_rates.db"
# Fallback to duckdb if sqlite isn't present
if not DB_PATH.exists() and Path("hartford_rates.duckdb").exists():
    DB_PATH = Path("hartford_rates.duckdb")

HOSPITAL_LOCATIONS = {
    "Saint Francis Hospital": {"city": "Hartford, CT", "distance": "3.2 mi"},
    "Hartford Hospital": {"city": "Hartford, CT", "distance": "2.8 mi"},
    "Hospital of Central Connecticut": {"city": "New Britain, CT", "distance": "12.4 mi"},
    "Manchester Memorial Hospital": {"city": "Manchester, CT", "distance": "10.1 mi"},
    "Johnson Memorial Hospital": {"city": "Stafford Springs, CT", "distance": "24.5 mi"},
    "State of Connecticut": {"city": "Hartford, CT", "distance": "4.0 mi"},
}


@st.cache_data(ttl=3600)
def load_all_rates():
    if not DB_PATH.exists():
        # Fallback dummy dataset for prototyping if DB not loaded yet
        return pd.DataFrame([
            {
                "cpt_code": "27130",
                "procedure_name": "Hip Replacement (Total)",
                "category": "Orthopedics",
                "hospital_name": "Saint Francis Hospital",
                "payer_name": "Aetna",
                "billing_class": "FACILITY",
                "negotiated_rate": 3500.0,
                "discounted_cash": 904.0,
            },
            {
                "cpt_code": "27130",
                "procedure_name": "Hip Replacement (Total)",
                "category": "Orthopedics",
                "hospital_name": "Saint Francis Hospital",
                "payer_name": "Aetna",
                "billing_class": "PROFESSIONAL",
                "negotiated_rate": 535.0,
                "discounted_cash": 400.0,
            },
            {
                "cpt_code": "27130",
                "procedure_name": "Hip Replacement (Total)",
                "category": "Orthopedics",
                "hospital_name": "Hospital of Central Connecticut",
                "payer_name": "Aetna",
                "billing_class": "FACILITY",
                "negotiated_rate": 4035.0,
                "discounted_cash": 7400.0,
            },
            {
                "cpt_code": "27130",
                "procedure_name": "Hip Replacement (Total)",
                "category": "Orthopedics",
                "hospital_name": "Hartford Hospital",
                "payer_name": "Aetna",
                "billing_class": "FACILITY",
                "negotiated_rate": 15894.0,
                "discounted_cash": 7400.0,
            },
            {
                "cpt_code": "27130",
                "procedure_name": "Hip Replacement (Total)",
                "category": "Orthopedics",
                "hospital_name": "Manchester Memorial Hospital",
                "payer_name": "Aetna",
                "billing_class": "FACILITY",
                "negotiated_rate": 9718.0,
                "discounted_cash": None,
            },
        ])

    conn = sqlite3.connect(DB_PATH)
    query = """
    SELECT 
        nr.cpt_code,
        COALESCE(c.description, nr.cpt_code) as procedure_name,
        COALESCE(c.category, 'General') as category,
        h.hospital_name,
        i.canonical_name as payer_name,
        nr.billing_class,
        nr.negotiated_dollar as negotiated_rate,
        nr.discounted_cash
    FROM negotiated_rates nr
    LEFT JOIN cpt_codes c ON nr.cpt_code = c.cpt_code
    LEFT JOIN hospitals h ON nr.hospital_id = h.hospital_id
    LEFT JOIN insurers i ON nr.insurer_id = i.insurer_id
    WHERE nr.negotiated_dollar > 0
    """
    df = pd.read_sql_query(query, conn)
    conn.close()
    return df


df = load_all_rates()

# ----------------- TOP BAR: HEADER & SEARCH -----------------
st.markdown(
    '<div class="header-title">🏥 Hartford County Care Cost Finder</div>',
    unsafe_allow_html=True,
)

# Friendly Procedure Mapping
procedure_options = (
    df[["cpt_code", "procedure_name"]]
    .drop_duplicates()
    .sort_values("procedure_name")
)
procedure_dict = {
    f"{row['procedure_name']} — CPT {row['cpt_code']}": row["cpt_code"]
    for _, row in procedure_options.iterrows()
}

# Quick-filter pill helper
if "selected_cpt" not in st.session_state:
    st.session_state["selected_cpt"] = "27130"

col_search, _ = st.columns([4, 1])
with col_search:
    search_keys = list(procedure_dict.keys())
    default_index = 0
    for idx, name in enumerate(search_keys):
        if procedure_dict[name] == st.session_state["selected_cpt"]:
            default_index = idx
            break

    selected_label = st.selectbox(
        "Search Procedure or CPT Code",
        options=search_keys,
        index=default_index,
        label_visibility="collapsed",
    )
    current_cpt = procedure_dict[selected_label]

# Quick shortcut tag buttons
st.markdown("**Popular:**", unsafe_allow_html=True)
tag_cols = st.columns(6)
quick_tags = [
    ("🦵 Hip Replacement", "27130"),
    ("🦵 Knee Replacement", "27447"),
    ("🩻 Lumbar Spine MRI", "72148"),
    ("🔬 Diagnostic Colonoscopy", "45378"),
    ("🏃 PT Therapy", "97110"),
    ("🩸 Metabolic Panel", "80053"),
]

for i, (label, cpt) in enumerate(quick_tags):
    if tag_cols[i].button(label, key=f"tag_{cpt}", use_container_width=True):
        st.session_state["selected_cpt"] = cpt
        st.rerun()

st.markdown("<br>", unsafe_allow_html=True)

# ----------------- MAIN TWO-COLUMN WORKFLOW -----------------
col_left, col_right = st.columns([1, 2.5], gap="large")

# LEFT COLUMN: Patient Insurance & Cost-Share Parameters
with col_left:
    st.markdown("### 📋 Your Insurance & Deductible")
    with st.container(border=True):
        available_payers = sorted(df["payer_name"].dropna().unique())
        selected_payer = st.selectbox(
            "Health Insurer",
            options=available_payers,
            index=available_payers.index("Aetna") if "Aetna" in available_payers else 0,
        )

        deductible_met = st.toggle("Deductible already met this year?", value=False)
        remaining_deductible = 0.0
        if not deductible_met:
            remaining_deductible = st.number_input(
                "Estimated Remaining Deductible ($)",
                min_value=0.0,
                max_value=10000.0,
                value=1500.0,
                step=250.0,
            )

        coinsurance = st.slider(
            "Coinsurance Percentage (Your share after deductible)",
            min_value=0,
            max_value=50,
            value=20,
            step=5,
            format="%d%%",
        )

        st.caption(
            "💡 **Tip:** Most employer plans cover preventive screenings (e.g. routine colonoscopies) at 100% with $0 out of pocket."
        )

# Filter Data for Selected CPT & Insurer
proc_df = df[(df["cpt_code"] == current_cpt) & (df["payer_name"] == selected_payer)]

if proc_df.empty:
    with col_right:
        st.warning(f"No in-network negotiated rates found for {selected_label} under {selected_payer}.")
    st.stop()

# Aggregate by Hospital: Compute Facility, Professional, and Combined Medians
hospital_summary = []
for hospital, grp in proc_df.groupby("hospital_name"):
    fac_rate = grp[grp["billing_class"] == "FACILITY"]["negotiated_rate"].median()
    prof_rate = grp[grp["billing_class"] == "PROFESSIONAL"]["negotiated_rate"].median()
    cash_price = grp["discounted_cash"].dropna().median()

    # Total in-network rate (bundle or facility + prof)
    if pd.notna(fac_rate) and pd.notna(prof_rate):
        total_rate = fac_rate + prof_rate
    elif pd.notna(fac_rate):
        total_rate = fac_rate
    else:
        total_rate = prof_rate if pd.notna(prof_rate) else grp["negotiated_rate"].median()

    # Calculate Patient Out-of-Pocket Estimate
    if deductible_met:
        patient_oop = total_rate * (coinsurance / 100.0)
    else:
        patient_oop = min(total_rate, remaining_deductible) + max(
            0.0, (total_rate - remaining_deductible) * (coinsurance / 100.0)
        )

    meta = HOSPITAL_LOCATIONS.get(
        hospital, {"city": "Hartford County, CT", "distance": "N/A"}
    )

    hospital_summary.append({
        "hospital_name": hospital,
        "city": meta["city"],
        "distance": meta["distance"],
        "total_rate": total_rate,
        "facility_fee": fac_rate if pd.notna(fac_rate) else (total_rate * 0.75),
        "surgeon_fee": prof_rate if pd.notna(prof_rate) else (total_rate * 0.25),
        "estimated_oop": patient_oop,
        "cash_price": cash_price,
    })

summary_df = pd.DataFrame(hospital_summary).sort_values("estimated_oop")

# RIGHT COLUMN: Cost Highlights & Provider Choice Cards
with col_right:
    best_option = summary_df.iloc[0]
    highest_option = summary_df.iloc[-1]
    potential_savings = highest_option["estimated_oop"] - best_option["estimated_oop"]

    # Top Hero Callout
    callout_col1, callout_col2, callout_col3 = st.columns([1.5, 1.5, 1.5])
    with callout_col1:
        with st.container(border=True):
            st.caption("ESTIMATED YOU PAY (BEST OPTION)")
            st.markdown(
                f'<div class="cost-highlight">${best_option["estimated_oop"]:,.0f}</div>',
                unsafe_allow_html=True,
            )
            st.caption(f"At {best_option['hospital_name']}")

    with callout_col2:
        with st.container(border=True):
            st.caption("TOTAL PLAN NEGOTIATED COST")
            st.markdown(
                f'<div style="font-size: 1.8rem; font-weight:700; color:#1E293B">${best_option["total_rate"]:,.0f}</div>',
                unsafe_allow_html=True,
            )
            st.caption(f"Network: {selected_payer}")

    with callout_col3:
        with st.container(border=True):
            st.caption("POTENTIAL PATIENT SAVINGS")
            st.markdown(
                f'<div style="font-size: 1.8rem; font-weight:700; color:#10B981">${potential_savings:,.0f}</div>',
                unsafe_allow_html=True,
            )
            st.caption("By choosing lowest-cost facility")

    st.markdown("<br>", unsafe_allow_html=True)
    st.subheader(f"In-Network Hospitals for {selected_payer} (Ranked by Value)")

    # Render Provider Decision Cards
    for idx, row in summary_df.iterrows():
        is_best_val = idx == summary_df.index[0]
        with st.container(border=True):
            header_col, oop_col, cash_col = st.columns([2.5, 1.5, 1.2])

            with header_col:
                badge = '<span class="best-value-badge">Best Value</span>' if is_best_val else ""
                st.markdown(
                    f"**{row['hospital_name']}** {badge}",
                    unsafe_allow_html=True,
                )
                st.caption(f"📍 {row['city']} ({row['distance']})")

                # Facility vs Surgeon Fee Breakdown Pills
                f_col1, f_col2 = st.columns(2)
                f_col1.markdown(
                    f'<div class="fee-pill"><span class="sub-rate-text">Facility Fee:</span><br><strong>${row["facility_fee"]:,.0f}</strong></div>',
                    unsafe_allow_html=True,
                )
                f_col2.markdown(
                    f'<div class="fee-pill"><span class="sub-rate-text">Surgeon / Prof Fee:</span><br><strong>${row["surgeon_fee"]:,.0f}</strong></div>',
                    unsafe_allow_html=True,
                )

            with oop_col:
                st.caption("Estimated Out-of-Pocket:")
                st.markdown(
                    f'<div style="font-size:1.5rem; font-weight:700; color:#0284C7">${row["estimated_oop"]:,.0f}</div>',
                    unsafe_allow_html=True,
                )
                st.caption(f"Total Contract: ${row['total_rate']:,.0f}")

            with cash_col:
                st.caption("Self-Pay Cash Price:")
                if pd.notna(row["cash_price"]) and row["cash_price"] > 0:
                    st.markdown(
                        f'<div style="font-size:1.2rem; font-weight:600; color:#334155">${row["cash_price"]:,.0f}</div>',
                        unsafe_allow_html=True,
                    )
                    if row["cash_price"] < row["estimated_oop"]:
                        st.caption("⚡ *Cheaper than insurance*")
                else:
                    st.markdown(
                        '<div style="color:#94A3B8; font-size:1.1rem;">Not Listed</div>',
                        unsafe_allow_html=True,
                    )

                st.button(
                    "Compare Details",
                    key=f"btn_{row['hospital_name']}",
                    use_container_width=True,
                )

# ----------------- COLLAPSIBLE TECHNICAL DRAWER -----------------
st.markdown("---")
with st.expander("📊 Investor & Actuarial Breakdown (Wholesale Spread & Distribution)"):
    tab1, tab2 = st.tabs(["Wholesale Price Spread", "Raw Contract Table"])

    with tab1:
        st.markdown(
            "Comparison of wholesale in-network contract rates negotiated by insurer vs. hospitals."
        )
        fig_bar = px.bar(
            summary_df,
            x="hospital_name",
            y="total_rate",
            color="total_rate",
            color_continuous_scale="Blues",
            labels={"total_rate": "Total Negotiated Rate ($)", "hospital_name": "Hospital"},
            title=f"Total Contract Rate Spread for CPT {current_cpt}",
        )
        fig_bar.update_layout(xaxis_tickangle=-30)
        st.plotly_chart(fig_bar, use_container_width=True)

    with tab2:
        st.dataframe(
            proc_df[[
                "cpt_code", "hospital_name", "payer_name", "billing_class", 
                "negotiated_rate", "discounted_cash"
            ]].sort_values(by="negotiated_rate"),
            use_container_width=True,
        )