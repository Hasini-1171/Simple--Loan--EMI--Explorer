"""
emi_core.py - Core engine for the Simple Loan EMI Explorer.

Everything that is not user interface lives here so that the terminal app (cli.py)
and the Streamlit app (app.py) share exactly the same logic:

  * EMI maths + amortization schedule
  * input validation
  * JSON persistence (with corrupted-file recovery)
  * search / filtering
  * summary analytics + threshold warnings
  * Matplotlib charts
  * Markdown / text / CSV export and bulk CSV import
"""
from __future__ import annotations

import csv
import json
import math
import uuid
from dataclasses import asdict, dataclass, fields
from datetime import datetime
from pathlib import Path
from statistics import mean

import matplotlib

matplotlib.use("Agg")  # headless-safe backend; works for CLI, Streamlit and servers
import matplotlib.pyplot as plt  # noqa: E402

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
EXPORT_DIR = BASE_DIR / "exports"
SAMPLE_DIR = BASE_DIR / "sample_data"
DB_PATH = DATA_DIR / "loans.json"
IMPORT_LOG = DATA_DIR / "import_errors.log"

CATEGORIES = ["Home", "Car", "Personal", "Education", "Business", "Gold", "Other"]

LIMITS = {
    "principal": (1_000, 100_000_000),   # INR
    "annual_rate": (0, 50),              # % per year
    "tenure_months": (1, 480),           # up to 40 years
}

# Threshold warning limits
HIGH_RATE = 20.0
LONG_TENURE_MONTHS = 360
EMI_INCOME_LIMIT = 0.50
LARGE_PRINCIPAL = 50_000_000


class ValidationError(ValueError):
    """Raised with a human-readable message when user input is invalid."""


# --------------------------------------------------------------------------- #
# Data model
# --------------------------------------------------------------------------- #
@dataclass
class LoanRecord:
    id: str
    name: str
    category: str
    principal: float
    annual_rate: float
    tenure_months: int
    emi: float
    total_payment: float
    total_interest: float
    created_at: str
    monthly_income: float | None = None

    @classmethod
    def from_dict(cls, d: dict) -> "LoanRecord":
        names = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in names})

    def to_dict(self) -> dict:
        return asdict(self)


# --------------------------------------------------------------------------- #
# Formatting helpers
# --------------------------------------------------------------------------- #
def inr(x: float) -> str:
    """Format a number using Indian digit grouping, e.g. 12,34,567.00"""
    neg = x < 0
    whole, dec = f"{abs(x):.2f}".split(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        whole = ",".join(parts + [tail])
    return f"{'-' if neg else ''}₹{whole}.{dec}"


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #
def parse_number(value, label: str) -> float:
    """Convert user text/number to a finite float or raise ValidationError."""
    if value is None or (isinstance(value, str) and not value.strip()):
        raise ValidationError(f"{label} cannot be blank.")
    try:
        num = float(str(value).replace(",", "").replace("₹", "").strip())
    except ValueError:
        raise ValidationError(f"{label} must be a number (got '{value}').") from None
    if math.isnan(num) or math.isinf(num):
        raise ValidationError(f"{label} must be a finite number.")
    return num


def validate_loan(name, category, principal, annual_rate, tenure_months,
                  monthly_income=None) -> dict:
    """Validate and normalise one loan entry. Raises ValidationError listing ALL problems."""
    errors: list[str] = []
    clean: dict = {}

    name = (str(name) if name is not None else "").strip()
    if not name:
        errors.append("Name cannot be blank.")
    elif len(name) > 60:
        errors.append("Name must be 60 characters or fewer.")
    clean["name"] = name

    cat = (str(category) if category is not None else "").strip().title()
    if cat not in CATEGORIES:
        errors.append(f"Category must be one of: {', '.join(CATEGORIES)}.")
    clean["category"] = cat

    def numeric(key, raw, label, integer=False):
        try:
            v = parse_number(raw, label)
        except ValidationError as e:
            errors.append(str(e))
            return
        lo, hi = LIMITS[key]
        if integer and v != int(v):
            errors.append(f"{label} must be a whole number.")
        elif not lo <= v <= hi:
            errors.append(f"{label} must be between {lo:,} and {hi:,}.")
        else:
            clean[key] = int(v) if integer else v

    numeric("principal", principal, "Loan amount")
    numeric("annual_rate", annual_rate, "Interest rate (%)")
    numeric("tenure_months", tenure_months, "Tenure (months)", integer=True)

    clean["monthly_income"] = None
    if monthly_income not in (None, "", 0, "0"):
        try:
            inc = parse_number(monthly_income, "Monthly income")
            if inc <= 0:
                errors.append("Monthly income must be positive.")
            else:
                clean["monthly_income"] = inc
        except ValidationError as e:
            errors.append(str(e))

    if errors:
        raise ValidationError(" | ".join(errors))
    return clean


# --------------------------------------------------------------------------- #
# Core business logic
# --------------------------------------------------------------------------- #
def calculate_emi(principal: float, annual_rate: float, tenure_months: int) -> float:
    """
    EMI = P * r * (1+r)^n / ((1+r)^n - 1)
    where r = annual_rate / 12 / 100 and n = tenure in months.
    A 0% loan is simply P / n.
    """
    r = annual_rate / 12 / 100
    n = tenure_months
    if r == 0:
        return round(principal / n, 2)
    factor = (1 + r) ** n
    return round(principal * r * factor / (factor - 1), 2)


def amortization_schedule(principal: float, annual_rate: float, tenure_months: int) -> list[dict]:
    """Month-by-month split of each EMI into interest and principal."""
    r = annual_rate / 12 / 100
    emi = calculate_emi(principal, annual_rate, tenure_months)
    balance = principal
    rows = []
    for m in range(1, tenure_months + 1):
        interest = balance * r
        pay = emi if m < tenure_months else balance + interest  # last payment clears rounding drift
        princ = pay - interest
        balance = max(balance - princ, 0.0)
        rows.append({"month": m, "emi": round(pay, 2), "interest": round(interest, 2),
                     "principal": round(princ, 2), "balance": round(balance, 2)})
    return rows


def build_record(name, category, principal, annual_rate, tenure_months,
                 monthly_income=None, created_at: str | None = None) -> LoanRecord:
    c = validate_loan(name, category, principal, annual_rate, tenure_months, monthly_income)
    emi = calculate_emi(c["principal"], c["annual_rate"], c["tenure_months"])
    total = round(emi * c["tenure_months"], 2)
    return LoanRecord(
        id=uuid.uuid4().hex[:8], name=c["name"], category=c["category"],
        principal=c["principal"], annual_rate=c["annual_rate"],
        tenure_months=c["tenure_months"], emi=emi, total_payment=total,
        total_interest=round(total - c["principal"], 2),
        created_at=created_at or datetime.now().isoformat(timespec="seconds"),
        monthly_income=c["monthly_income"],
    )


def get_warnings(rec: LoanRecord) -> list[str]:
    """Threshold warning indicators for one record."""
    w = []
    if rec.annual_rate > HIGH_RATE:
        w.append(f"High interest rate ({rec.annual_rate:g}% > {HIGH_RATE:g}%)")
    if rec.annual_rate == 0:
        w.append("Zero-interest loan (verify entry)")
    if rec.tenure_months > LONG_TENURE_MONTHS:
        w.append(f"Very long tenure ({rec.tenure_months} months)")
    if rec.total_interest > rec.principal:
        w.append("Total interest exceeds the amount borrowed")
    if rec.principal >= LARGE_PRINCIPAL:
        w.append("Unusually large loan amount")
    if rec.monthly_income and rec.emi / rec.monthly_income > EMI_INCOME_LIMIT:
        w.append(f"EMI is {rec.emi / rec.monthly_income:.0%} of income (limit {EMI_INCOME_LIMIT:.0%})")
    return w


# --------------------------------------------------------------------------- #
# Persistence (JSON)
# --------------------------------------------------------------------------- #
def load_records(path: Path = DB_PATH) -> list[LoanRecord]:
    """Load records; a missing or corrupted file never crashes the app."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, list):
            raise json.JSONDecodeError("root must be a list", "", 0)
    except FileNotFoundError:
        save_records([], path)
        return []
    except (json.JSONDecodeError, UnicodeDecodeError):
        backup = path.with_suffix(f".corrupt-{datetime.now():%Y%m%d%H%M%S}.json")
        path.replace(backup)  # keep the bad file for inspection
        save_records([], path)
        return []
    records = []
    for item in raw:  # schema check: skip entries that don't fit the model
        try:
            records.append(LoanRecord.from_dict(item))
        except TypeError:
            continue
    return records


def save_records(records: list[LoanRecord], path: Path = DB_PATH) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")  # write-then-rename so a crash can't half-write the DB
    tmp.write_text(json.dumps([r.to_dict() for r in records], indent=2, ensure_ascii=False),
                   encoding="utf-8")
    tmp.replace(path)


def add_record(rec: LoanRecord, path: Path = DB_PATH) -> None:
    records = load_records(path)
    records.append(rec)
    save_records(records, path)


def delete_record(rec_id: str, path: Path = DB_PATH) -> bool:
    records = load_records(path)
    kept = [r for r in records if r.id != rec_id.strip()]
    if len(kept) == len(records):
        return False
    save_records(kept, path)
    return True


# --------------------------------------------------------------------------- #
# Search / filter
# --------------------------------------------------------------------------- #
def search_records(records, keyword: str = "", category: str | None = None,
                   min_emi: float | None = None, max_emi: float | None = None,
                   only_warnings: bool = False) -> list[LoanRecord]:
    kw = keyword.strip().lower()
    out = []
    for r in records:
        if kw and kw not in r.name.lower() and kw not in r.category.lower() and kw != r.id:
            continue
        if category and category != "All" and r.category.lower() != category.lower():
            continue
        if min_emi is not None and r.emi < min_emi:
            continue
        if max_emi is not None and r.emi > max_emi:
            continue
        if only_warnings and not get_warnings(r):
            continue
        out.append(r)
    return out


# --------------------------------------------------------------------------- #
# Summary analytics
# --------------------------------------------------------------------------- #
def summarize(records: list[LoanRecord]) -> dict:
    if not records:
        return {"count": 0}
    by_cat: dict[str, dict] = {}
    by_month: dict[str, dict] = {}
    for r in records:
        c = by_cat.setdefault(r.category, {"count": 0, "principal": 0.0, "emi": 0.0})
        c["count"] += 1
        c["principal"] += r.principal
        c["emi"] += r.emi
        m = by_month.setdefault(r.created_at[:7], {"count": 0, "principal": 0.0})
        m["count"] += 1
        m["principal"] += r.principal
    flagged = [r for r in records if get_warnings(r)]
    return {
        "count": len(records),
        "total_principal": sum(r.principal for r in records),
        "total_interest": sum(r.total_interest for r in records),
        "total_payable": sum(r.total_payment for r in records),
        "total_monthly_emi": sum(r.emi for r in records),
        "avg_principal": mean(r.principal for r in records),
        "avg_emi": mean(r.emi for r in records),
        "avg_rate": mean(r.annual_rate for r in records),
        "avg_tenure": mean(r.tenure_months for r in records),
        "highest_emi": max(records, key=lambda r: r.emi),
        "lowest_emi": min(records, key=lambda r: r.emi),
        "by_category": dict(sorted(by_cat.items())),
        "by_month": dict(sorted(by_month.items())),
        "flagged_count": len(flagged),
        "flagged": flagged,
    }


# --------------------------------------------------------------------------- #
# Charts (all return a Matplotlib Figure; pass `save_to` to write a PNG)
# --------------------------------------------------------------------------- #
def _finish(fig, save_to):
    fig.tight_layout()
    if save_to:
        Path(save_to).parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(save_to, dpi=150)
    return fig


def plot_category_distribution(records, save_to=None):
    s = summarize(records)
    fig, ax = plt.subplots(1, 2, figsize=(11, 4.2))
    if s["count"]:
        cats = list(s["by_category"])
        ax[0].bar(cats, [s["by_category"][c]["count"] for c in cats], color="#4C78A8")
        ax[1].bar(cats, [s["by_category"][c]["principal"] / 1e5 for c in cats], color="#F58518")
    ax[0].set_title("Loans per category")
    ax[0].set_ylabel("Count")
    ax[1].set_title("Total principal per category")
    ax[1].set_ylabel("₹ lakh")
    for a in ax:
        a.tick_params(axis="x", rotation=30)
        a.grid(axis="y", alpha=0.3)
    return _finish(fig, save_to)


def plot_emi_vs_principal(records, save_to=None):
    fig, ax = plt.subplots(figsize=(7, 4.5))
    for cat in sorted({r.category for r in records}):
        pts = [(r.principal / 1e5, r.emi) for r in records if r.category == cat]
        ax.scatter(*zip(*pts), label=cat, s=45, alpha=0.8)
    ax.set_xlabel("Loan amount (₹ lakh)")
    ax.set_ylabel("Monthly EMI (₹)")
    ax.set_title("EMI vs loan amount")
    ax.grid(alpha=0.3)
    if records:
        ax.legend(fontsize=8)
    return _finish(fig, save_to)


def plot_trend(records, save_to=None):
    s = summarize(records)
    fig, ax = plt.subplots(figsize=(8, 4.2))
    if s["count"]:
        months = list(s["by_month"])
        ax.plot(months, [s["by_month"][m]["principal"] / 1e5 for m in months],
                marker="o", color="#54A24B")
    ax.set_title("Loan amount recorded per month")
    ax.set_ylabel("₹ lakh")
    ax.tick_params(axis="x", rotation=45)
    ax.grid(alpha=0.3)
    return _finish(fig, save_to)


def plot_amortization(principal, annual_rate, tenure_months, save_to=None):
    sched = amortization_schedule(principal, annual_rate, tenure_months)
    months = [r["month"] for r in sched]
    fig, ax = plt.subplots(1, 2, figsize=(11, 4.2))
    ax[0].stackplot(months, [r["principal"] for r in sched], [r["interest"] for r in sched],
                    labels=["Principal part", "Interest part"], colors=["#4C78A8", "#E45756"])
    ax[0].set_title("Where each EMI goes")
    ax[0].set_xlabel("Month")
    ax[0].set_ylabel("₹")
    ax[0].legend(loc="center right")
    ax[1].plot(months, [r["balance"] for r in sched], color="#72B7B2")
    ax[1].set_title("Outstanding balance")
    ax[1].set_xlabel("Month")
    ax[1].grid(alpha=0.3)
    return _finish(fig, save_to)


def plot_payment_breakdown(principal, total_interest, save_to=None):
    fig, ax = plt.subplots(figsize=(4.2, 4.2))
    ax.pie([principal, total_interest], labels=["Principal", "Interest"], autopct="%1.1f%%",
           colors=["#4C78A8", "#E45756"], startangle=90)
    ax.set_title("Total payment breakdown")
    return _finish(fig, save_to)


# --------------------------------------------------------------------------- #
# Export
# --------------------------------------------------------------------------- #
CSV_FIELDS = ["id", "name", "category", "principal", "annual_rate", "tenure_months", "emi",
              "total_payment", "total_interest", "monthly_income", "created_at"]


def export_csv(records, path=None) -> Path:
    path = Path(path or EXPORT_DIR / "loans_report.csv")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
        w.writeheader()
        for r in records:
            w.writerow(r.to_dict())
    return path


def export_markdown(records, path=None) -> Path:
    path = Path(path or EXPORT_DIR / "loans_summary.md")
    path.parent.mkdir(parents=True, exist_ok=True)
    s = summarize(records)
    L = [f"# Loan EMI Explorer - Summary Report",
         f"_Generated {datetime.now():%Y-%m-%d %H:%M}_", ""]
    if not s["count"]:
        L.append("No records available.")
    else:
        L += ["## Key metrics", "", "| Metric | Value |", "|---|---|",
              f"| Loans recorded | {s['count']} |",
              f"| Total principal | {inr(s['total_principal'])} |",
              f"| Total interest | {inr(s['total_interest'])} |",
              f"| Total payable | {inr(s['total_payable'])} |",
              f"| Combined monthly EMI | {inr(s['total_monthly_emi'])} |",
              f"| Average EMI | {inr(s['avg_emi'])} |",
              f"| Average rate | {s['avg_rate']:.2f}% |",
              f"| Average tenure | {s['avg_tenure']:.0f} months |",
              f"| Flagged records | {s['flagged_count']} |", "",
              "## By category", "", "| Category | Loans | Principal | Total EMI |", "|---|---|---|---|"]
        for c, v in s["by_category"].items():
            L.append(f"| {c} | {v['count']} | {inr(v['principal'])} | {inr(v['emi'])} |")
        L += ["", "## All records", "",
              "| ID | Name | Category | Amount | Rate | Months | EMI |", "|---|---|---|---|---|---|---|"]
        for r in records:
            L.append(f"| {r.id} | {r.name} | {r.category} | {inr(r.principal)} | "
                     f"{r.annual_rate:g}% | {r.tenure_months} | {inr(r.emi)} |")
        if s["flagged"]:
            L += ["", "## Warnings", ""]
            for r in s["flagged"]:
                L.append(f"- **{r.name}** ({r.id}): " + "; ".join(get_warnings(r)))
    path.write_text("\n".join(L) + "\n", encoding="utf-8")
    return path


def export_text_log(records, path=None) -> Path:
    path = Path(path or EXPORT_DIR / "audit_log.txt")
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"LOAN EMI EXPLORER AUDIT LOG - {datetime.now():%Y-%m-%d %H:%M:%S}", "=" * 70]
    for r in records:
        lines.append(f"[{r.created_at}] {r.id} | {r.name} | {r.category} | "
                     f"P={inr(r.principal)} R={r.annual_rate:g}% N={r.tenure_months} -> EMI={inr(r.emi)}")
        for w in get_warnings(r):
            lines.append(f"    WARNING: {w}")
    lines.append("=" * 70)
    lines.append(f"Total records: {len(records)}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


# --------------------------------------------------------------------------- #
# Bulk import
# --------------------------------------------------------------------------- #
def import_csv(path, db_path: Path = DB_PATH) -> tuple[int, list[str]]:
    """
    Import a legacy CSV. Required columns: name, category, principal, annual_rate,
    and either tenure_months or tenure_years. Optional: monthly_income.
    Returns (rows_added, error_messages); errors are also written to import_errors.log.
    """
    errors: list[str] = []
    added: list[LoanRecord] = []
    try:
        with open(path, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            cols = {(c or "").strip().lower() for c in (reader.fieldnames or [])}
            needed = {"name", "category", "principal", "annual_rate"}
            if not needed <= cols or not ({"tenure_months", "tenure_years"} & cols):
                msg = (f"Missing columns. Need {sorted(needed)} plus tenure_months or tenure_years; "
                       f"found {sorted(cols)}")
                _write_import_log([msg])
                return 0, [msg]
            for line_no, row in enumerate(reader, start=2):
                row = {(k or "").strip().lower(): v for k, v in row.items()}
                try:
                    months = row.get("tenure_months")
                    if not (months or "").strip():
                        months = parse_number(row.get("tenure_years"), "Tenure (years)") * 12
                    added.append(build_record(row.get("name"), row.get("category"),
                                              row.get("principal"), row.get("annual_rate"),
                                              months, row.get("monthly_income")))
                except ValidationError as e:
                    errors.append(f"Row {line_no}: {e}")
    except FileNotFoundError:
        return 0, [f"File not found: {path}"]
    except (UnicodeDecodeError, csv.Error) as e:
        return 0, [f"Could not read file: {e}"]

    if added:
        existing = load_records(db_path)
        save_records(existing + added, db_path)
    _write_import_log(errors)
    return len(added), errors


def _write_import_log(errors: list[str]) -> None:
    IMPORT_LOG.parent.mkdir(parents=True, exist_ok=True)
    with IMPORT_LOG.open("a", encoding="utf-8") as f:
        f.write(f"--- import at {datetime.now():%Y-%m-%d %H:%M:%S} ({len(errors)} errors) ---\n")
        for e in errors:
            f.write(e + "\n")
