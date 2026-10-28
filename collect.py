"""
Grocery Society event collector.

What it does, once per run:
  1. Reads the list of websites in sources.csv.
  2. Checks each site's rules for bots (robots.txt). If bots aren't allowed, it skips that site.
  3. Downloads the page, slowly and politely.
  4. Looks for the tidy event details many sites include for Google (called "schema.org" data).
  5. If there are none, asks Claude to read the page text and pull out the events.
  6. Cleans up dates, works out "On now", "Opening soon", "Closing soon" or "Ended",
     removes duplicates, and drops events that have ended.
  7. Saves everything to output/events.csv (open it in Google Sheets or Excel)
     and writes a short report to output/run_log.csv.

Nothing is published automatically. Every row has a "needs_review" column so you can check it first.

Run it:
  python collect.py                      (normal run, visits the real websites)
  python collect.py --sources tests/practice_sources.csv --offline tests/pages --today 2026-10-28
      (practice run on made-up gallery pages, no internet needed)
"""

import argparse
import csv
import json
import os
import re
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib import robotparser
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

# ---------- Settings you may want to change ----------

CONTACT_EMAIL = os.environ.get("GS_CONTACT_EMAIL", "[YOUR EMAIL]")
USER_AGENT = f"GrocerySocietyBot/0.1 (+mailto:{CONTACT_EMAIL})"
SECONDS_BETWEEN_SITES = 5          # be gentle with small gallery websites
SOON_DAYS = 7                      # "opening soon" / "closing soon" window
MAX_PAGE_CHARACTERS = 40000        # how much page text to send to Claude
CLAUDE_MODEL = "claude-haiku-5-5"  # fast and cheap; good at pulling facts out of text

HERE = Path(__file__).parent
SOURCES_FILE = HERE / "sources.csv"
OUTPUT_DIR = HERE / "output"

EVENT_TYPES = {
    "event", "exhibitionevent", "visualartsevent", "educationevent", "socialevent",
    "festival", "screeningevent", "literaryevent", "businessevent", "courseinstance",
}

COLUMNS = [
    "status", "needs_review", "title", "start_date", "end_date", "start_time", "end_time",
    "venue", "address", "city", "area", "price", "kind", "event_url",
    "source_name", "source_url", "found_by", "notes", "collected_on",
]


# ---------- Step 1: read the list of sources ----------

def load_sources(path=SOURCES_FILE):
    with open(path, newline="", encoding="utf-8") as f:
        return [row for row in csv.DictReader(f) if row.get("url")]


# ---------- Step 2 and 3: polite downloading ----------

def allowed_by_robots(url, session):
    """Return (allowed, note). If robots.txt can't be read, we treat the site as allowed."""
    parts = urlparse(url)
    robots_url = f"{parts.scheme}://{parts.netloc}/robots.txt"
    parser = robotparser.RobotFileParser()
    try:
        response = session.get(robots_url, timeout=15)
        if response.status_code >= 400:
            return True, "no robots.txt"
        parser.parse(response.text.splitlines())
    except requests.RequestException:
        return True, "robots.txt unreachable"
    if parser.can_fetch(USER_AGENT, url):
        return True, "robots.txt allows"
    return False, "robots.txt says no bots: ask the gallery for their calendar instead"


def download(url, session):
    response = session.get(url, timeout=30)
    response.raise_for_status()
    return response.text


# ---------- Step 4: read the tidy event details (schema.org) ----------

def walk_json(node):
    """Yield every dictionary inside a JSON structure."""
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from walk_json(value)
    elif isinstance(node, list):
        for item in node:
            yield from walk_json(item)


def is_event(obj):
    kinds = obj.get("@type", [])
    if isinstance(kinds, str):
        kinds = [kinds]
    return any(str(k).lower() in EVENT_TYPES for k in kinds)


def text_of(value):
    if isinstance(value, dict):
        return value.get("name") or value.get("streetAddress") or ""
    if isinstance(value, list):
        return ", ".join(text_of(v) for v in value if v)
    return str(value or "").strip()


def address_of(location):
    if isinstance(location, list):
        location = location[0] if location else {}
    if not isinstance(location, dict):
        return "", ""
    venue = location.get("name", "")
    address = location.get("address", "")
    if isinstance(address, dict):
        address = ", ".join(
            str(address.get(k, "")) for k in
            ("streetAddress", "addressLocality", "addressRegion", "postalCode")
            if address.get(k)
        )
    return str(venue).strip(), str(address).strip()


def price_of(offers):
    if isinstance(offers, list):
        offers = offers[0] if offers else {}
    if not isinstance(offers, dict):
        return ""
    price = offers.get("price")
    if price in (None, ""):
        return ""
    if str(price) in ("0", "0.0", "0.00"):
        return "Free"
    currency = offers.get("priceCurrency", "")
    return f"{currency} {price}".strip()


def from_structured_data(html, page_url):
    soup = BeautifulSoup(html, "html.parser")
    events = []
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        for obj in walk_json(data):
            if not is_event(obj):
                continue
            venue, address = address_of(obj.get("location"))
            start = str(obj.get("startDate", ""))
            end = str(obj.get("endDate", ""))
            events.append({
                "title": text_of(obj.get("name")),
                "start_date": start[:10],
                "end_date": end[:10],
                "start_time": start[11:16] if len(start) > 10 else "",
                "end_time": end[11:16] if len(end) > 10 else "",
                "venue": venue,
                "address": address,
                "price": price_of(obj.get("offers")),
                "kind": "",
                "event_url": urljoin(page_url, str(obj.get("url") or page_url)),
                "notes": "",
            })
    return events


# ---------- Step 5: ask Claude to read the page ----------

PROMPT = """You are reading the text of an art gallery or design venue web page.
List every exhibition or event described on it.

Today's date is {today}. The page is from {source_name} in {city}. Page address: {url}

Rules:
- Only use facts written on the page. Never guess. If something isn't stated, use null.
- Dates as YYYY-MM-DD. If the year is missing but obvious from context, use it and say so in "notes".
- Times as 24-hour HH:MM.
- "venue": where the event actually takes place. Galleries sometimes list shows held elsewhere
  (another gallery, a museum, an art fair). Use that other venue when the page says so.
- Include past events only if they have an end date; leave filtering to us.
- "kind": one of exhibition, opening, workshop, talk, screening, fair, open_call, other.
- "notes": anything uncertain, in a few words. Otherwise "".

Reply with JSON only, no other text, in exactly this shape:
[{{"title": "...", "start_date": "...", "end_date": "...", "start_time": null, "end_time": null,
  "venue": "...", "address": null, "price": null, "kind": "exhibition",
  "event_url": "...", "notes": ""}}]
If there are no events, reply with [].

PAGE TEXT:
{text}
"""


def page_text(html):
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg", "header", "footer", "nav", "form"]):
        tag.decompose()
    lines = [line.strip() for line in soup.get_text("\n").splitlines()]
    return "\n".join(line for line in lines if line)[:MAX_PAGE_CHARACTERS]


def parse_json_list(reply):
    start, end = reply.find("["), reply.rfind("]")
    if start == -1 or end == -1:
        return []
    try:
        data = json.loads(reply[start:end + 1])
    except json.JSONDecodeError:
        return []
    return [item for item in data if isinstance(item, dict)]


def from_claude(html, source, page_url):
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None  # no key set: skip this step and say so in the log
    import anthropic
    client = anthropic.Anthropic()
    prompt = PROMPT.format(
        today=date.today().isoformat(),
        source_name=source["name"],
        city=source["city"],
        url=page_url,
        text=page_text(html),
    )
    message = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=4000,
        messages=[{"role": "user", "content": prompt}],
    )
    reply = "".join(block.text for block in message.content if block.type == "text")
    events = []
    for item in parse_json_list(reply):
        events.append({
            "title": str(item.get("title") or "").strip(),
            "start_date": str(item.get("start_date") or ""),
            "end_date": str(item.get("end_date") or ""),
            "start_time": str(item.get("start_time") or ""),
            "end_time": str(item.get("end_time") or ""),
            "venue": str(item.get("venue") or ""),
            "address": str(item.get("address") or ""),
            "price": str(item.get("price") or ""),
            "kind": str(item.get("kind") or ""),
            "event_url": urljoin(page_url, str(item.get("event_url") or page_url)),
            "notes": str(item.get("notes") or ""),
        })
    return events


# ---------- Step 6: clean up, work out status, remove duplicates ----------

def to_date(value):
    try:
        return datetime.strptime(value[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def work_out_status(start, end, today):
    """The label always comes from the dates, never from the gallery's wording."""
    if not start and not end:
        return "check dates"
    end = end or start
    start = start or end
    if end < today:
        return "ended"
    if start > today:
        return "opening soon" if (start - today).days <= SOON_DAYS else "upcoming"
    if (end - today).days <= SOON_DAYS:
        return "closing soon"
    return "on now"


def simplify(text):
    text = (text or "").lower().replace("&", " and ")
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def finish(events, source, found_by, today):
    rows = []
    for event in events:
        if not event.get("title"):
            continue
        start, end = to_date(event["start_date"]), to_date(event["end_date"])
        status = work_out_status(start, end, today)
        if status == "ended":
            continue
        venue = event.get("venue") or source["default_venue"]
        missing = [name for name, value in
                   (("start date", start), ("end date", end), ("venue", event.get("venue")))
                   if not value]
        notes = event.get("notes", "")
        if missing:
            notes = (notes + "; " if notes else "") + "missing: " + ", ".join(missing)
        rows.append({
            "status": status,
            "needs_review": "yes" if (found_by == "claude" or missing) else "no",
            "title": event["title"],
            "start_date": start.isoformat() if start else "",
            "end_date": end.isoformat() if end else "",
            "start_time": event.get("start_time", ""),
            "end_time": event.get("end_time", ""),
            "venue": venue,
            "address": event.get("address", ""),
            "city": source["city"],
            "area": source["area"],
            "price": event.get("price", ""),
            "kind": event.get("kind", ""),
            "event_url": event.get("event_url", ""),
            "source_name": source["name"],
            "source_url": source["url"],
            "found_by": found_by,
            "notes": notes,
            "collected_on": today.isoformat(),
        })
    return rows


def remove_duplicates(rows):
    """Same title and same start date = the same event. Keep one, remember every source."""
    kept = {}
    for row in rows:
        key = (simplify(row["title"]), row["start_date"])
        if key in kept:
            first = kept[key]
            if row["source_name"] not in first["source_name"]:
                first["source_name"] += " | " + row["source_name"]
                first["notes"] = (first["notes"] + "; " if first["notes"] else "") + "also listed elsewhere"
        else:
            kept[key] = row
    order = {"on now": 0, "closing soon": 1, "opening soon": 2, "upcoming": 3, "check dates": 4}
    return sorted(kept.values(), key=lambda r: (order.get(r["status"], 9), r["start_date"] or "9999"))


# ---------- Step 7: save the results ----------

def save(rows, log):
    OUTPUT_DIR.mkdir(exist_ok=True)
    with open(OUTPUT_DIR / "events.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    with open(OUTPUT_DIR / "run_log.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["source_name", "result", "events_found", "note"])
        writer.writeheader()
        writer.writerows(log)


# ---------- Putting it together ----------

def offline_page(folder, source):
    """For practice runs: tests/pages/<source name in lower case, spaces as dashes>.html"""
    path = Path(folder) / (simplify(source["name"]).replace(" ", "-") + ".html")
    return path.read_text(encoding="utf-8") if path.exists() else None


def run(offline_folder=None, today=None, sources_file=SOURCES_FILE):
    today = today or date.today()
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    all_rows, log = [], []

    for number, source in enumerate(load_sources(sources_file)):
        name, url = source["name"], source["url"]
        try:
            if offline_folder:
                html = offline_page(offline_folder, source)
                if html is None:
                    log.append({"source_name": name, "result": "skipped", "events_found": 0,
                                "note": "no saved page for practice run"})
                    continue
            else:
                if number:
                    time.sleep(SECONDS_BETWEEN_SITES)
                allowed, robots_note = allowed_by_robots(url, session)
                if not allowed:
                    log.append({"source_name": name, "result": "skipped", "events_found": 0, "note": robots_note})
                    continue
                html = download(url, session)

            events, found_by = from_structured_data(html, url), "structured data"
            if not events:
                events, found_by = from_claude(html, source, url), "claude"
                if events is None:
                    log.append({"source_name": name, "result": "needs AI", "events_found": 0,
                                "note": "no tidy event data; set ANTHROPIC_API_KEY to read this page"})
                    continue

            rows = finish(events, source, found_by, today)
            all_rows.extend(rows)
            log.append({"source_name": name, "result": "ok", "events_found": len(rows), "note": found_by})
        except requests.RequestException as error:
            log.append({"source_name": name, "result": "error", "events_found": 0,
                        "note": f"could not download: {type(error).__name__}"})
        except Exception as error:  # keep going: one broken site should never stop the rest
            log.append({"source_name": name, "result": "error", "events_found": 0,
                        "note": f"{type(error).__name__}: {error}"[:200]})

    rows = remove_duplicates(all_rows)
    save(rows, log)
    print(f"Saved {len(rows)} events to output/events.csv")
    for entry in log:
        print(f"  {entry['source_name']}: {entry['result']} ({entry['events_found']}) {entry['note']}")
    return rows, log


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Collect art and design events.")
    parser.add_argument("--offline", metavar="FOLDER", help="practice run on saved pages in FOLDER")
    parser.add_argument("--today", metavar="YYYY-MM-DD", help="pretend today is this date (for testing)")
    parser.add_argument("--sources", metavar="FILE", default=str(SOURCES_FILE), help="list of websites (default: sources.csv)")
    args = parser.parse_args()
    pretend = to_date(args.today) if args.today else None
    run(offline_folder=args.offline, today=pretend, sources_file=args.sources)
    sys.exit(0)
