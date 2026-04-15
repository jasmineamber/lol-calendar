# LCK Calendar Generator

This project automatically fetches the LCK (League of Legends Champions Korea) match schedule from the Fandom wiki and generates an ICS (iCalendar) file that can be subscribed to in calendar applications.

## How it works

* Uses the MatchCalendarExport special page from lol.fandom.com to get schedule data in CSV format.
* Parses the CSV and converts it to ICS format.
* GitHub Actions runs daily to update the schedule.

## Usage

Subscribe to the calendar by adding this URL to your calendar app: `https://raw.githubusercontent.com/YOUR_USERNAME/YOUR_REPO/main/lck_schedule.ics`

Replace YOUR_USERNAME and YOUR_REPO with your GitHub username and repository name.

## Customization

To fetch schedules for different tournaments, modify the URL in `main.py` .

For example, change the tournament parameter in the URL.

## Requirements

* Python 3.9+
* Dependencies listed in requirements.txt

## License

MIT
