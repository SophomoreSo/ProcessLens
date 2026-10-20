"""Generate the deterministic, SYNTHETIC purchase-to-pay demo data.

    python scripts/generate_demo_data.py

Writes:
  sample_data/p2p_demo_events.csv      ~600 cases, valid (import with source timezone Europe/Berlin)
  sample_data/p2p_validation_demo.csv  small file with intentionally malformed rows

All names, vendors, amounts and timestamps are invented. Output is byte-for-byte
reproducible (fixed seed, no dependence on the current date).
"""

from __future__ import annotations

import csv
import random
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "sample_data"
SEED = 20250101
N_CASES = 600
FIRST_DAY = datetime(2025, 1, 6)
LAST_START_DAY = datetime(2025, 6, 20)
EXTRACTION_TIME = datetime(2025, 6, 27, 18, 0)  # events after this are "not yet happened" -> open cases

COLUMNS = ["event_id", "case_id", "activity", "timestamp", "start_timestamp", "resource", "department",
           "cost", "case_status", "vendor", "po_amount", "category"]

REQUESTER_DEPTS = {
    "Operations": ["ops.miller", "ops.santos", "ops.ito"],
    "IT": ["it.okafor", "it.lindqvist"],
    "Marketing": ["mkt.dubois", "mkt.kowalski"],
    "Facilities": ["fac.nguyen", "fac.haddad"],
}
MANAGERS = {"Operations": "mgr.ops", "IT": "mgr.it", "Marketing": "mgr.mkt", "Facilities": "mgr.fac"}
BUYERS = ["buyer.ahmed", "buyer.becker", "buyer.costa", "buyer.dahl"]
LEADS = ["lead.procurement1", "lead.procurement2"]
WAREHOUSE = ["wh.ruiz", "wh.patel", "wh.schmidt"]
AP_CLERKS = ["ap.jensen", "ap.moreau", "ap.tanaka"]
TREASURY = ["treasury.batch"]
VENDORS = ["Northwind Supplies", "Contoso Components", "Fabrikam Office", "Tailspin Logistics",
           "Adatum Hardware", "Litware Services"]
CATEGORIES = ["Office", "IT Hardware", "Services", "MRO", "Marketing Material"]


def business_time(rng: random.Random, t: datetime, hours: float) -> datetime:
    """Advance by roughly ``hours`` and snap into business hours (08:00-18:00, Mon-Fri)."""
    t = t + timedelta(hours=hours)
    while t.weekday() >= 5:
        t = (t + timedelta(days=1)).replace(hour=8, minute=0)
    if t.hour < 8:
        t = t.replace(hour=8, minute=rng.randint(0, 59))
    elif t.hour >= 18:
        t = (t + timedelta(days=1)).replace(hour=8, minute=rng.randint(0, 59))
        while t.weekday() >= 5:
            t += timedelta(days=1)
    return t.replace(second=rng.randint(0, 59), microsecond=0)


def make_case(rng: random.Random, idx: int, start: datetime) -> list[dict]:
    dept = rng.choice(list(REQUESTER_DEPTS))
    requester = rng.choice(REQUESTER_DEPTS[dept])
    buyer = rng.choice(BUYERS)
    vendor = rng.choice(VENDORS)
    amount = round(rng.lognormvariate(8.2, 1.0), 2)
    category = rng.choice(CATEGORIES)
    case_id = f"{4500000 + idx:010d}"  # leading zeros on purpose, e.g. 0004500001
    events: list[tuple[str, datetime, float | None, str, str, float]] = []

    def add(activity: str, t: datetime, work_min: float | None, resource: str, department: str, cost: float):
        events.append((activity, t, work_min, resource, department, cost))

    t = start
    add("Create Purchase Requisition", t, rng.uniform(5, 25), requester, dept, 12.0)

    skip_pr_approval = rng.random() < 0.06
    if not skip_pr_approval:
        t = business_time(rng, t, rng.uniform(2, 30))
        add("Approve Purchase Requisition", t, rng.uniform(2, 10), MANAGERS[dept], dept, 8.0)

    t = business_time(rng, t, rng.uniform(4, 40))
    add("Create Purchase Order", t, rng.uniform(10, 40), buyer, "Procurement", 20.0)

    if rng.random() < 0.05:
        t = business_time(rng, t, rng.uniform(4, 72))
        add("Cancel Purchase Order", t, rng.uniform(3, 10), buyer, "Procurement", 6.0)
        return finalize(case_id, events, "Cancelled", vendor, amount, category)

    missing_po_approval = rng.random() < 0.08
    lead = rng.choice(LEADS)
    if not missing_po_approval:
        # approval delays: high-value orders and one slow approver
        delay = rng.uniform(2, 24)
        if amount > 8000:
            delay += rng.uniform(48, 240)
        if lead == "lead.procurement2":
            delay += rng.uniform(12, 72)
        t = business_time(rng, t, delay)
        add("Approve Purchase Order", t, rng.uniform(3, 15), lead, "Procurement", 10.0)

    # automated send: sometimes recorded with exactly the same timestamp (tie)
    if rng.random() < 0.07:
        add("Send Purchase Order", t, None, "system", "Procurement", 0.5)
    else:
        t = business_time(rng, t, rng.uniform(0.5, 8))
        add("Send Purchase Order", t, None, "system", "Procurement", 0.5)

    # rework loop: price change requires re-approval
    loops = 0
    while rng.random() < (0.14 if loops == 0 else 0.25) and loops < 3:
        loops += 1
        t = business_time(rng, t, rng.uniform(8, 96))
        add("Change Price", t, rng.uniform(5, 20), buyer, "Procurement", 15.0)
        t = business_time(rng, t, rng.uniform(4, 60))
        add("Approve Purchase Order", t, rng.uniform(3, 15), lead, "Procurement", 10.0)
        t = business_time(rng, t, rng.uniform(0.5, 6))
        add("Send Purchase Order", t, None, "system", "Procurement", 0.5)

    t = business_time(rng, t, rng.uniform(48, 300))
    add("Receive Goods", t, rng.uniform(10, 45), rng.choice(WAREHOUSE), "Warehouse", 18.0)

    t = business_time(rng, t, rng.uniform(4, 120))
    clerk = rng.choice(AP_CLERKS)
    add("Receive Invoice", t, rng.uniform(5, 15), clerk, "Accounts Payable", 7.0)
    if rng.random() < 0.11:
        t = business_time(rng, t, rng.uniform(4, 48))
        add("Request Invoice Correction", t, rng.uniform(10, 30), clerk, "Accounts Payable", 14.0)
        t = business_time(rng, t, rng.uniform(24, 200))
        add("Receive Invoice", t, rng.uniform(5, 15), clerk, "Accounts Payable", 7.0)

    t = business_time(rng, t, rng.uniform(4, 96) + (rng.uniform(48, 160) if clerk == "ap.tanaka" else 0))
    add("Clear Invoice", t, rng.uniform(5, 20), clerk, "Accounts Payable", 9.0)
    t = business_time(rng, t, rng.uniform(24, 240))
    add("Pay Invoice", t, None, rng.choice(TREASURY), "Treasury", 2.0)
    return finalize(case_id, events, "Completed", vendor, amount, category)


def finalize(case_id, events, final_status, vendor, amount, category) -> list[dict]:
    visible = [e for e in events if e[1] <= EXTRACTION_TIME]
    status = final_status if len(visible) == len(events) else "Open"
    rows = []
    for activity, t, work_min, resource, dept, cost in visible:
        start = (t - timedelta(minutes=work_min)).replace(microsecond=0) if work_min else None
        rows.append({
            "case_id": case_id, "activity": activity, "timestamp": t.strftime("%Y-%m-%d %H:%M:%S"),
            "start_timestamp": start.strftime("%Y-%m-%d %H:%M:%S") if start else "",
            "resource": resource, "department": dept, "cost": f"{cost:.2f}", "case_status": status,
            "vendor": vendor, "po_amount": f"{amount:.2f}", "category": category,
        })
    return rows


def generate_events() -> list[dict]:
    rng = random.Random(SEED)
    span_days = (LAST_START_DAY - FIRST_DAY).days
    starts = []
    for _ in range(N_CASES):
        d = FIRST_DAY + timedelta(days=rng.randint(0, span_days))
        starts.append(business_time(rng, d.replace(hour=8), rng.uniform(0, 9)))
    starts.sort()
    rows: list[dict] = []
    for i, start in enumerate(starts, start=1):
        rows.extend(make_case(rng, i, start))
    rows.sort(key=lambda r: (r["timestamp"], r["case_id"]))  # source files are rarely case-ordered
    for n, r in enumerate(rows, start=1):
        r["event_id"] = f"EV{n:07d}"
    return rows


VALIDATION_ROWS = [
    # event_id, case_id, activity, timestamp, start_timestamp, resource, department, cost, case_status
    ["V001", "000123", "Create Purchase Requisition", "2024-10-21 09:00:00", "", "ops.miller", "Operations", "12.00", "Open"],
    ["V002", "000123", "Approve Purchase Requisition", "2024-10-21 11:30:00", "2024-10-21 11:20:00", "mgr.ops", "Operations", "8.00", "Open"],
    ["V003", "", "Create Purchase Order", "2024-10-22 10:00:00", "", "buyer.ahmed", "Procurement", "20.00", "Open"],
    ["V004", "000124", "", "2024-10-22 10:05:00", "", "buyer.becker", "Procurement", "20.00", "Open"],
    ["V005", "000124", "Create Purchase Order", "22/10/2024 10:05", "", "buyer.becker", "Procurement", "20.00", "Open"],
    ["V006", "000124", "Approve Purchase Order", "not a date", "", "lead.procurement1", "Procurement", "10.00", "Open"],
    ["V007", "000125", "Create Purchase Requisition", "2024-10-23 08:15:00", "", "it.okafor", "IT", "12,50 EUR", "Open"],
    ["V007", "000125", "Approve Purchase Requisition", "2024-10-23 09:15:00", "", "mgr.it", "IT", "8.00", "Open"],
    ["V009", "000125", "Create Purchase Order", "2024-10-23 12:00:00", "2024-10-23 13:00:00", "buyer.costa", "Procurement", "20.00", "Open"],
    ["V010", "000126", "Create Purchase Requisition", "2024-10-27 02:30:00", "", "fac.nguyen", "Facilities", "12.00", "Open"],
    ["V011", "000126", "Approve Purchase Requisition", "2024-03-31 02:30:00", "", "mgr.fac", "Facilities", "8.00", "Open"],
    ["V012", "000127", "=HYPERLINK(\"http://example.invalid\",\"click\")", "2024-10-24 10:00:00", "", "mkt.dubois", "Marketing", "1.00", "Open"],
    ["V013", "000127", "Create Purchase Order", "2024-10-24 10:00:00", "", "buyer.dahl", "Procurement", "abc", "Open"],
    ["V014", "000128", "Create Purchase Requisition", "2024-10-25T08:00:00Z", "", "ops.ito", "Operations", "12.00", "Completed"],
    ["V015", "000128", "Approve Purchase Requisition", "2024-10-25T10:00:00+02:00", "", "mgr.ops", "Operations", "-8.00", "Completed"],
    ["V016", "000128", "Approve Purchase Requisition", "2024-10-25 12:00:00", "", "mgr.ops", "Operations", "", "Completed"],
]


def write_validation_demo(path: Path) -> None:
    cols = COLUMNS[:9]
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(cols)
        for row in VALIDATION_ROWS:
            w.writerow(row)
        # a row with the wrong number of fields
        fh.write("V017,000129,Create Purchase Requisition,2024-10-26 09:00:00\n")


def main() -> None:
    OUT_DIR.mkdir(exist_ok=True)
    rows = generate_events()
    with (OUT_DIR / "p2p_demo_events.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    write_validation_demo(OUT_DIR / "p2p_validation_demo.csv")
    cases = {r["case_id"] for r in rows}
    print(f"Wrote {len(rows)} events / {len(cases)} cases to sample_data/p2p_demo_events.csv")
    print("Wrote sample_data/p2p_validation_demo.csv")


if __name__ == "__main__":
    main()
