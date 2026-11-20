"""tadpod MCP server: import, add_context, query.

Runs over stdio and makes no network calls. Data lives in ~/.tadpod/tadpod.db (override with
TADPOD_HOME). Whatever a tool returns is sent to the AI app (and its model provider) that called it.
"""

from __future__ import annotations

import argparse
import os

from mcp.server.mcpserver import MCPServer

from .store import SCHEMA, Store

mcp = MCPServer(
    "tadpod",
    instructions=(
        "tadpod is the user's local, private record of the people they know: their LinkedIn export, people they "
        "scanned on their phone, and free-form context they've written about people. If nothing is imported yet, "
        "guide them through setup (query returns instructions). Only go through unprocessed scans when the user "
        "asks (e.g. 'process my queue'): for each, show when it was scanned, ask for free-form context, and save "
        "it with add_context. Don't bring the queue up otherwise. Never send messages on the user's behalf."
    ),
)

_store: Store | None = None


def store() -> Store:
    global _store
    if _store is None:
        _store = Store()
    return _store


@mcp.tool(description="""Import a LinkedIn data export (the .zip LinkedIn emails, or the unzipped folder) from a
local file path the user gives you. Each import is kept as a dated snapshot; the newest is current. Returns counts,
candidate insights to share (pick the 1-2 most striking), example questions, and on re-imports what changed
(new connections, people no longer connected, role changes). Never ask the user to attach the file to the chat.""")
def import_linkedin(path: str) -> dict:
    return store().import_linkedin(path)


@mcp.tool(description="""Save a dated free-form context entry about a person: how you met, what you talked about,
anything. `person` is a LinkedIn URL, a full name, or a person id from query results. If nobody matches, a new
person is created (say so to the user). An ambiguous name returns candidates; ask which one and retry with the id.
`name` sets the name of a person known only by URL (e.g. a fresh scan). `date` (YYYY-MM-DD) defaults to today;
set it when the user is describing an earlier day.""")
def add_context(person: str, text: str, name: str | None = None, date: str | None = None) -> dict:
    return store().add_context(person, text, name, date)


@mcp.tool(description=f"""Run one read-only SQLite SELECT over the user's tadpod data.

Tables and views:
{SCHEMA}
Notes:
- people.slug is the linkedin.com/in/<slug> part of a profile URL; join everything on person_id or slug.
- scanned_at, noted_at, added_at, imported_at are UTC 'YYYY-MM-DD HH:MM:SS' (show local time with
  datetime(x, 'localtime')). messages.date is UTC 'YYYY-MM-DD HH:MM'. Other dates are 'YYYY-MM-DD'.
- company/position are as of the latest import; compare imports via snapshot_connections.import_id.
- from_me = 1 for messages the user sent. LIKE is case-insensitive.
- At most max_rows rows (default 200, max 1000); long text is truncated.""")
def query(sql: str, max_rows: int = 200) -> dict:
    return store().query(sql, max_rows)


def main() -> None:
    global _store
    parser = argparse.ArgumentParser(description="tadpod: your people, on your machine.")
    parser.add_argument("--home", default=os.environ.get("TADPOD_HOME"),
                        help="Data folder (default ~/.tadpod).")
    parser.add_argument("--inbox", default=os.environ.get("TADPOD_INBOX"),
                        help="Folder the phone shortcut writes to (default: tadpod folder in iCloud Drive).")
    args = parser.parse_args()
    _store = Store(args.home, [args.inbox] if args.inbox else None)
    mcp.run()


if __name__ == "__main__":
    main()
