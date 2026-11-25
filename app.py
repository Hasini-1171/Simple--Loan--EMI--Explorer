"""Streamlit web interface for the Simple Loan EMI Explorer.  Run:  streamlit run app.py"""
import json
import tempfile

import pandas as pd
import streamlit as st

import emi_core as core
from emi_core import ValidationError, inr

st.set_page_config(page_title="Loan EMI Explorer", page_icon="💰", layout="wide")

# ---------------------------- sidebar navigation ---------------------------- #
st.sidebar.title("💰 Loan EMI Explorer")
page = st.sidebar.radio("Navigate", ["🧮 Calculator", "📂 Records", "📊 Analytics", "⬆️⬇️ Import / Export"])
records = core.load_records()
st.sidebar.caption(f"{len(records)} saved loan(s)")

if st.sidebar.button("Load sample data"):
    try:
        data = json.loads((core.SAMPLE_DIR / "sample_loans.json").read_text(encoding="utf-8"))
        known = {r.id for r in records}
        new = [core.LoanRecord.from_dict(d) for d in data if d["id"] not in known]
        core.save_records(records + new)
        st.sidebar.success(f"Added {len(new)} sample loans")
        st.rerun()
    except FileNotFoundError:
        st.sidebar.error("Run `python generate_sample_data.py` first.")


def records_df(recs):
    return pd.DataFrame([
        {"ID": r.id, "Name": r.name, "Category": r.category, "Amount": r.principal,
        "Rate %": r.annual_rate, "Months": r.tenure_months, "EMI": r.emi,
        "Total interest": r.total_interest, "Created": r.created_at[:10],
        "Warnings": "; ".join(core.get_warnings(r))} for r in recs])


def show_warnings(rec):
    for w in core.get_warnings(rec):
        st.warning(w, icon="⚠️")


# -------------------------------- Calculator -------------------------------- #
if page == "🧮 Calculator":
    st.title("🧮 EMI Calculator")
    left, right = st.columns([1, 1.4])
    with left:
        name = st.text_input("Loan name", placeholder="e.g. Home loan - SBI")
        category = st.selectbox("Category", core.CATEGORIES)
        principal = st.number_input("Loan amount (₹)", min_value=0.0, value=1_000_000.0, step=10_000.0)
        rate = st.number_input("Annual interest rate (%)", min_value=-1.0, value=9.0, step=0.1)
        unit = st.radio("Tenure unit", ["Years", "Months"], horizontal=True)
        tenure = st.number_input(f"Tenure ({unit.lower()})", min_value=0.0,
                                value=10.0 if unit == "Years" else 120.0, step=1.0)
        income = st.number_input("Monthly income (₹, optional)", min_value=0.0, value=0.0, step=5_000.0)
        months = tenure * 12 if unit == "Years" else tenure

        rec, error = None, None
        try:
            rec = core.build_record(name or "Untitled loan", category, principal, rate, months, income or None)
        except ValidationError as e:
            error = str(e)

    with right:
        if error:
            for msg in error.split(" | "):
                st.error(msg)
        else:
            m1, m2, m3 = st.columns(3)
            m1.metric("Monthly EMI", inr(rec.emi))
            m2.metric("Total interest", inr(rec.total_interest))
            m3.metric("Total payable", inr(rec.total_payment))
            show_warnings(rec)
            c1, c2 = st.columns([1, 2.4])
            c1.pyplot(core.plot_payment_breakdown(rec.principal, rec.total_interest))
            c2.pyplot(core.plot_amortization(rec.principal, rec.annual_rate, rec.tenure_months))
            if st.button("💾 Save this loan", type="primary"):
                if not name.strip():
                    st.error("Give the loan a name before saving.")
                else:
                    core.add_record(rec)
                    st.success(f"Saved as {rec.id}")
                    st.rerun()
            with st.expander("Amortization schedule"):
                st.dataframe(pd.DataFrame(core.amortization_schedule(
                    rec.principal, rec.annual_rate, rec.tenure_months)), use_container_width=True, hide_index=True)

# --------------------------------- Records ---------------------------------- #
elif page == "📂 Records":
    st.title("📂 Saved Loans")
    f1, f2, f3, f4 = st.columns([2, 1.2, 1.2, 1])
    kw = f1.text_input("Search name / category / id")
    cat = f2.selectbox("Category", ["All"] + core.CATEGORIES)
    emi_lo = f3.number_input("Min EMI", min_value=0.0, value=0.0, step=1000.0)
    flagged_only = f4.checkbox("Flagged only")
    found = core.search_records(records, kw, cat, emi_lo or None, None, flagged_only)
    st.caption(f"{len(found)} of {len(records)} record(s)")
    if found:
        st.dataframe(records_df(found), use_container_width=True, hide_index=True)
        d1, d2 = st.columns([2, 1])
        target = d1.selectbox("Delete a loan", [""] + [f"{r.id} - {r.name}" for r in found])
        if d2.button("🗑️ Delete") and target:
            core.delete_record(target.split(" - ")[0])
            st.success("Deleted.")
            st.rerun()
    else:
        st.info("No matching records. Save a loan from the Calculator or load sample data.")

# -------------------------------- Analytics --------------------------------- #
elif page == "📊 Analytics":
    st.title("📊 Executive Summary")
    s = core.summarize(records)
    if not s["count"]:
        st.info("No data yet.")
    else:
        a = st.columns(4)
        a[0].metric("Loans", s["count"])
        a[1].metric("Total principal", inr(s["total_principal"]))
        a[2].metric("Total interest", inr(s["total_interest"]))
        a[3].metric("Combined monthly EMI", inr(s["total_monthly_emi"]))
        b = st.columns(4)
        b[0].metric("Average EMI", inr(s["avg_emi"]))
        b[1].metric("Average rate", f"{s['avg_rate']:.2f}%")
        b[2].metric("Average tenure", f"{s['avg_tenure']:.0f} mo")
        b[3].metric("Flagged loans", s["flagged_count"])
        st.pyplot(core.plot_category_distribution(records))
        c1, c2 = st.columns(2)
        c1.pyplot(core.plot_emi_vs_principal(records))
        c2.pyplot(core.plot_trend(records))
        if s["flagged"]:
            st.subheader("⚠️ Threshold warnings")
            for r in s["flagged"]:
                st.warning(f"**{r.name}** ({r.id}): " + "; ".join(core.get_warnings(r)))

# ----------------------------- Import / Export ------------------------------ #
else:
    st.title("⬆️⬇️ Import / Export")
    st.subheader("Bulk import (CSV)")
    st.caption("Columns: name, category, principal, annual_rate, tenure_months (or tenure_years), "
            "optional monthly_income.")
    up = st.file_uploader("Choose a CSV file", type="csv")
    if up and st.button("Import"):
        with tempfile.NamedTemporaryFile(delete=False, suffix=".csv") as tmp:
            tmp.write(up.getvalue())
        added, errors = core.import_csv(tmp.name)
        st.success(f"Imported {added} row(s).")
        if errors:
            st.error(f"{len(errors)} row(s) rejected:")
            st.code("\n".join(errors))
        if added:
            st.rerun() if not errors else None

    st.subheader("Export reports")
    if not records:
        st.info("Nothing to export yet.")
    else:
        e1, e2, e3 = st.columns(3)
        e1.download_button("📄 Markdown summary", core.export_markdown(records).read_text(encoding="utf-8"),
                        "loans_summary.md", "text/markdown")
        e2.download_button("🧾 Text audit log", core.export_text_log(records).read_text(encoding="utf-8"),
                        "audit_log.txt", "text/plain")
        e3.download_button("📊 CSV report", core.export_csv(records).read_text(encoding="utf-8"),
                        "loans_report.csv", "text/csv")
