"""Generate the synthetic order-to-delivery OCEL 2.0 demo log (sample_data/ocel_demo.json).

Object types: customer, order, item, package. Shared events are intentional:
"place order" relates an order, its customer and all its items; "create package"
and "send package" relate a package and every item packed in it (items of
different orders can share a package). Some orders change their status and
price over time (OCEL 2.0 changing attributes). Deterministic (fixed seed).

Usage: python scripts/generate_ocel_demo.py [output_path]
"""

from __future__ import annotations

import json
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SEED = 7
START = datetime(2025, 3, 3, 8, 0, tzinfo=timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def generate(n_orders: int = 60) -> dict:
    rng = random.Random(SEED)
    objects: dict[str, dict] = {}
    events: list[dict] = []
    products = ["Laptop", "Monitor", "Keyboard", "Dock", "Headset", "Chair"]
    customers = [f"c{i}" for i in range(1, 13)]
    for c in customers:
        objects[c] = {"id": c, "type": "customer", "attributes": [
            {"name": "segment", "time": iso(datetime(1970, 1, 1, tzinfo=timezone.utc)), "value": rng.choice(["retail", "business"])}],
            "relationships": []}

    def event(etype: str, ts: datetime, rels: list[tuple[str, str]], **attrs) -> None:
        events.append({"id": f"e{len(events) + 1}", "type": etype, "time": iso(ts),
                       "attributes": [{"name": k, "value": v} for k, v in attrs.items()],
                       "relationships": [{"objectId": o, "qualifier": q} for o, q in rels]})

    pending_items: list[tuple[datetime, str, str]] = []
    item_no = 0
    for n in range(1, n_orders + 1):
        oid = f"o{n}"
        customer = rng.choice(customers)
        placed = START + timedelta(hours=n * 5 + rng.randint(0, 4), minutes=rng.randint(0, 59))
        n_items = rng.choices([1, 2, 3, 4], weights=[3, 4, 2, 1])[0]
        items = []
        for _ in range(n_items):
            item_no += 1
            iid = f"i{item_no}"
            items.append(iid)
            objects[iid] = {"id": iid, "type": "item", "attributes": [
                {"name": "product", "time": iso(datetime(1970, 1, 1, tzinfo=timezone.utc)), "value": rng.choice(products)}],
                "relationships": [{"objectId": oid, "qualifier": "part of"}]}
        price = round(sum(rng.uniform(40, 900) for _ in items), 2)
        objects[oid] = {"id": oid, "type": "order", "attributes": [
            {"name": "status", "time": iso(placed), "value": "placed"},
            {"name": "price", "time": iso(placed), "value": price}],
            "relationships": [{"objectId": customer, "qualifier": "ordered by"}]}
        event("place order", placed, [(oid, "order"), (customer, "customer")] + [(i, "item") for i in items],
              channel=rng.choice(["web", "phone"]))
        t = placed + timedelta(hours=rng.uniform(1, 12))
        if rng.random() < 0.2:
            new_price = round(price * rng.uniform(0.8, 0.95), 2)
            event("change price", t, [(oid, "order")], cost=5.0)
            objects[oid]["attributes"].append({"name": "price", "time": iso(t), "value": new_price})
            t += timedelta(hours=rng.uniform(2, 20))
        event("confirm order", t, [(oid, "order")], cost=2.5)
        objects[oid]["attributes"].append({"name": "status", "time": iso(t), "value": "confirmed"})
        for iid in items:
            pt = t + timedelta(hours=rng.uniform(2, 30) * (3 if n_items >= 3 else 1))
            if rng.random() < 0.1:
                event("item out of stock", pt, [(iid, "item")])
                pt += timedelta(hours=rng.uniform(24, 72))
            event("pick item", pt, [(iid, "item")], cost=1.5)
            pending_items.append((pt, iid, oid))
        pay = t + timedelta(hours=rng.uniform(4, 96))
        event("pay order", pay, [(oid, "order"), (customer, "payer")], cost=0.8)
        objects[oid]["attributes"].append({"name": "status", "time": iso(pay), "value": "paid"})

    # Packages are created in waves from items picked so far (may mix orders).
    pending_items.sort()
    pkg_no = 0
    while pending_items:
        size = min(len(pending_items), rng.choice([1, 2, 3]))
        batch, pending_items = pending_items[:size], pending_items[size:]
        pkg_no += 1
        pid = f"p{pkg_no}"
        ready = max(b[0] for b in batch) + timedelta(hours=rng.uniform(1, 8))
        objects[pid] = {"id": pid, "type": "package", "attributes": [
            {"name": "weight_kg", "time": iso(ready), "value": round(rng.uniform(1, 15), 1)}],
            "relationships": [{"objectId": iid, "qualifier": "contains"} for _, iid, _ in batch]}
        rels = [(pid, "package")] + [(iid, "item") for _, iid, _ in batch]
        event("create package", ready, rels, cost=3.0)
        sent = ready + timedelta(hours=rng.uniform(2, 24))
        event("send package", sent, rels, cost=12.0)
        if rng.random() < 0.12:
            failed = sent + timedelta(hours=rng.uniform(20, 40))
            event("failed delivery", failed, [(pid, "package")])
            sent = failed + timedelta(hours=rng.uniform(20, 30))
        event("package delivered", sent + timedelta(hours=rng.uniform(18, 60)), rels)

    events.sort(key=lambda e: (e["time"], int(e["id"][1:])))
    attr = lambda *pairs: [{"name": n, "type": t} for n, t in pairs]
    return {
        "objectTypes": [
            {"name": "customer", "attributes": attr(("segment", "string"))},
            {"name": "order", "attributes": attr(("status", "string"), ("price", "float"))},
            {"name": "item", "attributes": attr(("product", "string"))},
            {"name": "package", "attributes": attr(("weight_kg", "float"))},
        ],
        "eventTypes": [
            {"name": "place order", "attributes": attr(("channel", "string"))},
            {"name": "change price", "attributes": attr(("cost", "float"))},
            {"name": "confirm order", "attributes": attr(("cost", "float"))},
            {"name": "item out of stock", "attributes": []},
            {"name": "pick item", "attributes": attr(("cost", "float"))},
            {"name": "pay order", "attributes": attr(("cost", "float"))},
            {"name": "create package", "attributes": attr(("cost", "float"))},
            {"name": "send package", "attributes": attr(("cost", "float"))},
            {"name": "failed delivery", "attributes": []},
            {"name": "package delivered", "attributes": []},
        ],
        "objects": list(objects.values()),
        "events": events,
    }


def main() -> None:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "sample_data" / "ocel_demo.json"
    data = generate()
    out.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {out}: {len(data['events'])} events, {len(data['objects'])} objects")


if __name__ == "__main__":
    main()
