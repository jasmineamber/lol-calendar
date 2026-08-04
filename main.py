import os
from collections.abc import Iterable
from datetime import datetime, timedelta, timezone

import requests
from icalendar import Calendar, Event

PANDASCORE_BASE_URL = "https://api.pandascore.co"
OUTPUT_FILENAME = "lck_schedule.ics"

TARGET_LEAGUE_NAMES = {"LPL", "LCK", "KeSPA Cup"}


def get_pandascore_token() -> str:
    token = os.getenv("PANDASCORE_TOKEN")
    if not token:
        raise RuntimeError(
            "Missing PandaScore token. Set the PANDASCORE_TOKEN environment variable."
        )
    return token


def parse_pandascore_datetime(value: str | None) -> datetime | None:
    if not value:
        return None

    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def request_pandascore(path: str, token: str, params: dict | None = None):
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


def fetch_upcoming_lol_matches(token: str) -> list[dict]:
    matches: list[dict] = []
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


def is_target_league_match(match: dict) -> bool:
    league = match.get("league") or {}
    return league.get("name") in TARGET_LEAGUE_NAMES


def opponent_label(opponent_entry: dict) -> str:
    opponent = opponent_entry.get("opponent") or {}
    return opponent.get("acronym") or opponent.get("name") or "TBD"


def match_teams(match: dict) -> list[str]:
    return [opponent_label(entry) for entry in match.get("opponents", [])]


def display_name(value: dict | None) -> str | None:
    if not value:
        return None

    return value.get("full_name") or value.get("name")


def competition_label(match: dict) -> str:
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


def format_label(match: dict) -> str | None:
    games = match.get("number_of_games")
    if not games:
        return None

    return f"BO{games}"


def matchup_label(match: dict) -> str:
    name = match.get("name")
    teams = match_teams(match)
    has_placeholder = any(team == "TBD" for team in teams)

    if name and (has_placeholder or len(teams) < 2):
        return name

    if len(teams) >= 2:
        return " vs ".join(teams)

    return "TBD"


def match_summary(match: dict) -> str:
    matchup = matchup_label(match)
    bo = format_label(match)
    if bo:
        matchup = f"{matchup} ({bo})"

    label = competition_label(match)
    return f"{matchup} [{label}]" if label else matchup


def match_description(match: dict) -> str:
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


def estimated_end(start: datetime, match: dict) -> datetime:
    # games = match.get("number_of_games") or 3
    # if games <= 1:
    #     return start + timedelta(hours=2)
    # if games >= 5:
    #     return start + timedelta(hours=5)
    return start + timedelta(hours=1)


def match_to_event(match: dict) -> Event | None:
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


def matches_to_events(matches: Iterable[dict]) -> list[Event]:
    events = []
    for match in matches:
        if not is_target_league_match(match):
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
    print(f"Wrote {len(events)} target-league LoL matches to {OUTPUT_FILENAME}.")
