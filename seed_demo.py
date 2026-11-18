"""Add clearly labeled sample records without overwriting existing work."""
import os
from datetime import datetime, timedelta, timezone

from pymongo import MongoClient

from storage import STATUSES, initialize, insert_once

SITES = {"DEMO-PERTH": "Demo · Perth workshop", "DEMO-PILBARA": "Demo · Pilbara depot"}
ASSETS = (
    ("DEMO-PERTH", "PUMP-17", "Transfer pump", "Workshop bay 2"),
    ("DEMO-PERTH", "LIFT-04", "Workshop lift", "Workshop bay 4"),
    ("DEMO-PILBARA", "GEN-12", "Standby generator", "Power shed"),
    ("DEMO-PILBARA", "COMP-08", "Air compressor", "Service yard"),
)
TICKETS = (
    (0, "Hydraulic", "Oil is pooling below the pump inlet after start-up.", 0),
    (1, "Mechanical", "The lift hesitates midway through raising an unloaded platform.", 2),
    (2, "Electrical", "The generator failed its weekly start test after a battery warning.", 3),
    (3, "Mechanical", "Replaced the worn drive belt and verified pressure under load.", 5),
)


def seed(database):
    now = datetime.now(timezone.utc)
    for site_id, name in SITES.items():
        insert_once(database.sites, {"_id": site_id, "name": name})
    for index, (site_id, code, name, location) in enumerate(ASSETS):
        insert_once(database.assets, {
            "_id": f"demo-{code}", "site_id": site_id, "code": code, "name": name,
            "location": location, "serial_number": f"DEMO-SN-{index + 1:03}",
            "last_service_date": (now - timedelta(days=30 + index * 10)).date().isoformat(),
            "next_service_date": (now + timedelta(days=14 + index * 10)).date().isoformat(),
        })
    for index, (asset, category, description, stage) in enumerate(TICKETS):
        start = now - timedelta(days=stage + 1)
        events = []
        for step, status in enumerate(STATUSES[:stage + 1]):
            event = {
                "previous_status": STATUSES[step - 1] if step else None,
                "status": status, "actor": "Demo Supervisor" if step not in (3, 4) else "Demo Technician",
                "changed_at": (start + timedelta(days=step)).isoformat(timespec="milliseconds"),
                "source": "demo_seed",
            }
            if status == "Assigned":
                event["assigned_to"] = "Demo Technician"
            events.append(event)
        insert_once(database.tickets, {
            "_id": f"demo-ticket-{index + 1}", "asset_id": f"demo-{ASSETS[asset][1]}",
            "category": category, "description": description, "status": STATUSES[stage],
            "created_at": events[0]["changed_at"],
            "assigned_to": "Demo Technician" if stage >= 2 else None, "history": events,
        })


if __name__ == "__main__":
    with MongoClient(os.getenv("MONGODB_URI", "mongodb://127.0.0.1:27017"),
                     serverSelectionTimeoutMS=5000) as client:
        database = client[os.getenv("MONGODB_DATABASE", "field_maintenance")]
        initialize(database)
        seed(database)
        print("Demo sites, assets and tickets are available.")
