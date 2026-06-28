import os
import re
from datetime import datetime, timedelta, timezone
from typing import Dict, Iterable, List, Optional

import requests
from icalendar import Calendar, Event

PANDASCORE_BASE_URL = "https://api.pandascore.co"
OUTPUT_FILENAME = "lck_schedule.ics"

# Keep the calendar focused on China and Korea. PandaScore does not expose a
# single region filter on matches, so we filter the competition metadata.
REGION_KEYWORDS = (
    "china",
    "chinese",
    "demacia",
    "nest",
    "korea",
    "korean",
    "challengers korea",
    "league-of-legends-mid-invitational",
)
REGION_PATTERNS = (
    re.compile(r"(?<![a-z0-9])lpl(?![a-z0-9])"),
    re.compile(r"(?<![a-z0-9])ldl(?![a-z0-9])"),
    re.compile(r"(?<![a-z0-9])lck(?![a-z0-9])"),
    re.compile(r"(?<![a-z0-9])lck cl(?![a-z0-9])"),
)


def get_pandascore_token() -> str:
    token = os.getenv("PANDASCORE_TOKEN")
    if not token:
        raise RuntimeError(
            "Missing PandaScore token. Set the PANDASCORE_TOKEN environment variable."
        )
    return token


def parse_pandascore_datetime(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None

    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def request_pandascore(path: str, token: str, params: Optional[Dict] = None):
    response = requests.get(
        f"{PANDASCORE_BASE_URL}{path}",
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
        },
        params=params,
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def fetch_upcoming_lol_matches(token: str) -> List[Dict]:
    matches: List[Dict] = []
    page = 1
    per_page = 100

    while True:
        batch = request_pandascore(
            "/lol/matches/upcoming",
            token,
            params={
                "page": page,
                "per_page": per_page,
                "sort": "scheduled_at",
            },
        )
        if not batch:
            break

        matches.extend(batch)
        if len(batch) < per_page:
            break

        page += 1

    return matches


def nested_name(match: Dict, key: str) -> str:
    value = match.get(key) or {}
    return " ".join(
        str(value.get(field) or "")
        for field in ("name", "full_name", "slug")
        if value.get(field)
    )


def competition_text(match: Dict) -> str:
    return " ".join(
        part
        for part in (
            nested_name(match, "league"),
            nested_name(match, "serie"),
            nested_name(match, "tournament"),
        )
        if part
    ).lower()


def is_china_or_korea_match(match: Dict) -> bool:
    text = competition_text(match)
    return any(keyword in text for keyword in REGION_KEYWORDS) or any(
        pattern.search(text) for pattern in REGION_PATTERNS
    )


def opponent_label(opponent_entry: Dict) -> str:
    opponent = opponent_entry.get("opponent") or {}
    return opponent.get("acronym") or opponent.get("name") or "TBD"


def match_teams(match: Dict) -> List[str]:
    return [opponent_label(entry) for entry in match.get("opponents", [])]


def display_name(value: Optional[Dict]) -> Optional[str]:
    if not value:
        return None

    return value.get("full_name") or value.get("name")


def competition_label(match: Dict) -> str:
    label_parts = []
    seen = set()

    for key in ("league", "serie", "tournament"):
        name = display_name(match.get(key))
        if not name:
            continue

        normalized = name.lower()
        if normalized in seen:
            continue

        label_parts.append(name)
        seen.add(normalized)

    return " ".join(label_parts)


def format_label(match: Dict) -> Optional[str]:
    games = match.get("number_of_games")
    if not games:
        return None

    return f"BO{games}"


def matchup_label(match: Dict) -> str:
    name = match.get("name")
    teams = match_teams(match)
    has_placeholder = any(team == "TBD" for team in teams)

    if name and (has_placeholder or len(teams) < 2):
        return name

    if len(teams) >= 2:
        return " vs ".join(teams)

    return "TBD"


def match_summary(match: Dict) -> str:
    matchup = matchup_label(match)
    bo = format_label(match)
    if bo:
        matchup = f"{matchup} ({bo})"

    label = competition_label(match)
    return f"{matchup} [{label}]" if label else matchup


def match_description(match: Dict) -> str:
    lines = []

    for label, key in (
        ("League", "league"),
        ("Serie", "serie"),
        ("Tournament", "tournament"),
    ):
        name = display_name(match.get(key))
        if name:
            lines.append(f"{label}: {name}")

    bo = format_label(match)
    if bo:
        lines.append(f"Format: {bo}")
    if match.get("status"):
        lines.append(f"Status: {match['status']}")
    if match.get("rescheduled"):
        lines.append("Rescheduled: yes")

    streams = [
        stream.get("raw_url")
        for stream in match.get("streams_list", [])
        if stream.get("raw_url")
    ]
    if match.get("official_stream_url"):
        streams.insert(0, match["official_stream_url"])
    if streams:
        lines.append(f"Stream: {streams[0]}")

    return "\n".join(lines)


def estimated_end(start: datetime, match: Dict) -> datetime:
    # games = match.get("number_of_games") or 3
    # if games <= 1:
    #     return start + timedelta(hours=2)
    # if games >= 5:
    #     return start + timedelta(hours=5)
    return start + timedelta(hours=1)


def match_to_event(match: Dict) -> Optional[Event]:
    start = parse_pandascore_datetime(
        match.get("begin_at") or match.get("scheduled_at")
    )
    if not start:
        return None

    if start.year != datetime.now(timezone.utc).year:
        return None

    end = parse_pandascore_datetime(match.get("end_at")) or estimated_end(start, match)
    now = datetime.now(timezone.utc)

    event = Event()
    event.add("summary", match_summary(match))
    event.add("dtstart", start)
    event.add("dtend", end)
    event.add("description", match_description(match))
    event.add("dtstamp", now)
    event.add("created", now)
    event.add("last-modified", now)
    event.add("status", "CONFIRMED")
    event.add("transp", "OPAQUE")
    event.add("sequence", 0)
    event.add("uid", f"pandascore-lol-match-{match['id']}@lol-calendar")
    return event


def matches_to_events(matches: Iterable[Dict]) -> List[Event]:
    events = []
    for match in matches:
        if not is_china_or_korea_match(match):
            continue

        event = match_to_event(match)
        if event:
            events.append(event)

    return events


def generate_ics(events: Iterable[Event], filename: str) -> None:
    cal = Calendar()
    cal.add("prodid", "-//LoL China Korea calendar//lol-calendar//")
    cal.add("version", "2.0")
    cal.add("calscale", "GREGORIAN")
    cal.add("method", "PUBLISH")

    for event in events:
        cal.add_component(event)

    with open(filename, "wb") as f:
        f.write(cal.to_ical())


if __name__ == "__main__":
    token = get_pandascore_token()
    all_matches = fetch_upcoming_lol_matches(token)
    events = matches_to_events(all_matches)
    generate_ics(events, OUTPUT_FILENAME)
    print(f"Wrote {len(events)} China/Korea LoL matches to {OUTPUT_FILENAME}.")
