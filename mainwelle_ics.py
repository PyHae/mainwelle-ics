#!/usr/bin/env python3
"""Radio Mainwelle Veranstaltungskalender -> iCalendar (.ics).

Die Seite https://www.mainwelle.de/veranstaltungskalender/ bettet alle Termine
als JSON ("subtype":"events" ... "items":[...]) in das HTML ein. Dieses Skript
liest das JSON aus und schreibt eine RFC-5545-konforme ICS-Datei.

Nur Python-Standardbibliothek. Aufruf:
    python3 mainwelle_ics.py -o docs/mainwelle.ics
    python3 mainwelle_ics.py --items tests/items.json -o out.ics   # offline
"""
from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import re
import sys
import urllib.error
import urllib.request
from zoneinfo import ZoneInfo

SOURCE_URL = "https://www.mainwelle.de/veranstaltungskalender/"
TZ = ZoneInfo("Europe/Berlin")
UTC = dt.timezone.utc
DEFAULT_DURATION = dt.timedelta(hours=2)
USER_AGENT = "mainwelle-ics/1.0 (private calendar feed; 1 request/day)"

TIME_RE = re.compile(r"(?<!\d)(\d{1,2})(?:\s*[:.]\s*(\d{2}))?(?!\d)")


# --------------------------------------------------------------------------- fetch

def fetch_html(url: str = SOURCE_URL) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read().decode("utf-8")


def extract_items(page: str) -> list[dict]:
    """Findet das eingebettete Events-Array und parst es als JSON."""
    anchor = page.find('"subtype":"events"')
    if anchor < 0:
        raise ValueError("Events-Block nicht gefunden – Seitenstruktur geändert?")
    start = page.find('"items":[', anchor)
    if start < 0:
        raise ValueError("items-Array nicht gefunden – Seitenstruktur geändert?")
    start += len('"items":')
    # Klammern zählen, Strings respektieren
    depth, i, in_str, esc = 0, start, False, False
    while i < len(page):
        c = page[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
        elif c == "[":
            depth += 1
        elif c == "]":
            depth -= 1
            if depth == 0:
                return json.loads(page[start : i + 1])
        i += 1
    raise ValueError("items-Array nicht abgeschlossen")


def extract_limits(page: str) -> tuple[int | None, int | None]:
    """(total, amount) aus dem Events-Block: Gesamtzahl bei Mainwelle vs. ausgelieferte Maximalzahl."""
    anchor = page.find('"subtype":"events"')
    block = page[anchor : page.find('"items":[', anchor)] if anchor >= 0 else ""
    total = re.search(r'"total":\s*(\d+)', block)
    amount = re.search(r'"amount":\s*"?(\d+)', block)
    return (int(total.group(1)) if total else None, int(amount.group(1)) if amount else None)


# --------------------------------------------------------------------------- parse

def parse_time(raw: str, last: bool = False) -> dt.time | None | str:
    """Erste (bzw. letzte) Uhrzeit aus Freitext wie '19 Uhr', '10.00', 'Sa:17:30-20:00'.

    Gibt '24' zurück für 24:00 (= Mitternacht Folgetag), None wenn nichts parsebar.
    """
    matches = [(int(h), int(m or 0)) for h, m in TIME_RE.findall(raw or "")]
    matches = [(h, m) for h, m in matches if 0 <= h <= 24 and 0 <= m <= 59]
    if not matches:
        return None
    h, m = matches[-1] if last else matches[0]
    if h == 24:
        return "24"
    return dt.time(h, m)


def epoch_to_date(ts) -> dt.date | None:
    """start_date/end_date sind Mitternacht UTC des Kalendertags."""
    if not ts:
        return None
    return dt.datetime.fromtimestamp(int(ts), UTC).date()


def build_event(item: dict) -> dict | None:
    start_d = epoch_to_date(item.get("start_date"))
    if start_d is None:
        return None
    end_d = epoch_to_date(item.get("end_date")) or start_d
    if end_d < start_d:
        end_d = start_d

    raw_start = (item.get("start_time") or "").strip()
    raw_end = (item.get("end_time") or "").strip()
    t_start = parse_time(raw_start)
    t_end = parse_time(raw_end, last=True)

    ev = {"uid": f"mainwelle-{item['post_id']}@mainwelle.de"}

    timed = (
        end_d == start_d
        and isinstance(t_start, dt.time)
        and t_start != dt.time(0, 0)
        and not item.get("whole_day")
    )
    if timed:
        s = dt.datetime.combine(start_d, t_start, TZ)
        if t_end == "24":
            e = dt.datetime.combine(start_d + dt.timedelta(days=1), dt.time(0, 0), TZ)
        elif isinstance(t_end, dt.time):
            e = dt.datetime.combine(start_d, t_end, TZ)
            if e <= s:  # z. B. Ende 4:00 oder 0:00 -> nach Mitternacht
                e += dt.timedelta(days=1)
        else:
            e = s + DEFAULT_DURATION
        ev["start"], ev["end"], ev["all_day"] = s, e, False
    else:
        # Mehrtägig oder ohne Uhrzeit -> ganztägig; DTEND exklusiv
        ev["start"], ev["end"], ev["all_day"] = start_d, end_d + dt.timedelta(days=1), True

    place = clean(item.get("place"))
    address = clean(item.get("address"))
    if place and address:
        ev["location"] = address if place.lower() in address.lower() else f"{place}, {address}"
    else:
        ev["location"] = place or address
    ev["summary"] = clean(item.get("title")) or "(ohne Titel)"

    lines = []
    if any(r and r not in ("00:00", "0:00") for r in (raw_start, raw_end)):
        lines.append("Zeit laut Veranstalter: " + " – ".join(x for x in (raw_start, raw_end) if x))
    price = clean(item.get("ticket_price"))
    if price:
        lines.append("Eintritt: " + price)
    excerpt = clean(item.get("excerpt"))
    if excerpt:
        lines.append(excerpt + ("…" if not excerpt.endswith((".", "!", "?")) else ""))
    ev["description"] = "\n".join(lines)
    ev["url"] = item.get("url") or SOURCE_URL
    return ev


def clean(s) -> str:
    return re.sub(r"\s+", " ", html.unescape(str(s or ""))).strip()


# --------------------------------------------------------------------------- ics

def ics_escape(s: str) -> str:
    return (
        s.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\r\n", "\n").replace("\n", "\\n")
    )


def fold(line: str) -> str:
    """RFC 5545: max. 75 Oktette pro Zeile, Fortsetzung mit Leerzeichen."""
    out, cur = [], b""
    for ch in line:
        b = ch.encode("utf-8")
        limit = 75 if not out else 74
        if len(cur) + len(b) > limit:
            out.append(cur.decode("utf-8"))
            cur = b""
        cur += b
    out.append(cur.decode("utf-8"))
    return "\r\n ".join(out)


def fmt(value, all_day: bool) -> str:
    if all_day:
        return f";VALUE=DATE:{value:%Y%m%d}"
    return ":" + value.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


def to_ics(events: list[dict], now: dt.datetime | None = None) -> str:
    now = now or dt.datetime.now(UTC)
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//mainwelle-ics//Radio Mainwelle Veranstaltungen//DE",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:Radio Mainwelle Veranstaltungen",
        "X-WR-TIMEZONE:Europe/Berlin",
        "REFRESH-INTERVAL;VALUE=DURATION:PT12H",
        "X-PUBLISHED-TTL:PT12H",
    ]
    for ev in sorted(events, key=lambda e: (str(e["start"]), e["summary"])):
        lines += [
            "BEGIN:VEVENT",
            f"UID:{ev['uid']}",
            f"DTSTAMP:{stamp}",
            "DTSTART" + fmt(ev["start"], ev["all_day"]),
            "DTEND" + fmt(ev["end"], ev["all_day"]),
            f"SUMMARY:{ics_escape(ev['summary'])}",
        ]
        if ev["location"]:
            lines.append(f"LOCATION:{ics_escape(ev['location'])}")
        if ev["description"]:
            lines.append(f"DESCRIPTION:{ics_escape(ev['description'])}")
        lines += [f"URL:{ev['url']}", "END:VEVENT"]
    lines.append("END:VCALENDAR")
    return "\r\n".join(fold(l) for l in lines) + "\r\n"


# --------------------------------------------------------------------------- main

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("-o", "--output", default="mainwelle.ics")
    ap.add_argument("--items", help="Items-JSON lokal lesen statt abrufen (Test)")
    ap.add_argument("--min-events", type=int, default=5,
                    help="Abbruch, wenn weniger Events gefunden (schützt vor leerem Feed)")
    args = ap.parse_args(argv)

    if args.items:
        items, total, amount = json.load(open(args.items, encoding="utf-8")), None, None
    else:
        page = fetch_html()
        items = extract_items(page)
        total, amount = extract_limits(page)
    events = [e for e in (build_event(i) for i in items) if e]
    if len(events) < args.min_events:
        print(f"FEHLER: nur {len(events)} Events – Datei wird nicht überschrieben", file=sys.stderr)
        return 1
    with open(args.output, "w", encoding="utf-8", newline="") as f:
        f.write(to_ics(events))
    timed = sum(not e["all_day"] for e in events)
    print(f"{len(events)} Events ({timed} mit Uhrzeit, {len(events) - timed} ganztägig) -> {args.output}")
    print(f"Mainwelle: total={total}, Seitenlimit={amount}, ausgeliefert={len(items)}")

    # Datei ist geschrieben; Exit 3 lässt den Workflow nach dem Commit fehlschlagen -> Mail
    if total is not None and total > len(items):
        print(f"::error::Mainwelle hat {total} Termine, die Seite liefert nur {len(items)} "
              f"(Limit {amount}). Die am weitesten entfernten fehlen im Feed.")
        return 3
    if amount and len(items) >= 0.9 * amount:
        print(f"::warning::{len(items)} von max. {amount} Terminen – Limit bald erreicht.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except urllib.error.HTTPError as e:
        body = e.read()[:300].decode("utf-8", "replace").replace("\n", " ")
        print(f"::error::HTTP {e.code} {e.reason} von {e.url} | Header: "
              f"server={e.headers.get('server')} cf-ray={e.headers.get('cf-ray')} | Body: {body}")
        raise
    except Exception as e:  # noqa: BLE001 – Ursache als Annotation sichtbar machen
        import traceback
        tb = traceback.format_exc().strip().splitlines()
        print(f"::error::{type(e).__name__}: {e} | " + " / ".join(tb[-4:]))
        raise
