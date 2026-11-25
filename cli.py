"""Terminal interface for the Simple Loan EMI Explorer. Run:  python cli.py"""
import json
import sys

import emi_core as core
from emi_core import ValidationError, inr

# ANSI colours (disabled automatically when output isn't a terminal)
_COLOUR = sys.stdout.isatty()


def c(text, code):
    return f"\033[{code}m{text}\033[0m" if _COLOUR else str(text)


BOLD, RED, GREEN, YELLOW, CYAN, DIM = "1", "31", "32", "33", "36", "2"

BANNER = r"""
  _____ __  __ ___   _____            _
 | ____|  \/  |_ _| | ____|_  ___ __ | | ___  _ __ ___ _ __
 |  _| | |\/| || |  |  _| \ \/ / '_ \| |/ _ \| '__/ _ \ '__|
 | |___| |  | || |  | |___ >  <| |_) | | (_) | | |  __/ |
 |_____|_|  |_|___| |_____/_/\_\ .__/|_|\___/|_|  \___|_|
                               |_|     Simple Loan EMI Explorer
"""

MENU = """
  1  Calculate EMI & save loan        6  Charts (save PNG)
  2  View all loans                   7  Export reports
  3  Search / filter                  8  Bulk import CSV
  4  Executive summary                9  Delete a loan
  5  Amortization schedule           10  Load sample data
  0  Exit
"""


# ----------------------------- input helpers -------------------------------- #
def ask(prompt, validator, default=None):
    """Loop until `validator(text)` succeeds. Ctrl+C / EOF returns to the menu."""
    suffix = f" [{default}]" if default not in (None, "") else ""
    while True:
        try:
            raw = input(c(f"  {prompt}{suffix}: ", CYAN)).strip()
        except (EOFError, KeyboardInterrupt):
            raise KeyboardInterrupt from None
        if not raw and default is not None:
            raw = str(default)
        try:
            return validator(raw)
        except ValidationError as e:
            print(c(f"  ✗ {e}", RED))


def v_name(t):
    return core.validate_loan(t, "Other", 1000, 0, 1)["name"]


def v_category(t):
    t = t.strip().title()
    if t.isdigit() and 1 <= int(t) <= len(core.CATEGORIES):
        t = core.CATEGORIES[int(t) - 1]
    if t not in core.CATEGORIES:
        raise ValidationError(f"Pick a number 1-{len(core.CATEGORIES)} or a name from the list.")
    return t


def v_range(key, label, integer=False):
    def check(t):
        v = core.parse_number(t, label)
        lo, hi = core.LIMITS[key]
        if integer and v != int(v):
            raise ValidationError(f"{label} must be a whole number.")
        if not lo <= v <= hi:
            raise ValidationError(f"{label} must be between {lo:,} and {hi:,}.")
        return int(v) if integer else v
    return check


def v_income(t):
    if not t:
        return None
    v = core.parse_number(t, "Monthly income")
    if v <= 0:
        raise ValidationError("Monthly income must be positive.")
    return v


def v_tenure(t):
    """Accept '60' (months) or '5y' (years)."""
    t = t.lower().replace(" ", "")
    if t.endswith("y"):
        months = core.parse_number(t[:-1], "Tenure") * 12
    else:
        months = core.parse_number(t, "Tenure")
    return v_range("tenure_months", "Tenure (months)", integer=True)(str(months))


def confirm(prompt):
    try:
        return input(c(f"  {prompt} (y/n): ", CYAN)).strip().lower().startswith("y")
    except (EOFError, KeyboardInterrupt):
        return False


# ------------------------------- display ------------------------------------ #
def table(records, page_size=15):
    if not records:
        print(c("\n  No records to show.", YELLOW))
        return
    head = f"  {'ID':<9}{'Name':<30}{'Category':<11}{'Amount':>17}{'Rate':>7}{'Mths':>6}{'EMI':>15}  !"
    total = len(records)
    for start in range(0, total, page_size):
        print("\n" + c(head, BOLD))
        print("  " + "-" * (len(head) - 2))
        for r in records[start:start + page_size]:
            flag = c("⚠", YELLOW) if core.get_warnings(r) else " "
            print(f"  {r.id:<9}{r.name[:28]:<30}{r.category:<11}{inr(r.principal):>17}"
                  f"{r.annual_rate:>6.2f}%{r.tenure_months:>6}{inr(r.emi):>15}  {flag}")
        shown = min(start + page_size, total)
        print(c(f"\n  Showing {shown} of {total}", DIM))
        if shown < total:
            try:
                if input(c("  Enter = next page, q = stop: ", CYAN)).strip().lower() == "q":
                    break
            except (EOFError, KeyboardInterrupt):
                break


def show_warnings(rec):
    for w in core.get_warnings(rec):
        print(c(f"  ⚠ WARNING: {w}", YELLOW))


# ------------------------------- actions ------------------------------------ #
def act_calculate():
    print(c("\n  — New loan —", BOLD))
    name = ask("Loan name", v_name)
    print("  Categories: " + ", ".join(f"{i}={n}" for i, n in enumerate(core.CATEGORIES, 1)))
    cat = ask("Category", v_category)
    principal = ask("Loan amount (₹)", v_range("principal", "Loan amount"))
    rate = ask("Annual interest rate (%)", v_range("annual_rate", "Interest rate (%)"))
    months = ask("Tenure (months, or e.g. 5y for years)", v_tenure)
    income = ask("Monthly income (optional, Enter to skip)", v_income, default="")
    rec = core.build_record(name, cat, principal, rate, months, income)

    print(c("\n  ┌── RESULT ─────────────────────────────", GREEN))
    print(c(f"  │ Monthly EMI     : {inr(rec.emi)}", GREEN))
    print(f"  │ Total interest  : {inr(rec.total_interest)}")
    print(f"  │ Total payable   : {inr(rec.total_payment)}")
    print(c("  └───────────────────────────────────────", GREEN))
    show_warnings(rec)
    if confirm("Save this loan?"):
        core.add_record(rec)
        print(c(f"  ✓ Saved with id {rec.id}", GREEN))


def act_view():
    table(core.load_records())


def act_search():
    records = core.load_records()
    kw = input(c("  Keyword in name/category (Enter to skip): ", CYAN)).strip()
    cat = input(c(f"  Category [{'/'.join(core.CATEGORIES)}] (Enter = all): ", CYAN)).strip().title()
    lo = ask("Min EMI (Enter to skip)", lambda t: core.parse_number(t, "Min EMI") if t else None, default="")
    hi = ask("Max EMI (Enter to skip)", lambda t: core.parse_number(t, "Max EMI") if t else None, default="")
    warn = confirm("Only show flagged loans?")
    table(core.search_records(records, kw, cat or None, lo, hi, warn))


def act_summary():
    records = core.load_records()
    s = core.summarize(records)
    if not s["count"]:
        print(c("\n  No data yet. Add a loan or load sample data (option 10).", YELLOW))
        return
    print(c("\n  ══════ EXECUTIVE SUMMARY ══════", BOLD))
    for label, val in [
        ("Loans recorded", s["count"]),
        ("Total principal", inr(s["total_principal"])),
        ("Total interest", inr(s["total_interest"])),
        ("Total payable", inr(s["total_payable"])),
        ("Combined monthly EMI", inr(s["total_monthly_emi"])),
        ("Average EMI", inr(s["avg_emi"])),
        ("Average rate", f"{s['avg_rate']:.2f}%"),
        ("Average tenure", f"{s['avg_tenure']:.0f} months"),
        ("Highest EMI", f"{s['highest_emi'].name} ({inr(s['highest_emi'].emi)})"),
        ("Lowest EMI", f"{s['lowest_emi'].name} ({inr(s['lowest_emi'].emi)})"),
    ]:
        print(f"  {label:<24}: {val}")
    print(c("\n  By category", BOLD))
    for cat, v in s["by_category"].items():
        print(f"  {cat:<11}{v['count']:>4} loans  {inr(v['principal']):>18}")
    print(c("\n  Trend (loans recorded per month)", BOLD))
    peak = max(m["count"] for m in s["by_month"].values())
    for month, m in s["by_month"].items():
        print(f"  {month}  {'█' * max(1, round(m['count'] / peak * 25))} {m['count']}")
    if s["flagged_count"]:
        print(c(f"\n  ⚠ {s['flagged_count']} loan(s) breach a threshold:", YELLOW))
        for r in s["flagged"][:10]:
            print(c(f"    {r.name}: {'; '.join(core.get_warnings(r))}", YELLOW))
        if s["flagged_count"] > 10:
            print(c(f"    …and {s['flagged_count'] - 10} more (use Search → flagged only)", DIM))
    else:
        print(c("\n  ✓ No threshold warnings.", GREEN))


def act_amortization():
    records = core.load_records()
    rid = input(c("  Loan id (Enter to enter values manually): ", CYAN)).strip()
    rec = next((r for r in records if r.id == rid), None)
    if rec:
        p, r, n = rec.principal, rec.annual_rate, rec.tenure_months
    else:
        p = ask("Loan amount (₹)", v_range("principal", "Loan amount"))
        r = ask("Annual interest rate (%)", v_range("annual_rate", "Interest rate (%)"))
        n = ask("Tenure (months, or e.g. 5y)", v_tenure)
    sched = core.amortization_schedule(p, r, n)
    print(c(f"\n  {'Month':>6}{'EMI':>15}{'Interest':>15}{'Principal':>15}{'Balance':>18}", BOLD))
    for i, row in enumerate(sched):
        print(f"  {row['month']:>6}{inr(row['emi']):>15}{inr(row['interest']):>15}"
              f"{inr(row['principal']):>15}{inr(row['balance']):>18}")
        if (i + 1) % 24 == 0 and i + 1 < len(sched):
            if input(c("  Enter = more, q = stop: ", CYAN)).strip().lower() == "q":
                break


def act_charts():
    records = core.load_records()
    if not records:
        print(c("\n  No data to plot yet.", YELLOW))
        return
    out = core.EXPORT_DIR
    core.plot_category_distribution(records, out / "chart_categories.png")
    core.plot_emi_vs_principal(records, out / "chart_emi_vs_principal.png")
    core.plot_trend(records, out / "chart_trend.png")
    print(c(f"\n  ✓ Saved 3 charts in {out}", GREEN))


def act_export():
    records = core.load_records()
    if not records:
        print(c("\n  Nothing to export.", YELLOW))
        return
    for p in (core.export_markdown(records), core.export_text_log(records), core.export_csv(records)):
        print(c(f"  ✓ {p}", GREEN))


def act_import():
    path = input(c("  Path to CSV file: ", CYAN)).strip().strip('"')
    added, errors = core.import_csv(path)
    print(c(f"\n  ✓ Imported {added} row(s).", GREEN))
    if errors:
        print(c(f"  ✗ {len(errors)} row(s) rejected (logged to {core.IMPORT_LOG}):", RED))
        for e in errors[:15]:
            print(c(f"    {e}", RED))


def act_delete():
    rid = input(c("  Loan id to delete: ", CYAN)).strip()
    if not rid:
        return
    if confirm(f"Really delete {rid}?"):
        print(c("  ✓ Deleted.", GREEN) if core.delete_record(rid) else c("  ✗ No such id.", RED))


def act_sample():
    path = core.SAMPLE_DIR / "sample_loans.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        print(c("  Run `python generate_sample_data.py` first.", RED))
        return
    existing = core.load_records()
    known = {r.id for r in existing}
    new = [core.LoanRecord.from_dict(d) for d in data if d["id"] not in known]
    core.save_records(existing + new)
    print(c(f"  ✓ Added {len(new)} sample loans.", GREEN))


ACTIONS = {"1": act_calculate, "2": act_view, "3": act_search, "4": act_summary,
           "5": act_amortization, "6": act_charts, "7": act_export, "8": act_import,
           "9": act_delete, "10": act_sample}


def main():
    print(c(BANNER, CYAN))
    n = len(core.load_records())  # also bootstraps / repairs the data file
    print(c(f"  {n} saved loan(s) loaded.", DIM))
    while True:
        print(MENU)
        try:
            choice = input(c("  Choose an option: ", BOLD)).strip()
        except (EOFError, KeyboardInterrupt):
            choice = "0"
        if choice == "0":
            print(c("\n  Goodbye! Your data is saved.\n", GREEN))
            break
        action = ACTIONS.get(choice)
        if not action:
            print(c("  ✗ Invalid choice - enter a number from the menu.", RED))
            continue
        try:
            action()
        except KeyboardInterrupt:
            print(c("\n  (cancelled)", DIM))


if __name__ == "__main__":
    main()
