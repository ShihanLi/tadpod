from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime, timezone
from urllib.parse import unquote

_URL_RE = re.compile(r"linkedin\.com/in/([^/?#\s,]+)", re.I)

_DATE_FORMATS = (
    "%Y-%m-%d %H:%M:%S UTC",  # messages.csv
    "%d %b %Y",               # Connections.csv: 26 Jun 2026
    "%m/%d/%y, %I:%M %p",     # Invitations.csv: 6/25/26, 7:21 AM
    "%Y-%m-%d",
)

TS_FORMAT = "%Y-%m-%d %H:%M:%S"  # every stored timestamp is UTC in this format


def slug_of(url: str | None) -> str | None:
    """'https://www.linkedin.com/in/Jane-Kim/?utm=x' -> 'jane-kim'."""
    m = _URL_RE.search(url or "")
    return unquote(m.group(1)).strip("/").lower() if m else None


def slugs_in(text: str | None) -> list[str]:
    return [unquote(s).strip("/").lower() for s in _URL_RE.findall(text or "")]


def norm_name(name: str | None) -> str:
    s = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-z0-9 ]+", " ", s.lower())
    return " ".join(s.split())


def parse_datetime(value: str | None) -> datetime | None:
    value = (value or "").strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


def parse_date(value: str | None) -> date | None:
    dt = parse_datetime(value)
    return dt.date() if dt else None


def iso(d: date | datetime | None) -> str | None:
    if d is None:
        return None
    return (d.date() if isinstance(d, datetime) else d).isoformat()


def parse_iso_timestamp(value: str) -> datetime | None:
    """ISO 8601 with optional offset ('Z' ok). Naive values are taken as local time."""
    try:
        dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt.astimezone() if dt.tzinfo is None else dt


def utc_ts(dt: datetime | None = None) -> str:
    dt = dt or datetime.now(timezone.utc)
    return dt.astimezone(timezone.utc).strftime(TS_FORMAT)
