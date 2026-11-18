"""MongoDB model and one-way import from the previous SQLite app."""
import sqlite3
from contextlib import closing

from bson import ObjectId

CATEGORIES = ("Mechanical", "Electrical", "Hydraulic", "Other")
STATUSES = ("New", "Triaged", "Assigned", "In Progress", "Resolved", "Closed")
DEMO_ASSETS = {"DRILL-01": "Drill", "PUMP-02": "Pump", "GEN-03": "Generator"}
LEGACY_STATUS = {"Open": "New", "In progress": "In Progress", "Completed": "Resolved",
                 "Cancelled": "New"}


def initialize(database):
    database.assets.create_index([("site_id", 1), ("code", 1)], unique=True)
    database.assets.create_index([("site_id", 1), ("serial_number", 1)], unique=True,
                                 partialFilterExpression={"serial_number": {"$type": "string"}})
    database.tickets.create_index([("asset_id", 1), ("created_at", -1)])
    database.tickets.create_index([("status", 1), ("created_at", -1)])
    database.work_orders.create_index("ticket_id")


def insert_once(collection, document):
    # Deterministic IDs and insert-only writes let an interrupted import resume.
    collection.update_one({"_id": document["_id"]}, {"$setOnInsert": document}, upsert=True)


def migrate_sqlite(database, source):
    if not source.is_file():
        return
    with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as sqlite:
        sqlite.row_factory = sqlite3.Row
        sqlite.execute("BEGIN")
        tables = {row[0] for row in sqlite.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        tickets = list(sqlite.execute("SELECT * FROM tickets"))
        assets = list(sqlite.execute("SELECT * FROM assets")) if "assets" in tables else []
        orders = list(sqlite.execute("SELECT * FROM work_orders")) if "work_orders" in tables else []
        history = list(sqlite.execute("SELECT * FROM work_order_history ORDER BY id")) if "work_order_history" in tables else []

    insert_once(database.sites, {"_id": "LEGACY", "name": "Imported equipment"})
    by_code = {code: {"name": name, "location": ""} for code, name in DEMO_ASSETS.items()}
    for ticket in tickets:
        by_code.setdefault(ticket["asset"].upper(), {"name": ticket["asset"], "location": ""})
    for asset in assets:
        by_code[asset["code"].upper()] = {"name": asset["name"], "location": asset["location"]}
    for code, details in by_code.items():
        insert_once(database.assets, {"_id": f"legacy-{code}", "site_id": "LEGACY", "code": code,
                                      "serial_number": None, "last_service_date": None,
                                      "next_service_date": None, **details})

    by_order = {}
    for event in history:
        by_order.setdefault(event["work_order_id"], []).append(dict(event))
    by_ticket = {order["ticket_id"]: order for order in orders}
    for ticket in tickets:
        order = by_ticket.get(ticket["id"])
        status = "Resolved" if ticket["status"] == "Resolved" else LEGACY_STATUS.get(
            order["status"] if order else "Open", "New")
        insert_once(database.tickets, {
            "_id": f'legacy-{ticket["id"]}', "legacy_id": ticket["id"],
            "legacy_status": ticket["status"], "legacy_asset_code": ticket["asset"],
            "asset_id": f'legacy-{ticket["asset"].upper()}', "category": ticket["category"],
            "description": ticket["description"], "status": status, "created_at": None,
            "assigned_to": None, "history": [
                {"previous_status": None, "status": status, "actor": None,
                 "changed_at": None, "source": "sqlite_import"}
            ],
        })
    for order in orders:
        insert_once(database.work_orders, {
            "_id": f'legacy-{order["id"]}', "legacy_id": order["id"],
            "ticket_id": f'legacy-{order["ticket_id"]}', "status": order["status"],
            "history": by_order.get(order["id"], []),
        })


def public(document):
    return {"id": str(document["_id"]), **{key: value for key, value in document.items() if key != "_id"}}


def new_id():
    return str(ObjectId())
