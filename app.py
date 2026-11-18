"""Local maintenance ticket demo using Python and SQLite."""
import json
import sqlite3
from contextlib import closing
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DB = ROOT / "maintenance.sqlite3"
ASSETS = ("DRILL-01", "PUMP-02", "GEN-03")
CATEGORIES = ("Mechanical", "Electrical", "Hydraulic", "Other")


def connect():
    database = sqlite3.connect(DB)
    database.row_factory = sqlite3.Row
    return database


def initialize():
    with closing(connect()) as database, database:
        database.execute("""
            CREATE TABLE IF NOT EXISTS tickets (
                id INTEGER PRIMARY KEY,
                asset TEXT NOT NULL,
                category TEXT NOT NULL,
                description TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'Open'
                    CHECK (status IN ('Open', 'Resolved'))
            )
        """)


class Handler(BaseHTTPRequestHandler):
    def reply(self, status, payload):
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.end_headers()
        self.wfile.write(json.dumps(payload).encode())

    def do_GET(self):
        if self.path == "/":
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write((ROOT / "index.html").read_bytes())
        elif self.path == "/api/tickets":
            with closing(connect()) as database:
                rows = database.execute("SELECT * FROM tickets ORDER BY id DESC")
                self.reply(HTTPStatus.OK, [dict(row) for row in rows])
        else:
            self.reply(HTTPStatus.NOT_FOUND, {"error": "Page not found."})

    def do_POST(self):
        if self.path != "/api/tickets":
            self.reply(HTTPStatus.NOT_FOUND, {"error": "Endpoint not found."})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 4096:
                raise ValueError
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise ValueError
        except (ValueError, UnicodeError):
            self.reply(HTTPStatus.BAD_REQUEST, {"error": "Send a valid JSON object up to 4 KB."})
            return

        asset = data.get("asset")
        category = data.get("category")
        description = data.get("description")
        if (asset not in ASSETS or category not in CATEGORIES
                or not isinstance(description, str)
                or not 10 <= len(description.strip()) <= 500):
            self.reply(HTTPStatus.BAD_REQUEST, {"error": "Choose an asset and category and enter 10–500 characters."})
            return

        with closing(connect()) as database, database:
            cursor = database.execute(
                "INSERT INTO tickets (asset, category, description) VALUES (?, ?, ?)",
                (asset, category, description.strip()),
            )
            ticket_id = cursor.lastrowid
        self.reply(HTTPStatus.CREATED, {"id": ticket_id})

    def do_PATCH(self):
        prefix = "/api/tickets/"
        identifier = self.path.removeprefix(prefix)
        if (not self.path.startswith(prefix) or not identifier.isascii()
                or not identifier.isdecimal() or len(identifier) > 18):
            self.reply(HTTPStatus.NOT_FOUND, {"error": "Ticket not found."})
            return
        with closing(connect()) as database, database:
            cursor = database.execute(
                "UPDATE tickets SET status = 'Resolved' WHERE id = ?", (int(identifier),)
            )
            found = cursor.rowcount > 0
        if found:
            self.reply(HTTPStatus.OK, {"status": "Resolved"})
        else:
            self.reply(HTTPStatus.NOT_FOUND, {"error": "Ticket not found."})


if __name__ == "__main__":
    initialize()
    with ThreadingHTTPServer(("127.0.0.1", 8000), Handler) as server:
        print("Open http://127.0.0.1:8000", flush=True)
        server.serve_forever()
