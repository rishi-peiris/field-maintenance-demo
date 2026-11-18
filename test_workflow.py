"""Integration coverage against a local MongoDB server and a temporary database."""
import json
import os
import sqlite3
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4

from pymongo import MongoClient

from app import MaintenanceServer
from storage import STATUSES, initialize, migrate_sqlite

USERS = {"Supervisor": "test-supervisor-token", "Technician": "test-technician-token"}


class WorkflowTest(unittest.TestCase):
    def setUp(self):
        client = MongoClient(os.getenv("MONGODB_TEST_URI", "mongodb://127.0.0.1:27017"),
                             serverSelectionTimeoutMS=3000)
        self.addCleanup(client.close)
        client.admin.command("ping")
        database = client[f"field_maintenance_test_{uuid4().hex}"]
        self.database = database
        self.addCleanup(client.drop_database, database.name)
        initialize(database)
        server = MaintenanceServer(("127.0.0.1", 0), database, USERS)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(thread.join)
        self.addCleanup(server.shutdown)
        self.url = f"http://127.0.0.1:{server.server_port}"

    def request(self, path, expected=200, method="GET", payload=None, user="Supervisor"):
        data = None if payload is None else json.dumps(payload).encode()
        headers = {"Content-Type": "application/json"}
        if user:
            headers["Authorization"] = f"Bearer {USERS[user]}"
        request = Request(self.url + path, data=data, method=method, headers=headers)
        try:
            response = urlopen(request, timeout=5)
        except HTTPError as error:
            response = error
        with response:
            codes = expected if isinstance(expected, tuple) else (expected,)
            self.assertIn(response.code, codes, (method, path))
            payload = json.load(response)
            return response.code if isinstance(expected, tuple) else payload

    def test_asset_register_and_audited_workflow(self):
        self.request("/api/assets", 401, user=None)
        for site in ("NORTH", "SOUTH"):
            self.request("/api/sites", 201, "POST", {"code": site, "name": site.title()})
        fields = {"site_id": "NORTH", "code": "PUMP-99", "name": "Pump", "serial_number": "sn-99",
                  "last_service_date": "2026-10-01", "next_service_date": "2026-11-01"}
        asset = self.request("/api/assets", 201, "POST", fields)
        self.request("/api/assets", 409, "POST", {**fields, "code": "OTHER"})
        self.request("/api/assets", 400, "POST", {**fields, "next_service_date": "2026-02-30"})
        self.request("/api/assets", 201, "POST", {**fields, "site_id": "SOUTH"})
        path = f'/api/assets/{asset["id"]}'
        updated = self.request(path, 200, "PATCH", {"next_service_date": "2026-12-01"})
        self.assertEqual(updated["next_service_date"], "2026-12-01")
        self.assertEqual(self.request("/api/assets?site_id=NORTH&serial_number=sn-99"), [updated])
        report = {"asset_id": asset["id"], "category": "Mechanical", "description": "Pump vibration observed."}
        self.request("/api/tickets", 400, "POST", {**report, "asset_id": "missing"})
        ticket = self.request("/api/tickets", 201, "POST", {**report, "actor": "Untrusted"})
        self.assertEqual(ticket["history"][0]["actor"], "Supervisor")
        self.assertEqual(self.request("/api/tickets?site_id=SOUTH"), [])
        self.assertEqual(self.request("/api/tickets?site_id=NORTH&status=New"), [ticket])
        status_path = f'/api/tickets/{ticket["id"]}/status'
        self.request(status_path, 409, "PATCH", {"status": "Assigned", "assigned_to": "Technician"})
        self.request(status_path, 200, "PATCH", {"status": "Triaged"})
        self.request(status_path, 400, "PATCH", {"status": "Assigned"})
        self.request(status_path, 200, "PATCH", {"status": "Assigned", "assigned_to": "Technician"})
        self.request(status_path, 200, "PATCH", {"status": "In Progress"}, user="Technician")
        self.request(status_path, 200, "PATCH", {"status": "Resolved"}, user="Technician")
        self.request(status_path, 200, "PATCH", {"status": "Closed"})
        self.request(status_path, 409, "PATCH", {"status": "In Progress"})
        repaired, = self.request(path + "/repairs")["tickets"]
        self.assertEqual([event["status"] for event in repaired["history"]], list(STATUSES))
        self.assertEqual([event["actor"] for event in repaired["history"]],
                         ["Supervisor"] * 3 + ["Technician"] * 2 + ["Supervisor"])
        self.assertTrue(all(event["changed_at"].endswith("+00:00") for event in repaired["history"]))

    def test_concurrent_transition_has_one_audit_event(self):
        self.database.tickets.insert_one({"_id": "race", "status": "New", "history": []})

        def transition():
            return self.request("/api/tickets/race/status", (200, 409), "PATCH", {"status": "Triaged"})

        with ThreadPoolExecutor(2) as pool:
            self.assertEqual(sorted(pool.map(lambda _: transition(), range(2))), [200, 409])
        ticket = self.database.tickets.find_one({"_id": "race"})
        self.assertEqual(len(ticket["history"]), 1)

    def test_sqlite_import_preserves_and_links_history(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "legacy.sqlite3"
            with closing(sqlite3.connect(source)) as sqlite, sqlite:
                sqlite.executescript("""
                    CREATE TABLE tickets (id, asset, category, description, status);
                    INSERT INTO tickets VALUES (42, 'PUMP-02', 'Hydraulic', 'Existing observation', 'Open');
                    CREATE TABLE assets (id, code, name, location);
                    INSERT INTO assets VALUES (1, 'PUMP-02', 'Transfer Pump', 'Workshop');
                    CREATE TABLE work_orders (id, ticket_id, status);
                    INSERT INTO work_orders VALUES (7, 42, 'Completed');
                    CREATE TABLE work_order_history (id, work_order_id, previous_status, status, changed_at);
                    INSERT INTO work_order_history VALUES (2, 7, 'In progress', 'Completed', '2026-11-18T03:00:00Z');
                """)
            original = source.read_bytes()
            migrate_sqlite(self.database, source)
            ticket, = self.request("/api/tickets")
            self.assertEqual((ticket["legacy_id"], ticket["legacy_status"], ticket["description"],
                              ticket["status"], ticket["created_at"]),
                             (42, "Open", "Existing observation", "Resolved", None))
            self.request("/api/tickets/legacy-42/status", 200, "PATCH", {"status": "Closed"})
            migrate_sqlite(self.database, source)
            asset_id = ticket["asset_id"]
            history = self.request(f"/api/assets/{asset_id}/repairs")
            self.assertEqual(history["asset"]["location"], "Workshop")
            self.assertEqual(history["tickets"][0]["status"], "Closed")
            order, = history["legacy_work_orders"]
            self.assertEqual(order["history"][0]["changed_at"], "2026-11-18T03:00:00Z")
            self.assertEqual(self.database.tickets.count_documents({}), 1)
            self.assertEqual(source.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
