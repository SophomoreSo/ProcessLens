"""Generate the synthetic task-event demo CSV (sample_data/task_events_demo.csv).

Accounts-payable clerks (pseudonymous ids) process invoices of the purchase-to-pay
demo dataset: they open the invoice e-mail, view the PDF, look up the purchase
order in the ERP, sometimes check figures in a spreadsheet, and post the invoice.
Some sessions are unrelated to any case (inbox triage). Durations are supplied
for about two thirds of the events, as a recording tool might export them.
Deterministic (fixed seed). No content, keystrokes or screenshots are included.

Usage: python scripts/generate_task_demo.py [output_path]
"""

from __future__ import annotations

import csv
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SEED = 11
START = datetime(2025, 2, 3, 7, 30, tzinfo=timezone.utc)
USERS = [f"u-{h}" for h in ("3f9a", "81c2", "c07e", "5d14", "a6b8", "e2f1")]


def demo_case_ids() -> list[str]:
    path = ROOT / "sample_data" / "p2p_demo_events.csv"
    with path.open(encoding="utf-8") as fh:
        ids = sorted({row["case_id"] for row in csv.DictReader(fh) if row["activity"] == "Receive Invoice"})
    return ids


def generate() -> list[dict]:
    rng = random.Random(SEED)
    cases = demo_case_ids()
    rng.shuffle(cases)
    rows: list[dict] = []
    session_no = 0

    def emit(session, user, t, app, action, case=None, dur=None) -> datetime:
        d = dur if dur is not None else rng.uniform(4, 90)
        rows.append({"session_id": session, "timestamp": t.strftime("%Y-%m-%dT%H:%M:%SZ"), "application": app,
                     "action": action, "pseudonymous_user_id": user, "business_case_id": case or "",
                     "duration": f"{d:.1f}" if rng.random() < 0.67 else ""})
        return t + timedelta(seconds=d + rng.uniform(1, 25))

    for day in range(20):
        base = START + timedelta(days=day + 2 * (day // 5))  # weekdays only
        for user in USERS:
            t = base + timedelta(minutes=rng.randint(0, 50))
            for _ in range(rng.randint(3, 7)):
                session_no += 1
                sid = f"s{session_no:05d}"
                if rng.random() < 0.18 or not cases:  # inbox triage, not linked to a case
                    for _ in range(rng.randint(2, 5)):
                        t = emit(sid, user, t, "Email", rng.choice(["Open message", "Archive message", "Reply"]))
                else:
                    case = cases.pop()
                    t = emit(sid, user, t, "Email", "Open message", case)
                    t = emit(sid, user, t, "PDF Viewer", "Open attachment", case)
                    t = emit(sid, user, t, "ERP", "Search purchase order", case)
                    if rng.random() < 0.35:
                        t = emit(sid, user, t, "Spreadsheet", "Open workbook", case)
                        t = emit(sid, user, t, "Spreadsheet", "Check amounts", case)
                        t = emit(sid, user, t, "ERP", "Search purchase order", case)
                    t = emit(sid, user, t, "ERP", "Enter invoice", case)
                    if rng.random() < 0.3:  # copy-paste loop between PDF and ERP
                        for _ in range(rng.randint(1, 3)):
                            t = emit(sid, user, t, "PDF Viewer", "Copy value", case, rng.uniform(3, 10))
                            t = emit(sid, user, t, "ERP", "Paste value", case, rng.uniform(2, 6))
                    if rng.random() < 0.15:
                        t = emit(sid, user, t, "Email", "Reply", case)
                    t = emit(sid, user, t, "ERP", "Post invoice", case)
                t += timedelta(minutes=rng.uniform(2, 50))
    return rows


def main() -> None:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "sample_data" / "task_events_demo.csv"
    rows = generate()
    with out.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {out}: {len(rows)} events")


if __name__ == "__main__":
    main()
