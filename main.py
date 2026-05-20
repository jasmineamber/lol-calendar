import csv
import os
import platform
import shutil
import socket
import subprocess
import tempfile
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import List, Optional
from urllib.parse import quote_plus
from urllib.request import urlretrieve

import pytz
from bs4 import BeautifulSoup
from DrissionPage import ChromiumOptions, ChromiumPage
from icalendar import Calendar, Event


USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
BROWSER_PATHS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    "/usr/bin/google-chrome",
    "/usr/bin/google-chrome-stable",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
]
BROWSER_EXECUTABLES = (
    "google-chrome",
    "google-chrome-stable",
    "chromium",
    "chromium-browser",
    "chrome",
    "chrome.exe",
    "msedge",
    "msedge.exe",
)


def build_schedule_url(tournament_names: List[str]) -> str:
    encoded_names = ",".join(quote_plus(name) for name in tournament_names)
    return f"https://lol.fandom.com/wiki/Special:RunQuery/MatchCalendarExport?MCE%5B1%5D={encoded_names}&_run="


def find_chromium_browser() -> Optional[str]:
    for executable in BROWSER_EXECUTABLES:
        browser_path = shutil.which(executable)
        if browser_path:
            return browser_path

    for browser_path in BROWSER_PATHS:
        if Path(browser_path).exists():
            return browser_path

    return None


def run_install_command(command: List[str]) -> None:
    if platform.system() == "Linux" and os.geteuid() != 0 and shutil.which("sudo"):
        command = ["sudo"] + command
    subprocess.run(command, check=True)


def install_chromium_browser() -> Optional[str]:
    if platform.system() != "Linux":
        return None

    if shutil.which("apt-get"):
        run_install_command(["apt-get", "update"])
        with tempfile.NamedTemporaryFile(suffix=".deb", delete=False) as deb_file:
            deb_path = deb_file.name
        try:
            urlretrieve(
                "https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb",
                deb_path,
            )
            run_install_command(["apt-get", "install", "-y", deb_path])
        finally:
            Path(deb_path).unlink(missing_ok=True)
    elif shutil.which("dnf"):
        run_install_command(
            ["dnf", "install", "-y", "https://dl.google.com/linux/direct/google-chrome-stable_current_x86_64.rpm"]
        )
    elif shutil.which("yum"):
        run_install_command(
            ["yum", "install", "-y", "https://dl.google.com/linux/direct/google-chrome-stable_current_x86_64.rpm"]
        )
    elif shutil.which("pacman"):
        run_install_command(["pacman", "-Sy", "--noconfirm", "chromium"])
    elif shutil.which("zypper"):
        run_install_command(["zypper", "install", "-y", "chromium"])
    elif shutil.which("apk"):
        run_install_command(["apk", "add", "chromium"])

    return find_chromium_browser()


def ensure_chromium_browser() -> str:
    browser_path = find_chromium_browser()
    if browser_path:
        return browser_path

    browser_path = install_chromium_browser()
    if browser_path:
        return browser_path

    raise RuntimeError(
        "No Chrome/Chromium/Edge browser found, and automatic installation failed."
    )


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def build_chromium_options(user_data_path: str) -> ChromiumOptions:
    options = ChromiumOptions()
    options.set_browser_path(ensure_chromium_browser())
    options.headless(True)
    options.set_local_port(find_free_port())
    options.set_user_data_path(user_data_path)
    options.set_user_agent(USER_AGENT)
    options.set_argument("--disable-blink-features", "AutomationControlled")
    options.set_argument("--disable-gpu")
    options.set_argument("--no-sandbox")
    return options


def drission_get(url: str, timeout: int = 30) -> str:
    with tempfile.TemporaryDirectory(prefix="lol-calendar-drission-") as user_data_dir:
        page = ChromiumPage(build_chromium_options(user_data_dir))
        try:
            page.run_cdp("Emulation.setTimezoneOverride", timezoneId="UTC")
            page.get(url, timeout=timeout)
            return page.html
        finally:
            page.quit()


def get_schedule_csv(url):
    page_text = drission_get(url, timeout=30)
    soup = BeautifulSoup(page_text, "html.parser")
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


def get_bo_info(tournament_name: str) -> str:
    url = f"https://lol.fandom.com/wiki/{tournament_name.replace(' ', '_')}"
    page_text = drission_get(url, timeout=30)
    soup = BeautifulSoup(page_text, "html.parser")
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
    yesterday = (datetime.now(pytz.utc) - timedelta(days=1)).date()
    yesterday_midnight = datetime.combine(yesterday, time.min, tzinfo=pytz.utc)
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
            if dt < yesterday_midnight:
                continue
            end_dt = dt + timedelta(minutes=60)
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
            if dt < yesterday_midnight:
                continue
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
            if dt < yesterday_midnight:
                continue
            end_dt = dt + timedelta(minutes=40)
        else:
            continue

        # Parse subject to reformat summary
        parts = subject.split(" - ")
        if len(parts) == 2:
            league = parts[0]
            teams = parts[1]
            # bo = bo_dict.get(league, "BO3")
            # summary = f"{teams} ({bo}) [{league}]"
            summary = f"{teams} [{league}]"
        else:
            summary = subject

        if end_dt == dt:
            end_dt = dt + timedelta(minutes=40)

        event = Event()
        event.add("summary", summary)
        event.add("dtstart", dt)
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
    tournaments = [
        "LCK/2026 Season/Rounds 1-2",
        "LPL/2026 Season/Split 2",
        "Esports World Cup 2026/Online Qualifiers/Korea",
    ]
    bo_dict = {}
    # for t in tournaments:
    #     bo_dict[t] = get_bo_info(t)
    url = build_schedule_url(tournaments)
    csv_text = get_schedule_csv(url)
    # Save CSV for manual verification
    with open("schedule.csv", "w", encoding="utf-8") as f:
        f.write(csv_text)
    events = parse_csv_to_events(csv_text, bo_dict)
    generate_ics(events, "lck_schedule.ics")
