"""Local field maintenance desk and JSON API."""
import hmac
import json
import os
import re
import secrets
from datetime import date, datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from pymongo import MongoClient
from pymongo.errors import DuplicateKeyError, PyMongoError

from storage import CATEGORIES, STATUSES, initialize, migrate_sqlite, new_id, public

ROOT = Path(__file__).resolve().parent
CODE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,31}")


class RequestError(Exception):
    def __init__(self, status, message):
        self.status = status
        super().__init__(message)


def required_text(data, field, maximum, minimum=1):
    value = data.get(field)
    if not isinstance(value, str) or not minimum <= len(value.strip()) <= maximum:
        raise RequestError(400, f"Enter a valid {field.replace('_', ' ')}.")
    return value.strip()


def code_from(data):
    code = required_text(data, "code", 32)
    if not CODE.fullmatch(code):
        raise RequestError(400, "Use letters, numbers, hyphens or underscores for the code.")
    return code.upper()


def service_date(data, field):
    value = data.get(field)
    if value is None or value == "":
        return None
    if isinstance(value, str) and re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        try:
            return date.fromisoformat(value).isoformat()
        except ValueError:
            pass
    raise RequestError(400, f"Enter a valid {field.replace('_', ' ')}.")


def timestamp():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def ticket_view(ticket):
    result = public(ticket)
    index = STATUSES.index(ticket["status"]) + 1
    result["next_status"] = STATUSES[index] if index < len(STATUSES) else None
    return result


class MaintenanceServer(ThreadingHTTPServer):
    def __init__(self, address, database, users, *, demo_access=False):
        self.database = database
        self.users = dict(users)
        self.demo_token = None
        if demo_access:
            if address[0] != "127.0.0.1":
                raise ValueError("Demo access requires a loopback server.")
            if "Demo Supervisor" in self.users:
                raise ValueError("Demo Supervisor is reserved when demo access is enabled.")
            self.demo_token = secrets.token_urlsafe(32)
            self.users["Demo Supervisor"] = self.demo_token
        super().__init__(address, Handler)


class Handler(BaseHTTPRequestHandler):
    @property
    def database(self):
        return self.server.database

    def reply(self, status, payload):
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(json.dumps(payload).encode())

    def actor(self):
        scheme, _, token = self.headers.get("Authorization", "").partition(" ")
        if scheme.lower() == "bearer" and token:
            for name, secret in self.server.users.items():
                if hmac.compare_digest(token.encode(), secret.encode()):
                    return name
        raise RequestError(401, "Enter a valid local access token.")

    def read_json(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 8192:
                raise ValueError
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise ValueError
            return data
        except (ValueError, UnicodeError):
            raise RequestError(400, "Send a valid JSON object up to 8 KB.")

    def do_GET(self):
        self.dispatch("GET")

    def do_POST(self):
        self.dispatch("POST")

    def do_PATCH(self):
        self.dispatch("PATCH")

    def dispatch(self, method):
        try:
            url = urlsplit(self.path)
            if method == "GET" and url.path == "/":
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                page = (ROOT / "index.html").read_bytes()
                if self.server.demo_token:
                    page = page.replace(b'data-demo="false"', b'data-demo="true"', 1)
                self.wfile.write(page)
                return
            if method == "POST" and url.path == "/api/demo-sign-in":
                self.demo_sign_in()
                return
            actor = self.actor()
            if method == "GET":
                self.reply(200, self.read_route(url.path, parse_qs(url.query), actor))
            else:
                self.reply(201 if method == "POST" else 200,
                           self.write_route(method, url.path, self.read_json(), actor))
        except RequestError as error:
            self.reply(error.status, {"error": str(error)})
        except DuplicateKeyError:
            self.reply(409, {"error": "That site code, asset code or serial number already exists for this register."})
        except PyMongoError:
            self.reply(503, {"error": "MongoDB is unavailable. Check the local server and try again."})
        except ValueError:
            self.reply(400, {"error": "Invalid request URL."})

    def demo_sign_in(self):
        if not self.server.demo_token:
            raise RequestError(404, "Demo sign-in is disabled.")
        port = self.server.server_port
        hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        host = self.headers.get("Host")
        if host not in hosts or self.headers.get("Origin") != f"http://{host}":
            raise RequestError(403, "Open the app directly on localhost to use demo sign-in.")
        self.reply(200, {"token": self.server.demo_token})

    def read_route(self, path, query, actor):
        db = self.database
        if path == "/api/session":
            return {"actor": actor, "users": sorted(self.server.users), "statuses": STATUSES}
        if path == "/api/sites":
            return [public(site) for site in db.sites.find().sort("name", 1)]
        if path == "/api/assets":
            filters = {key: query[key][0] for key in ("site_id", "serial_number") if key in query}
            if "serial_number" in filters:
                filters["serial_number"] = filters["serial_number"].strip().upper()
            return [public(asset) for asset in db.assets.find(filters).sort("code", 1)]
        if path == "/api/tickets":
            filters = {key: query[key][0] for key in ("asset_id", "status") if key in query}
            if "site_id" in query:
                asset_ids = db.assets.distinct("_id", {"site_id": query["site_id"][0]})
                if "asset_id" in filters:
                    if filters["asset_id"] not in asset_ids:
                        return []
                else:
                    filters["asset_id"] = {"$in": asset_ids}
            return [ticket_view(ticket) for ticket in db.tickets.find(filters).sort("created_at", -1)]
        match = re.fullmatch(r"/api/assets/([^/]+)/repairs", path)
        if match:
            asset_id = unquote(match[1])
            asset = db.assets.find_one({"_id": asset_id})
            if asset is None:
                raise RequestError(404, "Asset not found.")
            tickets = list(db.tickets.find({"asset_id": asset_id}).sort("created_at", -1))
            orders = list(db.work_orders.find({"ticket_id": {"$in": [ticket["_id"] for ticket in tickets]}}))
            repaired_ids = {order["ticket_id"] for order in orders if order["status"] == "Completed"}
            return {"asset": public(asset), "tickets": [ticket_view(ticket) for ticket in tickets
                    if ticket["status"] in ("Resolved", "Closed") or ticket["_id"] in repaired_ids],
                    "legacy_work_orders": [public(order) for order in orders]}
        raise RequestError(404, "Endpoint not found.")

    def write_route(self, method, path, data, actor):
        db = self.database
        if method == "POST" and path == "/api/sites":
            document = {"_id": code_from(data), "name": required_text(data, "name", 100)}
            db.sites.insert_one(document)
            return public(document)
        if method == "POST" and path == "/api/assets":
            document = {"_id": new_id(), **self.asset_fields(data)}
            db.assets.insert_one(document)
            return public(document)
        match = re.fullmatch(r"/api/assets/([^/]+)", path)
        if method == "PATCH" and match:
            current = db.assets.find_one({"_id": unquote(match[1])})
            if current is None:
                raise RequestError(404, "Asset not found.")
            allowed = {"site_id", "code", "name", "location", "serial_number",
                       "last_service_date", "next_service_date"}
            if not data or data.keys() - allowed:
                raise RequestError(400, "Supply asset fields to update.")
            changes = self.asset_fields({**current, **data})
            # Detect concurrent edits rather than overwriting a newer register entry.
            result = db.assets.update_one(current, {"$set": changes})
            if not result.matched_count:
                raise RequestError(409, "The asset changed. Refresh and try again.")
            return public({**current, **changes})
        if method == "POST" and path == "/api/tickets":
            asset_id = required_text(data, "asset_id", 200)
            asset = db.assets.find_one({"_id": asset_id})
            if asset is None:
                raise RequestError(400, "Choose an asset in the register.")
            category = data.get("category")
            if category not in CATEGORIES:
                raise RequestError(400, "Choose a valid category.")
            now = timestamp()
            document = {"_id": new_id(), "asset_id": asset_id, "category": category,
                        "description": required_text(data, "description", 500, 10),
                        "status": "New", "assigned_to": None, "created_at": now,
                        "history": [{"previous_status": None, "status": "New",
                                     "actor": actor, "changed_at": now}]}
            db.tickets.insert_one(document)
            return ticket_view(document)
        match = re.fullmatch(r"/api/tickets/([^/]+)/status", path)
        if method == "PATCH" and match:
            return self.transition(unquote(match[1]), data, actor)
        raise RequestError(404, "Endpoint not found.")

    def asset_fields(self, data):
        site_id = required_text(data, "site_id", 32)
        if self.database.sites.find_one({"_id": site_id}) is None:
            raise RequestError(400, "Choose a site in the register.")
        last = service_date(data, "last_service_date")
        upcoming = service_date(data, "next_service_date")
        if last and upcoming and upcoming < last:
            raise RequestError(400, "Next service must be on or after the last service.")
        serial = data.get("serial_number")
        if serial is not None and (not isinstance(serial, str) or len(serial.strip()) > 100):
            raise RequestError(400, "Enter a serial number up to 100 characters.")
        location = data.get("location", "")
        if not isinstance(location, str) or len(location.strip()) > 100:
            raise RequestError(400, "Enter a location up to 100 characters.")
        return {"site_id": site_id, "code": code_from(data),
                "name": required_text(data, "name", 100), "location": location.strip(),
                "serial_number": (serial or "").strip().upper() or None,
                "last_service_date": last, "next_service_date": upcoming}

    def transition(self, identifier, data, actor):
        collection = self.database.tickets
        requested = data.get("status")
        if requested not in STATUSES:
            raise RequestError(400, "Choose a valid ticket status.")
        ticket = collection.find_one({"_id": identifier})
        if ticket is None:
            raise RequestError(404, "Ticket not found.")
        if requested != ticket_view(ticket)["next_status"]:
            raise RequestError(409, "The ticket can only move to its next status. Refresh and try again.")

        changes = {"status": requested}
        event = {"previous_status": ticket["status"], "status": requested,
                 "actor": actor, "changed_at": timestamp()}
        if requested == "Assigned":
            assignee = required_text(data, "assigned_to", 100)
            if assignee not in self.server.users:
                raise RequestError(400, "Choose a configured assignee.")
            changes["assigned_to"] = assignee
            event["assigned_to"] = assignee
        result = collection.update_one(
            {"_id": identifier, "status": ticket["status"]},
            {"$set": changes, "$push": {"history": event}},
        )
        if not result.matched_count:
            raise RequestError(409, "Another user updated the ticket. Refresh and try again.")
        return ticket_view({**ticket, **changes, "history": [*ticket["history"], event]})


def configured_users():
    users = json.loads(os.environ.get("MAINTENANCE_USERS", "{}"))
    if (not isinstance(users, dict) or not users or
            any(not name.strip() or len(name) > 100 or not isinstance(token, str)
                or not re.fullmatch(r"[A-Za-z0-9._~-]{16,}", token)
                for name, token in users.items()) or len(set(users.values())) != len(users)):
        raise ValueError("Set MAINTENANCE_USERS to a JSON object mapping names to distinct access tokens of at least 16 characters.")
    return users


def main():
    try:
        demo_access = os.getenv("MAINTENANCE_DEMO_ACCESS") == "1"
        users = configured_users() if not demo_access or os.getenv("MAINTENANCE_USERS") else {}
        port = int(os.environ.get("MAINTENANCE_PORT", "8000"))
        if not 1 <= port <= 65535:
            raise ValueError("MAINTENANCE_PORT must be between 1 and 65535.")
        with MongoClient(os.environ.get("MONGODB_URI", "mongodb://127.0.0.1:27017"),
                         serverSelectionTimeoutMS=3000) as client:
            database = client[os.environ.get("MONGODB_DATABASE", "field_maintenance")]
            initialize(database)
            migrate_sqlite(database, ROOT / "maintenance.sqlite3")
            with MaintenanceServer(("127.0.0.1", port), database, users, demo_access=demo_access) as server:
                print(f"Open http://127.0.0.1:{port}", flush=True)
                server.serve_forever()
    except (ValueError, PyMongoError, OSError) as error:
        raise SystemExit(f"Could not start Field Maintenance Desk. {error}") from error


if __name__ == "__main__":
    main()
