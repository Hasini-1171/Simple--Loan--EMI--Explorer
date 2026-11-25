"""Creates 40 realistic sample loans (CSV + JSON) plus a deliberately messy CSV for import demos."""
import csv
import json
import random
from datetime import datetime, timedelta

from emi_core import SAMPLE_DIR, build_record

random.seed(17)

# (name, category, principal, rate, months, income) - boundary and edge cases first
EDGE = [
    ("Minimum loan", "Personal", 1_000, 10, 1, None),
    ("Maximum loan", "Business", 100_000_000, 9.5, 240, 5_000_000),
    ("Zero interest festival loan", "Personal", 60_000, 0, 12, 45_000),
    ("Max rate payday loan", "Personal", 25_000, 50, 6, 20_000),
    ("Longest tenure (40 yrs)", "Home", 3_000_000, 8.5, 480, 90_000),
    ("Shortest tenure (1 month)", "Gold", 50_000, 11, 1, None),
    ("Over-stretched EMI", "Car", 1_500_000, 12, 36, 40_000),
    ("High-rate business loan", "Business", 800_000, 24, 48, 150_000),
    ("Interest > principal home loan", "Home", 5_000_000, 12, 360, 200_000),
]
PEOPLE = ["Ravi", "Anita", "Suresh", "Priya", "Karthik", "Meena", "Arjun", "Divya", "Imran", "Lakshmi"]
KINDS = {
    "Home": ((1_500_000, 9_000_000), (8.2, 9.8), (120, 300)),
    "Car": ((400_000, 1_800_000), (8.5, 11.5), (36, 84)),
    "Personal": ((50_000, 600_000), (11, 18), (12, 60)),
    "Education": ((200_000, 2_500_000), (8.5, 12), (36, 120)),
    "Business": ((300_000, 3_000_000), (11, 17), (24, 84)),
    "Gold": ((30_000, 400_000), (8.8, 12), (6, 36)),
}


def main():
    SAMPLE_DIR.mkdir(exist_ok=True)
    now = datetime.now()
    rows = list(EDGE)
    for i in range(31):
        cat = random.choice(list(KINDS))
        (plo, phi), (rlo, rhi), (tlo, thi) = KINDS[cat]
        rows.append((f"{cat} loan - {random.choice(PEOPLE)}", cat,
                     round(random.randint(plo, phi), -3), round(random.uniform(rlo, rhi), 2),
                     random.randint(tlo, thi) // 6 * 6 or 6,
                     random.choice([None, 40_000, 60_000, 85_000, 120_000, 200_000])))
    records = []
    for i, (n, c, p, r, t, inc) in enumerate(rows):
        when = (now - timedelta(days=random.randint(0, 330))).isoformat(timespec="seconds")
        records.append(build_record(n, c, p, r, t, inc, created_at=when))
    records.sort(key=lambda r: r.created_at)

    (SAMPLE_DIR / "sample_loans.json").write_text(
        json.dumps([r.to_dict() for r in records], indent=2), encoding="utf-8")
    with (SAMPLE_DIR / "sample_loans.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["name", "category", "principal", "annual_rate", "tenure_months", "monthly_income"])
        for r in records:
            w.writerow([r.name, r.category, r.principal, r.annual_rate, r.tenure_months, r.monthly_income or ""])

    # Messy legacy file: shows row-by-row error logging during bulk import
    messy = [
        ["name", "category", "principal", "annual_rate", "tenure_years", "monthly_income"],
        ["Legacy home loan", "Home", "2,500,000", "8.4", "20", "75000"],
        ["Legacy car loan", "Car", "700000", "9.1", "5", ""],
        ["", "Home", "500000", "9", "10", ""],                 # blank name
        ["Bad amount", "Personal", "abc", "12", "3", ""],      # non-numeric
        ["Negative rate", "Personal", "100000", "-4", "2", ""],
        ["Unknown type", "Spaceship", "100000", "10", "2", ""],
        ["Too long", "Home", "1000000", "9", "60", ""],         # 720 months > limit
        ["Fractional months", "Gold", "90000", "10", "1.03", ""],
    ]
    with (SAMPLE_DIR / "sample_messy_import.csv").open("w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows(messy)
    print(f"Wrote {len(records)} sample records + messy import file to {SAMPLE_DIR}")


if __name__ == "__main__":
    main()
