"""
FastHTML web interface for the Simple Loan EMI Explorer.

Run:   python app_fasthtml.py        ->  http://localhost:5001

Uses the same emi_core.py engine and data/loans.json as the terminal app,
so loans saved anywhere appear everywhere. HTMX (bundled with FastHTML) gives
live EMI results while you type, with no page reloads and no JavaScript to write.
"""
import base64
import io
import json
import tempfile

import matplotlib.pyplot as plt
from fasthtml.common import *
from starlette.responses import RedirectResponse, Response

import emi_core as core
from emi_core import ValidationError, inr

CSS = """
.metric{text-align:center;margin:0}
.metric h3{margin:.2rem 0 0}
.err{color:#c62828;margin:.2rem 0}
.warn{background:#fff4d6;color:#7a5200;border-left:4px solid #f5a623;padding:.4rem .8rem;margin:.3rem 0;border-radius:4px}
.ok{background:#e6f6ea;color:#1b5e20;border-left:4px solid #43a047;padding:.4rem .8rem;border-radius:4px}
.scroll{overflow-x:auto}
table.dense td,table.dense th{padding:.35rem .6rem;font-size:.9rem;white-space:nowrap}
button.tiny{padding:.1rem .5rem;margin:0}
img.chart{max-width:100%;height:auto}
"""

app, rt = fast_app(hdrs=(Style(CSS),))

PAGE_SIZE = 15


# ------------------------------- helpers ----------------------------------- #
def layout(title, *content):
    links = [("Calculator", "/"), ("Records", "/records"), ("Analytics", "/analytics"),
             ("Import / Export", "/data")]
    return (
        Title(f"{title} · Loan EMI Explorer"),
        Main(
            Nav(Ul(Li(Strong("💰 Loan EMI Explorer"))),
                Ul(*[Li(A(label, href=href)) for label, href in links])),
            H1(title),
            *content,
            cls="container",
        ),
    )


def fig_img(fig, width=None):
    """Matplotlib figure -> inline <img> (base64 PNG)."""
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=110)
    plt.close(fig)
    src = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
    return Img(src=src, cls="chart", style=f"width:{width}px" if width else "")


def metric(label, value):
    return Article(Small(label), H3(value), cls="metric")


def to_months(tenure, unit):
    try:
        v = float(str(tenure).replace(",", "").strip())
    except ValueError:
        return tenure  # let the validator produce the error message
    return v * 12 if unit == "Years" else v


def records_table(recs, with_delete=True):
    heads = ["ID", "Name", "Category", "Amount", "Rate", "Months", "EMI", "", ""]
    rows = []
    for r in recs:
        w = core.get_warnings(r)
        rows.append(Tr(
            Td(r.id), Td(r.name), Td(r.category), Td(inr(r.principal)),
            Td(f"{r.annual_rate:g}%"), Td(r.tenure_months), Td(inr(r.emi)),
            Td(Span("⚠", title="; ".join(w)) if w else ""),
            Td(Button("🗑", cls="tiny outline secondary", hx_post=f"/delete/{r.id}",
                      hx_target="closest tr", hx_swap="outerHTML",
                      hx_confirm=f"Delete '{r.name}'?") if with_delete else ""),
        ))
    return Div(Table(Thead(Tr(*[Th(h) for h in heads])), Tbody(*rows), cls="dense"), cls="scroll")


# ------------------------------ calculator --------------------------------- #
def render_calc(name, category, principal, annual_rate, tenure, unit, monthly_income):
    months = to_months(tenure, unit)
    try:
        rec = core.build_record(name or "Untitled loan", category, principal,
                                annual_rate, months, monthly_income or None)
    except ValidationError as e:
        return Div(*[P("✗ " + m, cls="err") for m in str(e).split(" | ")])
    return Div(
        Grid(metric("Monthly EMI", inr(rec.emi)),
             metric("Total interest", inr(rec.total_interest)),
             metric("Total payable", inr(rec.total_payment))),
        *[Div("⚠ " + w, cls="warn") for w in core.get_warnings(rec)],
        Grid(fig_img(core.plot_payment_breakdown(rec.principal, rec.total_interest)),
             fig_img(core.plot_amortization(rec.principal, rec.annual_rate, rec.tenure_months))),
        Details(Summary("Amortization schedule"),
                Div(Table(
                    Thead(Tr(*[Th(h) for h in ["Month", "EMI", "Interest", "Principal", "Balance"]])),
                    Tbody(*[Tr(Td(s["month"]), Td(inr(s["emi"])), Td(inr(s["interest"])),
                               Td(inr(s["principal"])), Td(inr(s["balance"])))
                            for s in core.amortization_schedule(rec.principal, rec.annual_rate,
                                                                rec.tenure_months)]),
                    cls="dense"), cls="scroll", style="max-height:320px;overflow:auto")),
    )


@rt("/")
def index():
    live = dict(hx_post="/calc", hx_target="#result", hx_trigger="input delay:300ms, change")
    form = Form(
        Grid(
            Label("Loan name", Input(name="name", placeholder="e.g. Home loan - SBI")),
            Label("Category", Select(*[Option(c) for c in core.CATEGORIES], name="category")),
        ),
        Grid(
            Label("Loan amount (₹)", Input(name="principal", value="1000000", inputmode="decimal")),
            Label("Annual interest rate (%)", Input(name="annual_rate", value="9", inputmode="decimal")),
        ),
        Grid(
            Label("Tenure", Input(name="tenure", value="10", inputmode="decimal")),
            Label("Unit", Select(Option("Years"), Option("Months"), name="tenure_unit")),
            Label("Monthly income (optional)", Input(name="monthly_income", inputmode="decimal")),
        ),
        Button("💾 Save this loan", type="button", hx_post="/save", hx_include="#calc-form",
               hx_target="#msg"),
        Div(id="msg"),
        id="calc-form", **live,
    )
    first = render_calc("", "Home", "1000000", "9", "10", "Years", "")
    return layout("EMI Calculator", form, Hr(), Div(first, id="result"))


@rt("/calc", methods=["POST"])
def calc(name: str = "", category: str = "Other", principal: str = "", annual_rate: str = "",
        tenure: str = "", tenure_unit: str = "Years", monthly_income: str = ""):
    return render_calc(name, category, principal, annual_rate, tenure, tenure_unit, monthly_income)


@rt("/save", methods=["POST"])
def save(name: str = "", category: str = "Other", principal: str = "", annual_rate: str = "",
        tenure: str = "", tenure_unit: str = "Years", monthly_income: str = ""):
    if not name.strip():
        return P("✗ Give the loan a name before saving.", cls="err")
    try:
        rec = core.build_record(name, category, principal, annual_rate,
                                to_months(tenure, tenure_unit), monthly_income or None)
    except ValidationError as e:
        return Div(*[P("✗ " + m, cls="err") for m in str(e).split(" | ")])
    core.add_record(rec)
    return Div(f"✓ Saved '{rec.name}' with id {rec.id}. ", A("View records", href="/records"), cls="ok")


# -------------------------------- records ---------------------------------- #
@rt("/records")
def records(q: str = "", category: str = "All", min_emi: str = "", flagged: str = "", page: int = 1):
    try:
        lo = float(min_emi) if min_emi.strip() else None
    except ValueError:
        lo = None
    all_recs = core.load_records()
    found = core.search_records(all_recs, q, category, lo, None, bool(flagged))
    pages = max(1, -(-len(found) // PAGE_SIZE))
    page = min(max(page, 1), pages)
    chunk = found[(page - 1) * PAGE_SIZE: page * PAGE_SIZE]

    search = Form(
        Grid(
            Input(name="q", value=q, placeholder="Search name / category / id"),
            Select(*[Option(c, selected=(c == category)) for c in ["All"] + core.CATEGORIES],
                   name="category"),
            Input(name="min_emi", value=min_emi, placeholder="Min EMI"),
            Label(Input(type="checkbox", name="flagged", value="1", checked=bool(flagged)), "Flagged only"),
            Button("Search"),
        ),
        method="get", action="/records",
    )
    base = f"/records?q={q}&category={category}&min_emi={min_emi}&flagged={flagged}"
    pager = Div(
        A("← Prev", href=f"{base}&page={page - 1}") if page > 1 else "",
        Span(f"  Page {page} of {pages}  "),
        A("Next →", href=f"{base}&page={page + 1}") if page < pages else "",
    )
    body = (records_table(chunk), pager) if chunk else (
        P("No matching records. Save a loan from the Calculator or load the sample data."),)
    return layout(
        "Saved Loans", search,
        Small(f"{len(found)} of {len(all_recs)} record(s)"), *body,
        Form(Button("Load 40 sample loans", cls="outline"), method="post", action="/sample"),
    )


@rt("/delete/{rid}", methods=["POST"])
def delete_loan(rid: str):
    core.delete_record(rid)
    return ""


@rt("/sample", methods=["POST"])
def load_sample():
    try:
        data = json.loads((core.SAMPLE_DIR / "sample_loans.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        return layout("Sample data missing", P("Run ", Code("python generate_sample_data.py"), " first."))
    existing = core.load_records()
    known = {r.id for r in existing}
    core.save_records(existing + [core.LoanRecord.from_dict(d) for d in data if d["id"] not in known])
    return RedirectResponse("/records", status_code=303)


# -------------------------------- analytics -------------------------------- #
@rt("/analytics")
def analytics():
    recs = core.load_records()
    s = core.summarize(recs)
    if not s["count"]:
        return layout("Executive Summary", P("No data yet. Add a loan or load the sample data."),
                      Form(Button("Load 40 sample loans"), method="post", action="/sample"))
    return layout(
        "Executive Summary",
        Grid(metric("Loans", s["count"]), metric("Total principal", inr(s["total_principal"])),
             metric("Total interest", inr(s["total_interest"])),
             metric("Combined monthly EMI", inr(s["total_monthly_emi"]))),
        Grid(metric("Average EMI", inr(s["avg_emi"])), metric("Average rate", f"{s['avg_rate']:.2f}%"),
             metric("Average tenure", f"{s['avg_tenure']:.0f} months"),
             metric("Flagged loans", s["flagged_count"])),
        H3("Distribution"), fig_img(core.plot_category_distribution(recs)),
        Grid(fig_img(core.plot_emi_vs_principal(recs)), fig_img(core.plot_trend(recs))),
        H3("Highlights"),
        P(Strong("Highest EMI: "), f"{s['highest_emi'].name} ({inr(s['highest_emi'].emi)})"),
        P(Strong("Lowest EMI: "), f"{s['lowest_emi'].name} ({inr(s['lowest_emi'].emi)})"),
        *([H3("⚠ Threshold warnings")] + [
            Div(Strong(r.name), f" ({r.id}): " + "; ".join(core.get_warnings(r)), cls="warn")
            for r in s["flagged"]] if s["flagged"] else []),
    )


# ---------------------------- import / export ------------------------------ #
@rt("/data")
def data_page():
    upload = Form(
        Input(type="file", name="file", accept=".csv"),
        Button("Import CSV"),
        hx_post="/import", hx_target="#import-result", hx_encoding="multipart/form-data",
    )
    return layout(
        "Import / Export",
        H3("Bulk import"),
        Small("Columns: name, category, principal, annual_rate, tenure_months (or tenure_years), "
              "optional monthly_income."),
        upload, Div(id="import-result"),
        H3("Export reports"),
        Div(A("📄 Markdown summary", href="/export/md", role="button", cls="outline"), " ",
            A("🧾 Text audit log", href="/export/txt", role="button", cls="outline"), " ",
            A("📊 CSV report", href="/export/csv", role="button", cls="outline")),
    )


@rt("/import", methods=["POST"])
async def import_file(file: UploadFile):
    raw = await file.read()
    with tempfile.NamedTemporaryFile(delete=False, suffix=".csv") as tmp:
        tmp.write(raw)
    added, errors = core.import_csv(tmp.name)
    return Div(
        Div(f"✓ Imported {added} row(s).", cls="ok"),
        *([P(f"✗ {len(errors)} row(s) rejected:", cls="err"), Pre(Code("\n".join(errors)))]
          if errors else []),
    )


@rt("/export/{kind}")
def export(kind: str):
    recs = core.load_records()
    makers = {
        "md": (core.export_markdown, "text/markdown", "loans_summary.md"),
        "txt": (core.export_text_log, "text/plain", "audit_log.txt"),
        "csv": (core.export_csv, "text/csv", "loans_report.csv"),
    }
    if kind not in makers:
        return Response("Unknown export type", status_code=404)
    fn, mime, filename = makers[kind]
    text = fn(recs).read_text(encoding="utf-8")
    return Response(text, media_type=mime,
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


if __name__ == "__main__":
    serve()
