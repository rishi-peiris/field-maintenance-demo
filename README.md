# Field Maintenance Desk

Register equipment by site and track maintenance tickets through an audited lifecycle.

## Run locally on Windows

Start MongoDB Community Edition on loopback at port 27017. The app works with a standalone server. See the [MongoDB Windows installation guide](https://www.mongodb.com/docs/manual/tutorial/install-mongodb-on-windows/).

In PowerShell, from this folder:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
$supervisorToken = .\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(32))"
$technicianToken = .\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(32))"
$env:MAINTENANCE_USERS = @{ Supervisor = $supervisorToken; Technician = $technicianToken } | ConvertTo-Json -Compress
.\.venv\Scripts\python.exe app.py
```

Open <http://127.0.0.1:8000> and sign in with one of the configured tokens. For example, before starting the server you can run `$supervisorToken | Set-Clipboard` and paste it into the sign-in form. Tokens stay in the page for the current session. Give each person their own token to identify changes in the audit trail. Configured accounts currently share the same permissions.

The default MongoDB URI is `mongodb://127.0.0.1:27017`. Set `MONGODB_URI` and `MONGODB_DATABASE` to override the connection or the default database name `field_maintenance`.

## Local demo sign-in and sample data

With MongoDB running, use PowerShell in the project folder:

```powershell
.\.venv\Scripts\python.exe seed_demo.py
$env:MAINTENANCE_DEMO_ACCESS = '1'
.\.venv\Scripts\python.exe app.py
```

Open the URL printed by the app and choose **Continue as Demo Supervisor**. Demo access is opt-in, runs on loopback, and allows anyone using that local instance to edit its data. Access-token sign-in remains available. Set `MAINTENANCE_PORT` if another process already uses port 8000. The sample-data script can be rerun without overwriting existing records.

## Use the desk

1. Add a site. Add an asset under it with its code, name, serial number, location, and service dates. Use **Edit asset** to update the dates or move imported equipment to its actual site.
2. Log a ticket against a registered asset. Use the site and status filters in the queue.
3. Move the ticket from **New → Triaged → Assigned → In Progress → Resolved → Closed**. Choose a configured assignee when assigning. Expand **Status history** to see each actor and timestamp.
4. Expand **Repair history** on an asset to see its resolved or closed tickets and imported work orders.

## MongoDB model and SQLite import

- `sites` contains site codes and names. `assets.site_id` references the site. Asset code and populated serial number are unique within a site and compared in uppercase.
- `tickets.asset_id` references the equipment. Each ticket embeds its short lifecycle history. A conditional MongoDB update changes the status and appends its event atomically.
- `work_orders` preserves legacy work orders with their existing histories and links to imported tickets.
- Startup reads `maintenance.sqlite3` in read-only mode when present. Imported equipment starts at the **Imported equipment** site. Stable import IDs let startup resume or repeat without overwriting records already changed in MongoDB. Preserve the SQLite file as a source snapshot and use MongoDB for subsequent changes.
- Imported tickets retain their original IDs and statuses in legacy fields. Resolved tickets and completed work orders map to **Resolved**. Open tickets with work in progress map to **In Progress**. Other open tickets map to **New**. The import leaves unknown actors and timestamps empty and preserves recorded work-order timestamps.

## API

All API requests authenticate with `Authorization: Bearer <token>`.

| Endpoint | Use |
| --- | --- |
| `GET /api/session` | Current actor, configured assignees, and workflow statuses |
| `GET /api/sites`, `POST /api/sites` | List or register sites |
| `GET /api/assets`, `POST /api/assets` | List or register assets. Filter by `site_id` or `serial_number`. |
| `PATCH /api/assets/{id}` | Edit equipment and service dates |
| `GET /api/assets/{id}/repairs` | Read related repair tickets and legacy orders |
| `GET /api/tickets`, `POST /api/tickets` | List or log tickets. Filter by `site_id`, `asset_id`, or `status`. |
| `PATCH /api/tickets/{id}/status` | Move to the next status. Include `assigned_to` when assigning. |

Run `.\.venv\Scripts\python.exe -m unittest -v` with MongoDB running. The tests use a uniquely named temporary database. Set `MONGODB_TEST_URI` to override the test connection.
