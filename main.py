import csv
from datetime import datetime, timedelta
from urllib.parse import quote_plus

import cloudscraper
import pytz
import requests
from bs4 import BeautifulSoup
from icalendar import Calendar, Event


def build_schedule_url(tournament_names: list[str]) -> str:
    encoded_names = ",".join(quote_plus(name) for name in tournament_names)
    return f"https://lol.fandom.com/wiki/Special:RunQuery/MatchCalendarExport?MCE%5B1%5D={encoded_names}&_run="


def get_schedule_csv(url, scraper):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Referer": "https://lol.fandom.com/",
    }
    response = scraper.get(url, headers=headers, timeout=30)
    try:
        response.raise_for_status()
    except requests.exceptions.HTTPError as exc:
        if response.status_code == 403:
            raise RuntimeError(
                "Request blocked by Fandom (403). "
                "cloudscraper could not bypass the protection."
            ) from exc
        raise

    soup = BeautifulSoup(response.text, "html.parser")
    text = soup.get_text()
    start = text.find("Subject,Start Date,Start Time")
    if start == -1:
        raise ValueError("CSV not found in page")
    csv_text = text[start:]

    terminators = [
        "## Additional query",
        "Additional query",
        "Fandom Apps",
        "Explore Properties",
        "Local Sitemap",
    ]
    lowest_end = None
    for terminator in terminators:
        end = csv_text.find(terminator)
        if end != -1 and (lowest_end is None or end < lowest_end):
            lowest_end = end
    if lowest_end is not None:
        csv_text = csv_text[:lowest_end]

    return csv_text.strip()


def get_bo_info(tournament_name: str, scraper) -> str:
    url = f"https://lol.fandom.com/wiki/{tournament_name.replace(' ', '_')}"
    response = scraper.get(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Referer": "https://lol.fandom.com/",
        },
        timeout=30,
    )
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    format_section = soup.find("span", {"id": "Format"})
    if format_section:
        ul = format_section.find_next("ul")
        if ul:
            text = ul.get_text().lower()
            if "best of three" in text:
                return "BO3"
            elif "best of five" in text:
                return "BO5"
    return "BO3"  # default


def parse_csv_to_events(csv_text, bo_dict):
    lines = [line for line in csv_text.splitlines() if line.strip()]
    reader = csv.reader(lines)
    header = next(reader, None)
    if header is None:
        raise ValueError("CSV text is empty")

    events = []

    for row in reader:
        if not row:
            continue
        if len(row) == 1 and row[0].strip() in {
            "Additional query",
            "Fandom Apps",
            "Explore Properties",
            "Local Sitemap",
        }:
            break

        subject = row[0]

        if len(row) == 3:
            date_str, time_str = row[1], row[2]
            dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
            dt = dt.replace(hour=(dt.hour - 1) % 24)
            dt = pytz.utc.localize(dt)
            dt = dt + timedelta(hours=1)
            end_dt = dt + timedelta(hours=1)
            end_dt = pytz.utc.localize(end_dt)
            end_dt = end_dt + timedelta(hours=1)
        elif len(row) >= 11 and all(x.isdigit() for x in row[1:6]):
            start_year, start_month, start_day, start_hour, start_minute = (
                int(row[1]),
                int(row[2]),
                int(row[3]),
                (int(row[4]) - 1) % 24,
                int(row[5]),
            )
            end_year, end_month, end_day, end_hour, end_minute = (
                int(row[6]),
                int(row[7]),
                int(row[8]),
                (int(row[9]) - 1) % 24,
                int(row[10]),
            )
            dt = datetime(start_year, start_month, start_day, start_hour, start_minute)
            end_dt = datetime(end_year, end_month, end_day, end_hour, end_minute)
            dt = pytz.utc.localize(dt)
            dt = dt + timedelta(hours=1)
            end_dt = pytz.utc.localize(end_dt)
            end_dt = end_dt + timedelta(hours=1)
        elif len(row) >= 6 and all(x.isdigit() for x in row[1:6]):
            start_year, start_month, start_day, start_hour, start_minute = (
                int(row[1]),
                int(row[2]),
                int(row[3]),
                (int(row[4]) - 1) % 24,
                int(row[5]),
            )
            dt = datetime(start_year, start_month, start_day, start_hour, start_minute)
            dt = pytz.utc.localize(dt)
            dt = dt + timedelta(hours=1)
            end_dt = dt + timedelta(hours=1)
            end_dt = pytz.utc.localize(end_dt)
            end_dt = end_dt + timedelta(hours=1)
        else:
            continue

        # Parse subject to reformat summary
        parts = subject.split(" - ")
        if len(parts) == 2:
            league = parts[0]
            teams = parts[1]
            bo = bo_dict.get(league, "BO3")
            summary = f"{teams} ({bo}) [{league}]"
        else:
            summary = subject

        event = Event()
        event.add("summary", summary)
        event.add("dtstart", dt)
        if end_dt != dt:
            event.add("dtend", end_dt)
        event.add("description", parts[0] if len(parts) == 2 else subject)
        event.add("dtstamp", dt)
        event.add("created", dt)
        event.add("last-modified", dt)
        event.add("status", "CONFIRMED")
        event.add("transp", "OPAQUE")
        event.add("sequence", 0)
        event.add("uid", f"{dt.strftime('%Y%m%dT%H%M%SZ')}@example.com")
        events.append(event)

    return events


def generate_ics(events, filename):
    cal = Calendar()
    cal.add("prodid", "-//My calendar//example.com//")
    cal.add("version", "2.0")
    cal.add("calscale", "GREGORIAN")
    cal.add("method", "PUBLISH")
    for event in events:
        cal.add_component(event)
    with open(filename, "wb") as f:
        f.write(cal.to_ical())


if __name__ == "__main__":
    tournaments = ["LCK/2026 Season/Rounds 1-2", "LPL/2026 Season/Split 2"]
    scraper = cloudscraper.create_scraper(
        browser={"browser": "chrome", "platform": "windows", "mobile": False}
    )
    scraper.get(
        "https://lol.fandom.com/",
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Referer": "https://lol.fandom.com/",
        },
        timeout=30,
    )
    bo_dict = {}
    for t in tournaments:
        bo_dict[t] = get_bo_info(t, scraper)
    url = build_schedule_url(tournaments)
    csv_text = get_schedule_csv(url, scraper)
    # Save CSV for manual verification
    with open("schedule.csv", "w", encoding="utf-8") as f:
        f.write(csv_text)
    events = parse_csv_to_events(csv_text, bo_dict)
    generate_ics(events, "lck_schedule.ics")
