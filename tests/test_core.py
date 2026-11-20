import zipfile
from pathlib import Path

import pytest

from tadpod.store import Store

FIX = Path(__file__).parent / "fixtures" / "linkedin"


def make_zip(tmp_path: Path, name: str, connections: str | None = None) -> Path:
    z = tmp_path / name
    with zipfile.ZipFile(z, "w") as zf:
        for f in FIX.iterdir():
            text = connections if (connections and f.name == "Connections.csv") else f.read_text()
            zf.writestr(f.name, text)
    return z


@pytest.fixture
def inbox(tmp_path):
    d = tmp_path / "inbox"
    d.mkdir()
    return d


@pytest.fixture
def store(tmp_path, inbox):
    return Store(tmp_path / "home", [inbox])


def rows(store, sql):
    out = store.query(sql)
    assert "error" not in out, out
    return out["rows"]


def test_empty_store_explains_setup(store):
    out = store.query("SELECT count(*) FROM people")
    assert out["rows"] == [[0]] and "Download your data" in out["setup"]


def test_first_import(store, tmp_path):
    res = store.import_linkedin(str(make_zip(tmp_path, "Basic_LinkedInDataExport_11-20-2026.zip")))
    assert res["imported"] == {"export_date": "2026-11-20", "connections": 5, "messages": 4, "invitations": 1}
    assert res["candidate_insights"] and res["example_questions"]
    assert "changes_since_last_import" not in res
    assert rows(store, "SELECT count(*) FROM people WHERE source = 'linkedin'") == [[5]]
    assert rows(store, "SELECT sum(from_me) FROM messages") == [[2]]
    assert "setup" not in store.query("SELECT 1")
    # same file again is a no-op
    assert store.import_linkedin(str(tmp_path / "Basic_LinkedInDataExport_11-20-2026.zip"))["skipped"] is True


def test_reimport_keeps_snapshots_and_reports_changes(store, tmp_path):
    store.import_linkedin(str(make_zip(tmp_path, "a_07-19-2026.zip")))
    newer = (FIX / "Connections.csv").read_text()
    newer = newer.replace("Microsoft,Software Engineer", "Stripe,Staff Engineer")
    newer = newer.replace("Ana,Lopez,https://www.linkedin.com/in/ana-lopez,,,,10 Jan 2018\n", "")
    newer += "Omar,Haddad,https://www.linkedin.com/in/omarh,,Port of Boston,Dock Supervisor,01 Nov 2026\n"
    res = store.import_linkedin(str(make_zip(tmp_path, "b_11-20-2026.zip", newer)))
    ch = res["changes_since_last_import"]
    assert ch["new_connections"] == {"count": 1, "examples": ["Omar Haddad"]}
    assert ch["no_longer_connected"] == {"count": 1, "examples": ["Ana Lopez"]}
    assert ch["role_changes"]["examples"] == [
        {"name": "Priya Shah", "was": "Software Engineer @ Microsoft", "now": "Staff Engineer @ Stripe"}]
    assert rows(store, "SELECT count(*) FROM imports") == [[2]]
    assert rows(store, "SELECT company FROM connections WHERE slug = 'priya-shah-123'") == [["Stripe"]]
    assert rows(store, "SELECT count(DISTINCT import_id) FROM snapshot_connections") == [[2]]


def test_inbox_scan_to_queue_to_context(store, tmp_path, inbox):
    store.import_linkedin(str(make_zip(tmp_path, "x_11-20-2026.zip")))
    f = inbox / "inbox.txt"
    f.write_text(
        "2026-11-18T19:42:00-05:00 https://www.linkedin.com/in/new-person?utm_source=share\n"
        "2026-11-19T08:00:00-05:00 https://www.linkedin.com/in/samortiz\n"
        "garbage line without a url\n"
    )
    q = rows(store, "SELECT slug, name, scanned_at FROM unprocessed ORDER BY scanned_at")
    assert q == [["new-person", None, "2026-11-19 00:42:00"], ["samortiz", "Sam Ortiz", "2026-11-19 13:00:00"]]
    assert f.read_text() == "garbage line without a url\n"  # ingested lines removed, unparsed kept
    rows(store, "SELECT 1")  # re-sync is idempotent
    assert rows(store, "SELECT count(*) FROM scans") == [[2]]

    res = store.add_context("https://www.linkedin.com/in/new-person", "Met at the climbing gym; runs a bakery",
                            name="Rosa Diaz")
    assert res["saved"] and not res.get("created_new_person") and res["person"]["name"] == "Rosa Diaz"
    assert rows(store, "SELECT slug FROM unprocessed") == [["samortiz"]]


def test_phone_clock_ahead_still_clears_queue(store, inbox):
    (inbox / "inbox.txt").write_text("2099-01-01T00:00:00Z https://www.linkedin.com/in/future\n")
    assert rows(store, "SELECT slug FROM unprocessed") == [["future"]]
    store.add_context("https://www.linkedin.com/in/future", "met today")
    assert rows(store, "SELECT count(*) FROM unprocessed") == [[0]]


def test_add_context_resolution(store, tmp_path):
    store.import_linkedin(str(make_zip(tmp_path, "x_11-20-2026.zip")))
    by_name = store.add_context("sam ortiz", "Coffee; she's moving to Mass General's ER", date_="2026-11-01")
    assert by_name["person"]["company"] == "Mass General Brigham" and by_name["date"] == "2026-11-01"
    by_id = store.add_context(str(by_name["person"]["id"]), "Follow-up call")
    assert by_id["person"]["id"] == by_name["person"]["id"]
    new = store.add_context("Tom Reed", "Runs a dairy farm in Vermont")
    assert new["created_new_person"] is True
    store.add_context("https://www.linkedin.com/in/tomreed-2", "Another Tom", name="Tom Reed")
    amb = store.add_context("Tom Reed", "which one?")
    assert amb["ambiguous"] and len(amb["candidates"]) == 2
    assert store.add_context("Sam Ortiz", "x", date_="last tuesday")["error"]
    assert rows(store, "SELECT count(*) FROM context") == [[4]]


def test_name_only_person_links_on_later_import(store, tmp_path):
    store.add_context("Omar Haddad", "Met at the port tour")
    newer = (FIX / "Connections.csv").read_text() + \
        "Omar,Haddad,https://www.linkedin.com/in/omarh,,Port of Boston,Dock Supervisor,01 Nov 2026\n"
    res = store.import_linkedin(str(make_zip(tmp_path, "x_11-20-2026.zip", newer)))
    assert res["linked_by_name"] == ["Omar Haddad"]
    assert rows(store, "SELECT c.company, x.text FROM connections c JOIN context x ON x.person_id = c.person_id") == [
        ["Port of Boston", "Met at the port tour"]]
    assert rows(store, "SELECT count(*) FROM people WHERE name = 'Omar Haddad'") == [[1]]


@pytest.mark.parametrize("sql", [
    "DELETE FROM people",
    "INSERT INTO people(name) VALUES ('x')",
    "DROP TABLE context",
    "ATTACH DATABASE '/tmp/evil.db' AS evil",
    "PRAGMA query_only = OFF",
    "SELECT 1; DELETE FROM people",
])
def test_query_is_read_only(store, tmp_path, sql):
    store.import_linkedin(str(make_zip(tmp_path, "x_11-20-2026.zip")))
    assert "error" in store.query(sql)
    assert rows(store, "SELECT count(*) FROM people") == [[5]]


def test_import_errors(store, tmp_path):
    assert "No file" in store.import_linkedin(str(tmp_path / "nope.zip"))["error"]
    (tmp_path / "empty").mkdir()
    assert "Connections.csv" in store.import_linkedin(str(tmp_path / "empty"))["error"]
