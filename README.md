# Field Maintenance Desk

A small Python and SQLite demo for tracking equipment issues. A field worker logs an observation against a demo asset. The maintenance queue displays the issue and lets a reviewer mark it resolved.

## Run locally

Requires Python 3.9 or later. No external packages are required.

```sh
python app.py
```

Open <http://127.0.0.1:8000>. Select PUMP-02, choose Hydraulic and enter “Small fluid leak observed near the pump casing.” Log the issue, reload the page to see it persist, then mark it resolved.

## Design

| Component | Responsibility |
| --- | --- |
| HTML and JavaScript | Issue form, maintenance queue and status updates |
| Python HTTP server | JSON request validation and REST endpoints |
| SQLite | Ticket storage with parameterised SQL and a status constraint |

The API uses `GET /api/tickets` to list tickets, `POST /api/tickets` to create a ticket and `PATCH /api/tickets/{id}` to mark it resolved. The server validates asset, category and description independently of the browser.

The initial scaffold was created with OpenAI Codex. The app uses fictional data and runs locally as a portfolio demo.
