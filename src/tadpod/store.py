"""tadpod's local store: one SQLite file holding LinkedIn import snapshots, people, scans and context.

No network access. server.py is a thin MCP layer on top.
"""

from __future__ import annotations

import csv
import hashlib
import io
import os
import re
import sqlite3
import zipfile
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from .util import iso, norm_name, parse_date, parse_datetime, parse_iso_timestamp, slug_of, slugs_in, utc_ts

ICLOUD = Path("~/Library/Mobile Documents").expanduser()
# The Shortcuts app's "Append to Text File" writes under iCloud Drive/Shortcuts by default;
# a plain iCloud Drive folder works too.
DEFAULT_INBOX_DIRS = (
    ICLOUD / "iCloud~is~workflow~my~workflows" / "Documents" / "tadpod",
    ICLOUD / "com~apple~CloudDocs" / "tadpod",
)
EXPORT_FILES = ("Connections.csv", "messages.csv", "Invitations.csv")

SCHEMA = """\
people(id, slug, url, name, source, added_at)               -- everyone tadpod knows; source: linkedin | scan | chat
context(id, person_id, date, text, noted_at)                -- dated free-form notes about a person
scans(id, person_id, scanned_at, ingested_at, raw)          -- QR scans from the phone shortcut
unprocessed(scan_id, person_id, slug, url, name, scanned_at) -- view: scans with no context written since
connections(person_id, slug, name, first_name, last_name, company, position, connected_on, email, url)
                                                            -- view: 1st-degree connections in the latest import
snapshot_connections(import_id, slug, name, first_name, last_name, company, position, connected_on, email, url)
imports(id, imported_at, export_date, source_file, connections, messages, invitations)
messages(id, conversation_id, conversation_title, date, sender_name, sender_slug, from_me, content)
message_participants(message_id, slug)                      -- everyone in each message except the user
invitations(direction, sent_at, other_name, other_slug, message)
"""

_DDL = """
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS imports(
  id INTEGER PRIMARY KEY, imported_at TEXT, export_date TEXT, source_file TEXT, sha256 TEXT UNIQUE,
  connections INTEGER, messages INTEGER, invitations INTEGER);
CREATE TABLE IF NOT EXISTS snapshot_connections(
  import_id INTEGER REFERENCES imports(id), slug TEXT, name TEXT, first_name TEXT, last_name TEXT,
  company TEXT, position TEXT, connected_on TEXT, email TEXT, url TEXT);
CREATE INDEX IF NOT EXISTS snap_import_slug ON snapshot_connections(import_id, slug);
CREATE TABLE IF NOT EXISTS people(
  id INTEGER PRIMARY KEY, slug TEXT UNIQUE, url TEXT, name TEXT, source TEXT, added_at TEXT);
CREATE TABLE IF NOT EXISTS context(
  id INTEGER PRIMARY KEY, person_id INTEGER REFERENCES people(id), date TEXT, text TEXT, noted_at TEXT);
CREATE INDEX IF NOT EXISTS context_person ON context(person_id);
CREATE TABLE IF NOT EXISTS scans(
  id INTEGER PRIMARY KEY, person_id INTEGER REFERENCES people(id), scanned_at TEXT, ingested_at TEXT, raw TEXT,
  UNIQUE(person_id, scanned_at));
CREATE TABLE IF NOT EXISTS messages(
  id INTEGER PRIMARY KEY, conversation_id TEXT, conversation_title TEXT, date TEXT,
  sender_name TEXT, sender_slug TEXT, from_me INTEGER, content TEXT);
CREATE TABLE IF NOT EXISTS message_participants(message_id INTEGER, slug TEXT);
CREATE INDEX IF NOT EXISTS participants_slug ON message_participants(slug);
CREATE TABLE IF NOT EXISTS invitations(direction TEXT, sent_at TEXT, other_name TEXT, other_slug TEXT, message TEXT);
CREATE VIEW IF NOT EXISTS connections AS
  SELECT p.id AS person_id, s.slug, s.name, s.first_name, s.last_name, s.company, s.position,
         s.connected_on, s.email, s.url
  FROM snapshot_connections s LEFT JOIN people p ON p.slug = s.slug
  WHERE s.import_id = (SELECT max(id) FROM imports WHERE connections > 0);
CREATE VIEW IF NOT EXISTS unprocessed AS
  SELECT s.id AS scan_id, p.id AS person_id, p.slug, p.url, p.name, s.scanned_at
  FROM scans s JOIN people p ON p.id = s.person_id
  WHERE NOT EXISTS (SELECT 1 FROM context c WHERE c.person_id = s.person_id AND c.noted_at >= s.ingested_at);
"""

SETUP = (
    "No LinkedIn data imported yet. Walk the user through it: on LinkedIn go to Me -> Settings & Privacy -> "
    "Data privacy -> Download your data. Pick the full archive (needed for message history; it can take up to "
    "24 hours) or just Connections (minutes). When the email arrives, download the zip, then call "
    "import_linkedin with its file path. Don't have them attach the zip to the chat; that would send it to the model."
)

EXAMPLE_QUESTIONS = [
    "Who did I message a lot but haven't talked to in over a year?",
    "Which companies do most of my connections work at?",
    "What did <name> and I last talk about?",
    "Who have I connected with in the last 6 months?",
]

_READ_ACTIONS = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION, sqlite3.SQLITE_RECURSIVE}
MAX_ROWS = 1000
MAX_CELL_CHARS = 1000
_INBOX_TS = re.compile(r"^\s*(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?)")
_ANY_URL = re.compile(r"https?://\S+")
_EXPORT_DATE = re.compile(r"(\d{2})-(\d{2})-(\d{4})")


def _s(row: dict, key: str) -> str | None:
    return (row.get(key) or "").strip() or None


def _csv(text: str | None, header_prefix: str | None = None) -> list[dict[str, str]]:
    if not text:
        return []
    if header_prefix:
        # Connections.csv starts with a few lines of notes before the real header.
        idx = text.find(header_prefix)
        text = text[idx:] if idx != -1 else ""
    return list(csv.DictReader(io.StringIO(text)))


def _read_export(path: Path) -> tuple[dict[str, str], date | None]:
    """Return ({basename: text}, newest file date) for the export files we use, from a zip or a folder."""
    wanted = {n.lower(): n for n in EXPORT_FILES}
    out: dict[str, str] = {}
    dates: list[date] = []
    if path.is_dir():
        for p in path.rglob("*.csv"):
            if p.name.lower() in wanted:
                out[wanted[p.name.lower()]] = p.read_text(encoding="utf-8-sig", errors="replace")
                dates.append(datetime.fromtimestamp(p.stat().st_mtime).date())
    elif zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as z:
            for info in z.infolist():
                base = Path(info.filename).name.lower()
                if base in wanted:
                    out[wanted[base]] = z.read(info).decode("utf-8-sig", errors="replace")
                    dates.append(date(*info.date_time[:3]))
    return out, (max(dates) if dates else None)


def _read_only_authorizer(action, *_):
    return sqlite3.SQLITE_OK if action in _READ_ACTIONS else sqlite3.SQLITE_DENY


class Store:
    def __init__(self, home: Path | str | None = None, inbox_dirs: list[Path] | None = None):
        self.home = Path(home or os.environ.get("TADPOD_HOME") or "~/.tadpod").expanduser()
        self.home.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.db_path = self.home / "tadpod.db"
        if inbox_dirs is None:
            env = os.environ.get("TADPOD_INBOX")
            inbox_dirs = [Path(env).expanduser()] if env else list(DEFAULT_INBOX_DIRS)
        self.inbox_dirs = [Path(d) for d in inbox_dirs]
        self.db = sqlite3.connect(self.db_path, check_same_thread=False)
        self.db.execute("PRAGMA journal_mode = WAL")
        self.db.executescript(_DDL)
        self.db.commit()

    # ------------------------------------------------------------------ helpers

    def _meta(self, key: str) -> str | None:
        row = self.db.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def _has_import(self) -> bool:
        return self.db.execute("SELECT 1 FROM imports LIMIT 1").fetchone() is not None

    def _person_by_url(self, url: str) -> int | None:
        slug = slug_of(url)
        row = (self.db.execute("SELECT id FROM people WHERE slug = ?", (slug,)) if slug
               else self.db.execute("SELECT id FROM people WHERE slug IS NULL AND url = ?", (url,))).fetchone()
        return row[0] if row else None

    def _create_person(self, *, url: str | None = None, name: str | None = None, source: str) -> int:
        cur = self.db.execute(
            "INSERT INTO people(slug, url, name, source, added_at) VALUES (?,?,?,?,?)",
            (slug_of(url), url, name, source, utc_ts()),
        )
        return cur.lastrowid

    # ------------------------------------------------------------------ inbox

    def sync_inbox(self) -> dict:
        """Move new lines from the phone inbox file(s) into scans. Safe to call often."""
        added, notes = 0, []
        for folder in self.inbox_dirs:
            if not folder.is_dir():
                continue
            for p in sorted(folder.iterdir()):
                if p.name.startswith(".") and p.name.endswith(".icloud"):
                    notes.append(f"{p.name[1:-7]} is in iCloud but not downloaded to this Mac yet")
                    continue
                if p.suffix.lower() != ".txt":
                    continue
                original = p.read_text(encoding="utf-8", errors="replace")
                keep = []
                for line in original.splitlines():
                    if not line.strip():
                        continue
                    url_m = _ANY_URL.search(line)
                    if not url_m:
                        keep.append(line)  # never silently drop something we can't parse
                        continue
                    url = url_m.group(0).rstrip(").,;")
                    ts_m = _INBOX_TS.match(line)
                    when = parse_iso_timestamp(ts_m.group(1)) if ts_m else None
                    scanned_at = utc_ts(when or datetime.fromtimestamp(p.stat().st_mtime, timezone.utc))
                    person = self._person_by_url(url) or self._create_person(url=url, source="scan")
                    cur = self.db.execute(
                        "INSERT OR IGNORE INTO scans(person_id, scanned_at, ingested_at, raw) VALUES (?,?,?,?)",
                        (person, scanned_at, utc_ts(), line.strip()),
                    )
                    added += cur.rowcount
                self.db.commit()
                # Remove what we ingested, keeping anything appended while we were working.
                current = p.read_text(encoding="utf-8", errors="replace")
                if current.startswith(original):
                    rest = current[len(original):]
                    p.write_text("".join(l + "\n" for l in keep) + rest.lstrip("\n"), encoding="utf-8")
        return {"new_scans": added, **({"notes": notes} if notes else {})}

    # ------------------------------------------------------------------ import

    def import_linkedin(self, path: str) -> dict:
        self.sync_inbox()
        p = Path(path).expanduser()
        if not p.exists():
            return {"error": f"No file or folder at {p}", "setup": SETUP}
        files, files_date = _read_export(p)
        if "Connections.csv" not in files:
            return {"error": f"No Connections.csv found in {p}. Is this a LinkedIn data export?", "setup": SETUP}
        digest = hashlib.sha256("\0".join(files.get(n, "") for n in EXPORT_FILES).encode()).hexdigest()
        if self.db.execute("SELECT 1 FROM imports WHERE sha256 = ?", (digest,)).fetchone():
            return {"skipped": True, "reason": "This exact export was already imported."}

        m = _EXPORT_DATE.search(p.name)
        try:
            export_date = date(int(m.group(3)), int(m.group(1)), int(m.group(2))).isoformat() if m else None
        except ValueError:
            export_date = None
        export_date = export_date or iso(files_date)

        prev_import = self.db.execute("SELECT max(id) FROM imports WHERE connections > 0").fetchone()[0]
        db = self.db
        with db:
            conns = []
            for r in _csv(files["Connections.csv"], header_prefix="First Name"):
                first, last = _s(r, "First Name") or "", _s(r, "Last Name") or ""
                conns.append(dict(
                    slug=slug_of(r.get("URL")), name=f"{first} {last}".strip(), first_name=first, last_name=last,
                    company=_s(r, "Company"), position=_s(r, "Position"),
                    connected_on=iso(parse_date(r.get("Connected On"))), email=_s(r, "Email Address"), url=_s(r, "URL"),
                ))
            msg_rows = [r for r in _csv(files.get("messages.csv"))
                        if (r.get("IS MESSAGE DRAFT") or "").strip().lower() != "yes"]
            inv_rows = _csv(files.get("Invitations.csv"))

            cur = db.execute(
                "INSERT INTO imports(imported_at, export_date, source_file, sha256, connections, messages, invitations)"
                " VALUES (?,?,?,?,?,?,?)",
                (utc_ts(), export_date, p.name, digest, len(conns),
                 len(msg_rows) if "messages.csv" in files else None,
                 len(inv_rows) if "Invitations.csv" in files else None),
            )
            import_id = cur.lastrowid
            db.executemany(
                "INSERT INTO snapshot_connections VALUES (:import_id,:slug,:name,:first_name,:last_name,"
                ":company,:position,:connected_on,:email,:url)",
                [{**c, "import_id": import_id} for c in conns],
            )
            for c in conns:
                if not c["slug"]:
                    continue
                db.execute(
                    "INSERT OR IGNORE INTO people(slug, url, name, source, added_at) VALUES (?,?,?,?,?)",
                    (c["slug"], c["url"], c["name"], "linkedin", utc_ts()),
                )
                db.execute("UPDATE people SET name = ? WHERE slug = ? AND (name IS NULL OR name = '')",
                           (c["name"], c["slug"]))
            merged = self._merge_name_only_people(conns)

            if "messages.csv" in files:
                self._replace_messages(msg_rows)
            if "Invitations.csv" in files:
                self._replace_invitations(inv_rows)

        result = {
            "imported": {"export_date": export_date, "connections": len(conns),
                         "messages": len(msg_rows) if "messages.csv" in files else "not in export (kept previous)",
                         "invitations": len(inv_rows) if "Invitations.csv" in files else "not in export (kept previous)"},
            "candidate_insights": self._insights(),
            "example_questions": EXAMPLE_QUESTIONS,
            "present": "Tell the user it worked, share the 1-2 most striking insights, and offer the example questions.",
        }
        if merged:
            result["linked_by_name"] = merged
        if prev_import:
            result["changes_since_last_import"] = self._diff(prev_import, import_id)
        return result

    def _merge_name_only_people(self, conns: list[dict]) -> list[str]:
        """People added by name only (no URL) get linked when exactly one connection has that name."""
        by_name: dict[str, list[dict]] = {}
        for c in conns:
            if c["slug"]:
                by_name.setdefault(norm_name(c["name"]), []).append(c)
        name_only: dict[str, list[tuple[int, str]]] = {}
        for pid, name in self.db.execute("SELECT id, name FROM people WHERE slug IS NULL AND url IS NULL"):
            name_only.setdefault(norm_name(name), []).append((pid, name))
        merged = []
        for key, people in name_only.items():
            matches = by_name.get(key, [])
            if len(people) != 1 or len(matches) != 1:
                continue
            pid, name = people[0]
            target = self.db.execute("SELECT id FROM people WHERE slug = ?", (matches[0]["slug"],)).fetchone()[0]
            self.db.execute("UPDATE context SET person_id = ? WHERE person_id = ?", (target, pid))
            self.db.execute("UPDATE scans SET person_id = ? WHERE person_id = ?", (target, pid))
            self.db.execute("DELETE FROM people WHERE id = ?", (pid,))
            merged.append(name)
        return merged

    def _replace_messages(self, rows: list[dict]) -> None:
        parsed = []
        for r in rows:
            sender = slug_of(r.get("SENDER PROFILE URL"))
            people = set(slugs_in(r.get("RECIPIENT PROFILE URLS")))
            if sender:
                people.add(sender)
            parsed.append((r, sender, people))
        # The export never states the owner's own URL; it's the one present in the most messages.
        counts = Counter(s for _, _, ppl in parsed for s in ppl)
        me = counts.most_common(1)[0][0] if counts else None
        db = self.db
        db.execute("DELETE FROM message_participants")
        db.execute("DELETE FROM messages")
        db.execute("INSERT OR REPLACE INTO meta VALUES ('self_slug', ?)", (me,))
        for i, (r, sender, people) in enumerate(parsed, 1):
            dt = parse_datetime(r.get("DATE"))
            db.execute(
                "INSERT INTO messages VALUES (?,?,?,?,?,?,?,?)",
                (i, _s(r, "CONVERSATION ID"), _s(r, "CONVERSATION TITLE") or _s(r, "SUBJECT"),
                 dt.strftime("%Y-%m-%d %H:%M") if dt else None, _s(r, "FROM"), sender,
                 int(sender is not None and sender == me), _s(r, "CONTENT")),
            )
            db.executemany("INSERT INTO message_participants VALUES (?,?)", [(i, s) for s in people - {me}])

    def _replace_invitations(self, rows: list[dict]) -> None:
        self.db.execute("DELETE FROM invitations")
        for r in rows:
            direction = (_s(r, "Direction") or "").lower() or None
            out = direction == "outgoing"
            self.db.execute(
                "INSERT INTO invitations VALUES (?,?,?,?,?)",
                (direction, iso(parse_datetime(r.get("Sent At"))), _s(r, "To") if out else _s(r, "From"),
                 slug_of(r.get("inviteeProfileUrl") if out else r.get("inviterProfileUrl")), _s(r, "Message")),
            )

    def _diff(self, old: int, new: int, limit: int = 15) -> dict:
        def snap(i):
            return {r[0]: r for r in self.db.execute(
                "SELECT slug, name, company, position FROM snapshot_connections WHERE import_id = ? AND slug IS NOT NULL", (i,))}
        a, b = snap(old), snap(new)
        added = [b[s][1] for s in b.keys() - a.keys()]
        gone = [a[s][1] for s in a.keys() - b.keys()]
        changed = [
            {"name": b[s][1], "was": " @ ".join(filter(None, [a[s][3], a[s][2]])),
             "now": " @ ".join(filter(None, [b[s][3], b[s][2]]))}
            for s in a.keys() & b.keys() if (a[s][2], a[s][3]) != (b[s][2], b[s][3])
        ]
        return {
            "new_connections": {"count": len(added), "examples": sorted(added)[:limit]},
            "no_longer_connected": {"count": len(gone), "examples": sorted(gone)[:limit]},
            "role_changes": {"count": len(changed), "examples": changed[:limit]},
        }

    def _insights(self, today: date | None = None) -> list[str]:
        today = today or date.today()
        db = self.db
        total = db.execute("SELECT count(*) FROM connections").fetchone()[0]
        if not total:
            return []
        out = []
        years = dict(db.execute(
            "SELECT substr(connected_on,1,4), count(*) FROM connections WHERE connected_on IS NOT NULL GROUP BY 1"))
        if years:
            peak = max(years, key=years.get)
            recent = db.execute("SELECT count(*) FROM connections WHERE connected_on >= ?",
                                ((today - timedelta(days=365)).isoformat(),)).fetchone()[0]
            out.append(f"Your busiest year for new connections was {peak} ({years[peak]}); "
                       f"you added {recent} in the last 12 months.")
        top = db.execute("SELECT company, count(*) n FROM connections WHERE company IS NOT NULL "
                         "GROUP BY company ORDER BY n DESC LIMIT 5").fetchall()
        if top:
            share = round(100 * sum(n for _, n in top) / total)
            out.append(f"{share}% of your {total} connections work at just 5 companies, led by {top[0][0]} ({top[0][1]}).")
        messaged = db.execute("SELECT count(DISTINCT c.slug) FROM connections c "
                              "JOIN message_participants p ON p.slug = c.slug").fetchone()[0]
        if messaged:
            out.append(f"You've exchanged LinkedIn messages with {messaged} of {total} connections "
                       f"({round(100 * messaged / total)}%).")
            dormant = db.execute(
                "SELECT count(*) FROM (SELECT c.slug FROM connections c JOIN message_participants p ON p.slug = c.slug "
                "JOIN messages m ON m.id = p.message_id GROUP BY c.slug HAVING count(*) >= 3 AND max(m.date) < ?)",
                ((today - timedelta(days=365)).isoformat(),)).fetchone()[0]
            if dormant:
                out.append(f"{dormant} people you used to message regularly (3+ messages) have been quiet for over a year.")
        return out

    # ------------------------------------------------------------------ context

    def add_context(self, person: str, text: str, name: str | None = None, date_: str | None = None) -> dict:
        self.sync_inbox()
        if not (text or "").strip():
            return {"error": "text is empty"}
        entry_date = date_ or date.today().isoformat()
        if not parse_date(entry_date):
            return {"error": f"date must be YYYY-MM-DD, got {date_!r}"}
        person = (person or "").strip()
        created = False
        url_m = _ANY_URL.search(person)
        if person.isdigit():
            row = self.db.execute("SELECT id FROM people WHERE id = ?", (int(person),)).fetchone()
            if not row:
                return {"error": f"No person with id {person}"}
            pid = row[0]
        elif url_m:
            url = url_m.group(0)
            pid = self._person_by_url(url)
            if pid is None:
                pid, created = self._create_person(url=url, name=name, source="chat"), True
        else:
            key = norm_name(person)
            if not key:
                return {"error": "person is empty"}
            matches = [(i, n) for i, n in self.db.execute("SELECT id, name FROM people WHERE name IS NOT NULL")
                       if norm_name(n) == key]
            if len(matches) > 1:
                return {"ambiguous": True, "candidates": self._describe([i for i, _ in matches]),
                        "hint": "Ask the user which one, then call again with the person id."}
            if matches:
                pid = matches[0][0]
            else:
                pid, created = self._create_person(name=name or person, source="chat"), True
        if name:
            self.db.execute("UPDATE people SET name = ? WHERE id = ? AND (name IS NULL OR name = '')", (name, pid))
        cur = self.db.execute("INSERT INTO context(person_id, date, text, noted_at) VALUES (?,?,?,?)",
                              (pid, entry_date, text.strip(), utc_ts()))
        self.db.commit()
        out = {"saved": True, "entry_id": cur.lastrowid, "date": entry_date, "person": self._describe([pid])[0]}
        if created:
            out["created_new_person"] = True
        return out

    def _describe(self, ids: list[int]) -> list[dict]:
        out = []
        for pid in ids:
            p = self.db.execute("SELECT id, name, url, source FROM people WHERE id = ?", (pid,)).fetchone()
            c = self.db.execute("SELECT company, position FROM connections WHERE person_id = ?", (pid,)).fetchone()
            out.append({"id": p[0], "name": p[1], "url": p[2], "source": p[3],
                        "company": c[0] if c else None, "position": c[1] if c else None})
        return out

    # ------------------------------------------------------------------ query

    def query(self, sql: str, max_rows: int = 200) -> dict:
        inbox = self.sync_inbox()
        max_rows = max(1, min(max_rows, MAX_ROWS))
        ro = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
        try:
            ro.execute("PRAGMA query_only = ON")
            ro.set_authorizer(_read_only_authorizer)
            cur = ro.execute(sql)
            columns = [c[0] for c in cur.description or []]
            rows = cur.fetchmany(max_rows + 1)
        except sqlite3.Error as e:
            return {"error": str(e), "schema": SCHEMA}
        finally:
            ro.close()

        def cell(v):
            return v[:MAX_CELL_CHARS] + "…" if isinstance(v, str) and len(v) > MAX_CELL_CHARS else v

        out = {"columns": columns, "rows": [[cell(v) for v in r] for r in rows[:max_rows]],
               "truncated": len(rows) > max_rows}
        if not self._has_import():
            out["setup"] = SETUP
        if inbox.get("notes"):
            out["inbox_notes"] = inbox["notes"]
        return out
