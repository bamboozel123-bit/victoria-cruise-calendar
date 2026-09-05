import re
import html
import hashlib
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup


GVHA_URL = "https://gvha.ca/cruise-landing-page/cruise-schedule/current-season/"
OUTPUT_FILE = "docs/cruise-calendar.ics"

TIMEZONE = ZoneInfo("America/Vancouver")


def clean_text(text):
    text = html.unescape(text)
    return " ".join(text.split()).strip()


def escape_ics(text):
    text = str(text)
    text = text.replace("\\", "\\\\")
    text = text.replace(";", "\\;")
    text = text.replace(",", "\\,")
    text = text.replace("\n", "\\n")
    return text


def parse_time(value):
    """
    Extract HH:MM from values such as:
    19:00
    ETA-19:00
    ETD-23:30
    """
    match = re.search(r"(\d{1,2}):(\d{2})", value or "")
    if not match:
        return None

    return f"{int(match.group(1)):02d}:{match.group(2)}"


def parse_date(value, current_year):
    """
    Convert strings such as:
    Sat, Sep 05
    into a date.
    """
    value = clean_text(value)

    match = re.search(r"([A-Z][a-z]{2}),?\s+([A-Z][a-z]{2})\s+(\d{1,2})", value)

    if not match:
        return None

    month = match.group(2)
    day = int(match.group(3))

    return datetime.strptime(
        f"{current_year} {month} {day}",
        "%Y %b %d"
    ).date()


def make_uid(date_string, vessel):
    raw = f"{date_string}|{vessel}".encode("utf-8")
    digest = hashlib.sha256(raw).hexdigest()[:20]
    return f"{digest}@victoria-cruise-calendar"


def main():
    print("Downloading GVHA cruise schedule...")

    response = requests.get(
        GVHA_URL,
        timeout=30,
        headers={
            "User-Agent": "Victoria Cruise Calendar/1.0"
        },
    )
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")

    tables = soup.find_all("table")

    if not tables:
        raise RuntimeError("Could not find the cruise schedule table.")

    # Find the table containing the expected cruise columns.
    schedule_table = None

    for table in tables:
        headers = [
            clean_text(th.get_text(" ", strip=True)).lower()
            for th in table.find_all("th")
        ]

        header_text = " ".join(headers)

        if "vessel" in header_text and "eta" in header_text:
            schedule_table = table
            break

    if schedule_table is None:
        raise RuntimeError("Could not identify the GVHA cruise schedule table.")

    rows = schedule_table.find_all("tr")

    events = []
    current_year = datetime.now(TIMEZONE).year

    for row in rows:
        cells = row.find_all(["td", "th"])

        if len(cells) < 6:
            continue

        values = [
            clean_text(cell.get_text(" ", strip=True))
            for cell in cells
        ]

        # Skip header rows.
        if values[0].lower() == "date":
            continue

        date_text = values[0]
        vessel = values[1]
        eta_text = values[2]
        etd_text = values[4]
        passengers = values[5]
        berth = values[6] if len(values) > 6 else ""

        # The final cell normally contains:
        # Cruise Line | To: destination | From: origin | Length: ...
        details = values[7] if len(values) > 7 else ""

        # Ignore rows without a real vessel/date.
        if not vessel or vessel.lower() == "vessel":
            continue

        cruise_date = parse_date(date_text, current_year)

        if not cruise_date:
            continue

        eta = parse_time(eta_text)
        etd = parse_time(etd_text)

        if not eta or not etd:
            continue

        # Ignore explicitly cancelled calls.
        row_text = " ".join(values).lower()

        if "cancelled" in row_text:
            continue

        cruise_line = ""
        destination = ""
        origin = ""

        parts = [clean_text(x) for x in details.split("|")]

        for part in parts:
            lower = part.lower()

            if lower.startswith("to:"):
                destination = part[3:].strip()

            elif lower.startswith("from:"):
                origin = part[5:].strip()

            elif not lower.startswith("length:"):
                cruise_line = part

        start_local = datetime.strptime(
            f"{cruise_date} {eta}",
            "%Y-%m-%d %H:%M"
        ).replace(tzinfo=TIMEZONE)

        end_local = datetime.strptime(
            f"{cruise_date} {etd}",
            "%Y-%m-%d %H:%M"
        ).replace(tzinfo=TIMEZONE)

        # Some ships can theoretically depart after midnight.
        if end_local <= start_local:
            from datetime import timedelta
            end_local += timedelta(days=1)

        uid = make_uid(str(cruise_date), vessel)

        description = (
            f"Vessel: {vessel}\\n"
            f"Cruise line: {cruise_line}\\n"
            f"Passengers: {passengers}\\n"
            f"Berth: {berth}\\n"
            f"From: {origin}\\n"
            f"To: {destination}\\n"
            f"Source: {GVHA_URL}"
        )

        events.append({
            "uid": uid,
            "vessel": vessel,
            "start": start_local,
            "end": end_local,
            "description": description,
            "berth": berth,
        })

    if not events:
        raise RuntimeError("No cruise events were found.")

    print(f"Found {len(events)} cruise calls.")

    now = datetime.now(TIMEZONE).strftime("%Y%m%dT%H%M%S")

    calendar = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Victoria Cruise Calendar//GVHA//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:Victoria Cruise Ships",
        "X-WR-CALDESC:Victoria cruise ship arrivals and departures from GVHA",
        "X-WR-TIMEZONE:America/Vancouver",
    ]

    for event in events:
        start_utc = event["start"].astimezone(ZoneInfo("UTC"))
        end_utc = event["end"].astimezone(ZoneInfo("UTC"))

        calendar.extend([
            "BEGIN:VEVENT",
            f"UID:{event['uid']}",
            f"DTSTAMP:{now}Z",
            f"DTSTART:{start_utc.strftime('%Y%m%dT%H%M%SZ')}",
            f"DTEND:{end_utc.strftime('%Y%m%dT%H%M%SZ')}",
            f"SUMMARY:🚢 {escape_ics(event['vessel'])}",
            f"LOCATION:Ogden Point, Victoria, BC",
            f"DESCRIPTION:{escape_ics(event['description'])}",
            "STATUS:CONFIRMED",
            "END:VEVENT",
        ])

    calendar.append("END:VCALENDAR")

    output = "\r\n".join(calendar) + "\r\n"

    import os

    os.makedirs("docs", exist_ok=True)

    with open(OUTPUT_FILE, "w", encoding="utf-8", newline="") as file:
        file.write(output)

    print(f"Calendar written to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
